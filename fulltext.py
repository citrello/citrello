"""
Full texts: the text of each paper, next to its reference, so ChatGPT and Claude can check what a paper says.

Only the text is kept (about 60 KB a paper), never the PDF: people read papers in their browser, and the AI needs
the words, not the file. Texts live in the library folder (Citrello/fulltext/<reference id>.txt), on the person's
computer (in the web app: inside the browser), and the reference notes that it has one ("fulltext": {...}).

Ways in: a PDF dropped on a reference, a whole folder of PDFs matched to their references (by DOI, then title),
the browser button on a paper's page, and open-access copies. Ways out: read_paper / find_in_papers for the AI.
"""
import io
import math
import os
import re
import time
from collections import Counter

import citrello_core as core
import library

MAX_CHARS = 600_000      # a long thesis; anything longer is cut
MIN_CHARS = 1_500        # less than this is an abstract or a cover page, not a paper


def folder():
    return os.path.join(library.HOME, "fulltext")


def _path(item_id):
    return os.path.join(folder(), re.sub(r"[^\w\-]", "_", item_id) + ".txt")


# --------------------------------------------------------------------------
# Getting text out of a PDF
# --------------------------------------------------------------------------

def clean(text):
    """Join words split across lines, drop page furniture, keep paragraphs."""
    text = text.replace("\r", "\n").replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)                    # hyphenated line breaks
    lines = [l.strip() for l in text.split("\n")]
    counts = Counter(l for l in lines if 3 <= len(l) <= 80)
    repeated = {l for l, n in counts.items() if n >= 4 and not re.search(r"[.!?]$", l)}  # running headers/footers
    out, para = [], []
    for l in lines:
        if not l or re.fullmatch(r"\d{1,4}", l) or l in repeated:
            if para and not l:
                out.append(" ".join(para))
                para = []
            continue
        para.append(l)
        if re.search(r"[.!?:]$", l) and len(l) < 60:  # a short line that ends a sentence usually ends a paragraph
            out.append(" ".join(para))
            para = []
    if para:
        out.append(" ".join(para))
    text = "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in out if p.strip())
    return text[:MAX_CHARS]


def clean_page(text):
    """A paper's text as a browser shows it on the publisher's page (innerText): one paragraph per line."""
    drop = re.compile(r"^(download pdf|view pdf|open in new tab|google scholar|crossref|cross ?ref|view article|"
                      r"article google scholar|scopus|pubmed|full size image|view in article|copy|share|cite)$", re.I)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.replace("\r", "\n").split("\n")]
    return "\n\n".join(l for l in lines if len(l) > 2 and not drop.match(l))[:MAX_CHARS]


def pdf_text(data):
    """PDF bytes -> (text, metadata title). Raises ValueError with a plain message."""
    from pypdf import PdfReader
    if not data.startswith(b"%PDF"):
        raise ValueError("That isn't a PDF.")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [p.extract_text() or "" for p in reader.pages]
        title = ((reader.metadata or {}).get("/Title") or "") if reader.metadata else ""
    except Exception as e:
        raise ValueError(f"This PDF couldn't be read ({e.__class__.__name__}).")
    text = clean("\n\n".join(pages))
    if len(text) < MIN_CHARS:
        raise ValueError("No text found in this PDF: it may be a scan (an image of the pages).")
    return text, str(title)


# --------------------------------------------------------------------------
# Matching a paper to its reference
# --------------------------------------------------------------------------

def _words(s):
    return [w for w in core.norm(s).split() if len(w) > 2]


def match(text, items, title_hint=""):
    """The library item this paper's text belongs to, or None. DOI first (exact), then the title near the top."""
    head = text[:6000]
    dois = {core.clean_doi(m.group(1)) for m in core.DOI_RE.finditer(head)}
    by_doi = {core.norm_doi(it["csl"].get("DOI", "")): it for it in items if it["csl"].get("DOI")}
    for d in dois:
        if d and core.norm_doi(d) in by_doi:
            return by_doi[core.norm_doi(d)], "doi"
    head_words = set(_words(head[:3000] + " " + title_hint))
    best, best_score = None, 0.0
    for it in items:
        tw = _words(it["csl"].get("title", ""))
        if len(tw) < 3:
            continue
        score = sum(1 for w in tw if w in head_words) / len(tw)
        if score > best_score:
            best, best_score = it, score
    return (best, "title") if best_score >= 0.85 else (None, None)


def same_paper(it, text):
    """Does this text look like the paper of this reference? Most of its title's words near the start (a PDF
    fetched by the extension could be a different paper: a wrong DOI, another paper's link)."""
    title = [w for w in _words(it["csl"].get("title", "")) if w not in STOP]
    if len(title) < 3:
        return True  # too short a title to tell
    head = set(_words(text[:12000]))
    return sum(1 for w in title if w in head) / len(title) >= 0.75


def first_doi(text):
    m = core.DOI_RE.search(text[:6000])
    return core.clean_doi(m.group(1)) if m else None


# --------------------------------------------------------------------------
# Storing
# --------------------------------------------------------------------------

def attach(key, text, source="pdf"):
    """Store a paper's text for a reference. Returns the reference's full-text summary."""
    text = text[:MAX_CHARS]
    if len(text) < MIN_CHARS:
        raise ValueError("That's too little text for a paper (an abstract or a cover page?).")
    with library.editing() as data:
        it = library.get(data, key)
        if not it:
            raise KeyError(f"No reference {key}")
        os.makedirs(folder(), exist_ok=True)
        with open(_path(it["id"]), "w", encoding="utf-8") as f:
            f.write(text)
        it["fulltext"] = {"words": len(text.split()), "source": source,
                          "added": time.strftime("%Y-%m-%d")}
        return dict(it["fulltext"], key=it["key"])


def remove(key):
    with library.editing() as data:
        it = library.get(data, key)
        if not it:
            raise KeyError(f"No reference {key}")
        it.pop("fulltext", None)
        try:
            os.remove(_path(it["id"]))
        except OSError:
            pass


def read(it):
    """The stored text of a library item, or None."""
    if not it.get("fulltext"):
        return None
    try:
        with open(_path(it["id"]), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def attach_pdf(data, key=None, items=None, check_title=False):
    """A PDF for one reference (key), or matched to the library. Returns {"key", "words", "how"} or
    {"unmatched": True, "doi": ...} when it fits no reference (the caller may add it by its DOI)."""
    text, title = pdf_text(data)
    if key:
        if check_title:
            it = library.get(library.load(), key)
            if it and not same_paper(it, text):
                raise ValueError("This PDF seems to be a different paper (its title isn't in it), so it wasn't kept.")
        return dict(attach(key, text), how="chosen")
    it, how = match(text, items if items is not None else library.load()["items"], title)
    if not it:
        return {"unmatched": True, "doi": first_doi(text), "title_hint": title}
    return dict(attach(it["key"], text), how=how)


# --------------------------------------------------------------------------
# Reading, for the AI: the passages that matter, not 40 pages
# --------------------------------------------------------------------------

def chunks(text, size=900):
    """Paragraph-sized pieces (about `size` characters), with their position."""
    out, cur, start, pos = [], [], 0, 0
    for para in text.split("\n\n"):
        if cur and sum(len(p) for p in cur) + len(para) > size:
            out.append((start, "\n\n".join(cur)))
            cur, start = [], pos
        cur.append(para)
        pos += len(para) + 2
    if cur:
        out.append((start, "\n\n".join(cur)))
    return out


STOP = set("the and for with that this from are was were has have had been their which into than also these those "
           "such can may our its not but all any more most other some per via using used use based between".split())


def passages(text, query, n=4):
    """The n pieces of the paper that best match the query (BM25 ranking on words), in reading order."""
    pieces = chunks(text)
    q = [w for w in _words(query) if w not in STOP]
    if not q or not pieces:
        return []
    docs = [Counter(_words(p)) for _, p in pieces]
    avg = sum(sum(d.values()) for d in docs) / len(docs)
    df = Counter(w for d in docs for w in set(d) if w in q)
    scored = []
    for i, d in enumerate(docs):
        length = sum(d.values()) or 1
        s = 0.0
        for w in set(q):
            if d[w]:
                idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * d[w] * 2.2 / (d[w] + 1.2 * (0.25 + 0.75 * length / avg))
        if s > 0:
            scored.append((s, i))
    best = sorted(i for _, i in sorted(scored, reverse=True)[:n])
    return [{"at": round(100 * pieces[i][0] / max(len(text), 1)), "text": pieces[i][1]} for i in best]


def summary(it):
    ft = it.get("fulltext")
    return dict(ft) if ft else None


# --------------------------------------------------------------------------
# Free (open-access) full texts, for many references at once
# --------------------------------------------------------------------------

UNPAYWALL = os.environ.get("CITRELLO_UNPAYWALL") or "https://api.unpaywall.org/v2/"
EPMC = os.environ.get("CITRELLO_EPMC") or "https://www.ebi.ac.uk/europepmc/webservices/rest/"
CONTACT = "hello@citrello.app"  # Unpaywall asks for a contact address with each request


def _get(url, accept="application/json", timeout=40):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"Citrello/{core.VERSION} ({CONTACT})"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _epmc_text(doi):
    """Europe PMC's open full text (life sciences, medicine): XML -> paragraphs. None when there isn't one."""
    import json as _json
    import urllib.parse
    hits = _json.loads(_get(EPMC + "search?format=json&resultType=lite&query=" + urllib.parse.quote(f'DOI:"{doi}"')))
    rec = next((r for r in hits.get("resultList", {}).get("result", []) if r.get("pmcid") and r.get("isOpenAccess") == "Y"), None)
    if not rec:
        return None
    xml = _get(EPMC + rec["pmcid"] + "/fullTextXML", "application/xml").decode("utf-8", "replace")
    body = xml.split("<body", 1)[1] if "<body" in xml else xml
    paras = [re.sub(r"<[^>]+>", " ", p) for p in re.findall(r"<(?:p|title)[^>]*>(.*?)</(?:p|title)>", body, re.S)]
    text = "\n\n".join(re.sub(r"\s+", " ", p).strip() for p in paras if p.strip())
    return text if len(text) >= MIN_CHARS else None


def _free_pdf(doi):
    """The free PDF's bytes: straight from where Unpaywall says it is (desktop app), or, in the web app, through
    Citrello's bridge, because most repositories don't let web pages download their files (only the DOI is sent)."""
    import json as _json
    import urllib.parse
    if os.environ.get("CITRELLO_WEB"):
        import bridge_client
        try:
            data = _get(bridge_client.BRIDGE + "/api/oa-pdf?doi=" + urllib.parse.quote(doi), "application/pdf", 90)
            return data if data.startswith(b"%PDF") else None
        except Exception:
            return None
    info = _json.loads(_get(UNPAYWALL + urllib.parse.quote(doi) + "?email=" + CONTACT))
    locs = [info.get("best_oa_location")] + (info.get("oa_locations") or [])
    urls = list(dict.fromkeys(l["url_for_pdf"] for l in locs if l and l.get("url_for_pdf")))[:4]
    for u in urls:
        try:
            data = _get(u, "application/pdf", 60)
            if data.startswith(b"%PDF") and len(data) < 40_000_000:
                return data
        except Exception:
            continue
    return None


def find_free(keys=None, progress=None):
    """For references without full text (all, or the given keys) that have a DOI: look for a free copy (Europe PMC,
    then Unpaywall's open-access locations) and keep its text. Returns a summary; never stops over one paper."""
    data = library.load()
    items = [it for it in data["items"] if not it.get("fulltext") and it["csl"].get("DOI")
             and (not keys or it["key"] in keys)]
    found, missing, n = [], [], len(items)
    for i, it in enumerate(items):
        if progress:
            progress(i, n, it["key"])
        doi = it["csl"]["DOI"]
        text, source = None, None
        try:
            text, source = _epmc_text(doi), "open access (Europe PMC)"
        except Exception:
            pass
        if not text:
            try:
                pdf = _free_pdf(doi)
                if pdf:
                    text, source = pdf_text(pdf)[0], "open access"
            except Exception:
                text = None
        if text:
            try:
                attach(it["key"], text, source)
                found.append(it["key"])
                continue
            except (ValueError, KeyError):
                pass
        missing.append(it["key"])
    if progress:
        progress(n, n, "")
    no_doi = sum(1 for it in data["items"] if not it.get("fulltext") and not it["csl"].get("DOI"))
    return {"checked": n, "found": len(found), "found_keys": found, "missing": len(missing), "no_doi": no_doi}
