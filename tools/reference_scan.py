"""Scan recently published open-access papers for references that may not exist.

Picks papers from Europe PMC (open access, full text, published between --from and --to), checks every reference with
Citrello's own reference check (citrello_core: Crossref, doi.org, title search) and lists the papers with references
that "may not exist" (a DOI that doesn't exist or belongs to another paper, and no paper with the title anywhere).
Run by hand from GitHub Actions ("Reference scan"); writes results.jsonl and report.md to --out.

    python tools/reference_scan.py --count 200 --seed 1 --out scan-out
"""
import argparse
import concurrent.futures as cf
import html
import io
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import citrello_core as core  # noqa: E402

UA = "Citrello-reference-scan/1.0 (https://citrello.app)"
TOPICS = ["sport", "exercise", "education", "nursing", "public health", "psychology", "nutrition", "management",
          "environment", "dentistry", "pharmacy", "rehabilitation", "social", "cancer", "diabetes", "covid",
          "surgery", "mental health", "artificial intelligence", "ecology"]
ENGINEERING = ["civil engineering", "mechanical engineering", "electrical engineering", "materials", "energy",
               "construction", "chemical engineering", "environmental engineering", "manufacturing", "machine learning",
               "structural", "transportation", "renewable energy", "concrete", "battery", "robotics", "water treatment",
               "heat transfer", "composites", "power system"]
MAYBE = "may not exist"


def get(url, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(10 * (i + 1))


def candidates(count, seed, date_from, date_to, skip):
    rnd = random.Random(seed)
    topics = TOPICS[:]
    rnd.shuffle(topics)
    per = max(5, count // len(topics) + 2)
    out, seen = [], set(skip)
    for t in topics:
        q = f'"{t}" AND OPEN_ACCESS:y AND HAS_FT:y AND FIRST_PDATE:[{date_from} TO {date_to}]'
        u = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?" + urllib.parse.urlencode(
            {"query": q, "format": "json", "pageSize": 200, "resultType": "lite"})
        try:
            res = [r for r in json.loads(get(u))["resultList"]["result"] if r.get("pmcid")]
        except Exception as e:
            print(f"search failed for {t}: {e}", flush=True)
            continue
        rnd.shuffle(res)
        n = 0
        for r in res:
            if r["pmcid"] in seen or n >= per:
                continue
            seen.add(r["pmcid"])
            n += 1
            out.append({"pmcid": r["pmcid"], "doi": r.get("doi"), "journal": r.get("journalTitle"),
                        "title": r.get("title"), "year": r.get("pubYear"), "topic": t})
    rnd.shuffle(out)
    return out[:count]


def candidates_crossref(count, seed, date_from, date_to, skip, prefix, topics):
    """Recent journal articles whose reference lists are in Crossref (Elsevier's are: prefix 10.1016)."""
    rnd = random.Random(seed)
    topics = topics[:]
    rnd.shuffle(topics)
    per = max(3, count // len(topics) + 2)
    filt = f"from-pub-date:{date_from},until-pub-date:{date_to},has-references:true,type:journal-article"
    if prefix:
        filt += f",prefix:{prefix}"
    out, seen = [], set(skip)
    for t in topics:
        u = "https://api.crossref.org/works?" + urllib.parse.urlencode(
            {"filter": filt, "query": t, "sample": min(100, per * 2), "mailto": "hello@citrello.app",
             "select": "DOI,title,container-title,issued,reference,publisher"})
        try:
            items = json.loads(get(u))["message"]["items"]
        except Exception as e:
            print(f"search failed for {t}: {e}", flush=True)
            continue
        n = 0
        for it in items:
            if it["DOI"] in seen or n >= per or not it.get("reference"):
                continue
            seen.add(it["DOI"])
            n += 1
            out.append({"pmcid": it["DOI"], "doi": it["DOI"], "journal": core._first(it.get("container-title")),
                        "title": core._first(it.get("title")), "publisher": it.get("publisher"),
                        "year": str(((it.get("issued") or {}).get("date-parts") or [[""]])[0][0]), "topic": t,
                        "crossref_refs": it["reference"]})
    rnd.shuffle(out)
    return out[:count]


def crossref_reference_text(r):
    """A reference as Crossref lists it -> the text of a reference, or None when Crossref already matched its DOI
    (a DOI Crossref found itself is a real paper: nothing to check)."""
    if r.get("doi-asserted-by") == "crossref":
        return None
    if r.get("unstructured"):
        t = r["unstructured"]
        return t + (f" doi:{r['DOI']}" if r.get("DOI") and r["DOI"].lower() not in t.lower() else "")
    if not r.get("article-title"):
        return None  # a book or report listed by name only: too little to check
    parts = [f"{r['author']} et al. ({r.get('year', '')})." if r.get("author") else f"({r.get('year', '')}).",
             r["article-title"] + "."]
    src = ", ".join(x for x in (r.get("journal-title") or r.get("series-title") or r.get("volume-title"),
                                r.get("volume"), r.get("first-page")) if x)
    if src:
        parts.append(src + ".")
    if r.get("DOI"):
        parts.append(f"doi:{r['DOI']}")
    return " ".join(parts)


def references(xml):
    refs = []
    for r in re.findall(r"<ref[ >].*?</ref>", xml, re.S):
        r = re.sub(r"<label>.*?</label>", "", r, flags=re.S)
        cits = re.findall(r"<(mixed-citation|element-citation|citation)[ >].*?</\1>", r, re.S)
        if len(cits) > 1:  # the same reference twice (structured and as printed): keep the printed one
            r = next((m.group(0) for m in re.finditer(r"<mixed-citation[ >].*?</mixed-citation>", r, re.S)),
                     re.search(r"<(element-citation|citation)[ >].*?</\1>", r, re.S).group(0))
        r = re.sub(r'<pub-id pub-id-type="doi">(.*?)</pub-id>', r" https://doi.org/\1", r)
        r = re.sub(r"<pub-id[^>]*>.*?</pub-id>", "", r, flags=re.S)
        r = re.sub(r"<ext-link[^>]*>(.*?)</ext-link>", r" \1", r, flags=re.S)
        t = html.unescape(re.sub(r"<[^>]+>", " ", r))
        t = re.sub(r"\s+([.,;:])", r"\1", re.sub(r"\s+", " ", t)).strip()
        if len(t) > 20:
            refs.append(t)
    return refs


class _ThreadLog(io.TextIOBase):
    """stderr per thread, so each paper counts its own failed lookups when several are checked at once."""
    local = threading.local()

    def write(self, s):
        buf = getattr(self.local, "buf", None)
        (buf if buf is not None else sys.__stderr__).write(s)
        return len(s)


sys.stderr = _ThreadLog()


def scan(paper, email):
    if "crossref_refs" in paper:  # the reference list came with the Crossref record
        listed = paper.pop("crossref_refs")
        refs = [t for t in map(crossref_reference_text, listed) if t]
        paper = dict(paper, listed=len(listed), matched_by_crossref=sum(r.get("doi-asserted-by") == "crossref"
                                                                        for r in listed))
        if len(listed) < 8 or not refs:
            return dict(paper, error=f"{len(listed)} references, {len(refs)} to check (skipped)")
    else:
        try:
            refs = references(get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{paper['pmcid']}/fullTextXML"))
        except Exception as e:
            return dict(paper, error=f"full text: {e}")
        if not 8 <= len(refs) <= 150:
            return dict(paper, error=f"{len(refs)} references (skipped)")
    log = _ThreadLog.local.buf = io.StringIO()
    try:
        recs = [core.resolve_reference(i + 1, t, email=email) for i, t in enumerate(refs)]
        core.assess_references(recs)
    finally:
        _ThreadLog.local.buf = None
    failed = log.getvalue().count("Crossref lookup failed")
    openalex_failed = log.getvalue().count("OpenAlex lookup failed")
    maybe = [{"n": r["number"], "ref": r["raw"],
              "why": " ".join(c["text"] for c in r["checks"] if MAYBE in c["text"])} for r in recs
             if any(MAYBE in c["text"] for c in r["checks"])]
    counts = {k: sum(r["reliability"] == k for r in recs) for k in ("verified", "check", "problem")}
    return dict(paper, refs=len(refs), counts=counts, failed_lookups=failed, openalex_failed=openalex_failed,
                maybe_nonexistent=maybe)


def safe_scan(paper, email):
    """One paper's check; an error in it is reported for that paper and the scan goes on."""
    try:
        return scan(dict(paper), email)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc(file=sys.__stderr__)
        paper = {k: v for k, v in paper.items() if k != "crossref_refs"}
        return dict(paper, error=f"check failed: {type(e).__name__}: {e}")


def report(results, args):
    checked = [r for r in results if "error" not in r]
    done = [r for r in checked if r["failed_lookups"] <= 2]
    # a paper whose other lookups failed still counts when 2+ references were found to be missing
    flagged = sorted([r for r in checked if len(r["maybe_nonexistent"]) >= 2], key=lambda r: -len(r["maybe_nonexistent"]))
    one = [r for r in done if len(r["maybe_nonexistent"]) == 1]
    lines = [f"# Reference scan (seed {args.seed}, papers published {args.date_from} to {args.date_to})", "",
             f"- Papers checked fully: **{len(done)}** of {len(results)} picked",
             f"- References checked: {sum(r['refs'] for r in done)}",
             f"- Papers with 2 or more references that may not exist: **{len(flagged)}**",
             f"- Papers with exactly 1 (often a wrong DOI of a book or report; not counted): {len(one)}", ""]
    for r in flagged:
        link = (f"https://europepmc.org/article/PMC/{r['pmcid']}" if str(r["pmcid"]).startswith("PMC")
                else f"https://doi.org/{r['doi']}")
        failed = f" ({r['failed_lookups']} lookups failed)" if r["failed_lookups"] > 2 else ""
        lines += [f"## {len(r['maybe_nonexistent'])} of {r.get('listed', r['refs'])}: {r['title']}{failed}",
                  f"{r['journal']} ({r['year']}) · {r.get('publisher') or ''} · DOI {r['doi']} · {link}", ""]
        lines += [f"{m['n']}. {m['ref']}  \n   → {m['why']}" for m in r["maybe_nonexistent"]] + [""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--from", dest="date_from", default="2026-06-01")
    ap.add_argument("--to", dest="date_to", default="2026-11-15")
    ap.add_argument("--email", default="hello@citrello.app", help="contact for Crossref's polite pool")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--skip", default="", help="file with PMC ids already scanned (one per line)")
    ap.add_argument("--out", default="scan-out")
    ap.add_argument("--source", default="europepmc", choices=["europepmc", "crossref"],
                    help="europepmc: open-access full texts (mostly medicine); crossref: reference lists in Crossref")
    ap.add_argument("--prefix", default="", help="crossref only: DOI prefix of a publisher (Elsevier: 10.1016)")
    ap.add_argument("--topics", default="", help="engineering, or topics separated by commas (default: the medical list)")
    ap.add_argument("--pmcids", default="", help="check these papers (PMC ids, comma separated) instead of a sample")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    skip = open(args.skip).read().split() if args.skip and os.path.exists(args.skip) else []
    ids = [x.strip() for x in args.pmcids.split(",") if x.strip()]
    topics = (ENGINEERING if args.topics == "engineering" else
              [t.strip() for t in args.topics.split(",") if t.strip()] or TOPICS)
    papers = ([{"pmcid": i, "doi": None, "journal": "", "title": i, "year": "", "topic": "chosen"} for i in ids]
              if ids else
              candidates_crossref(args.count, args.seed, args.date_from, args.date_to, skip, args.prefix, topics)
              if args.source == "crossref" else candidates(args.count, args.seed, args.date_from, args.date_to, skip))
    print(f"{len(papers)} papers picked", flush=True)
    results = []
    with open(os.path.join(args.out, "results.jsonl"), "w") as f, cf.ThreadPoolExecutor(args.workers) as ex:
        for r in ex.map(lambda p: safe_scan(p, args.email), papers):
            results.append(r)
            f.write(json.dumps(r) + "\n")
            f.flush()
            print(r["pmcid"], r.get("error") or f"{r['refs']} refs, {len(r['maybe_nonexistent'])} may not exist, "
                  f"{r['failed_lookups']} failed lookups", flush=True)
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write(report(results, args))
    with open(os.path.join(args.out, "scanned.txt"), "w") as f:
        f.write("\n".join(skip + [p["pmcid"] for p in papers]))
    print(open(os.path.join(args.out, "report.md")).read().split("\n## ")[0])


if __name__ == "__main__":
    main()
