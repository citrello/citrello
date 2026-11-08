#!/usr/bin/env python3
"""
citrello - turn a Word article with hand-typed numbered citations into a
Mendeley-managed document.

  extract   Read the bibliography of a .docx, resolve every entry to structured
            metadata (Crossref when online, local parser otherwise) and write
            refs.json (editable), refs.ris and refs.bib (import into Mendeley).
  auth      One-time OAuth login to your Mendeley account (official API).
  push      Add the references in refs.json to your Mendeley library and store
            each document's Mendeley ID back in refs.json.
  convert   Replace typed citations ([1], [2-4], [1, 3], superscript 1,2 ...)
            with Mendeley citation fields and turn the reference list into a
            Mendeley bibliography field.
  all       extract + convert in one go (add --push to also push).

Requires: python-docx  (pip install python-docx)
"""
import argparse
import base64
import copy
import difflib
import http.server
import io
import json
import os
import re
import secrets
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser

try:
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
except ImportError:  # pragma: no cover
    sys.exit("python-docx is required:  pip install python-docx")


# The Mac app's bundled Python has no system certificates: use certifi's for HTTPS (Crossref, doi.org...).
if sys.platform == "darwin" and getattr(sys, "frozen", False) and not os.environ.get("SSL_CERT_FILE"):
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass

VERSION = "1.0.1"  # the one place the version is set

USER_AGENT = "Citrello/" + VERSION + " (https://citrello.app; mailto:{email})"
CSL_KEEP = {"type", "title", "author", "editor", "container-title", "issued", "volume",
            "issue", "page", "DOI", "URL", "publisher", "publisher-place", "ISSN",
            "ISBN", "edition", "collection-title", "number", "genre"}

# --------------------------------------------------------------------------
# Locating the bibliography
# --------------------------------------------------------------------------

BIB_HEADING_RE = re.compile(
    r"^\s*(\d+\.?\s*)?(references?|bibliography|literature cited|works cited|"
    r"reference list|sources|cited literature)\s*:?\s*$", re.I)
NUM_PREFIX_RE = re.compile(r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d+)[\.\)](?!\d))\s*")


def is_heading(p):
    style = (p.style.name or "").lower() if p.style is not None else ""
    return style.startswith("heading") or style == "title"


def has_numbering(p):
    ppr = p._p.pPr
    return ppr is not None and ppr.numPr is not None


def body_paragraphs(document):
    """Every paragraph of the main text in order, including those inside content controls (Mendeley Cite and
    Word's own bibliography keep the reference list in one) - python-docx's document.paragraphs skips them."""
    from docx.text.paragraph import Paragraph
    out = []
    for p in document.element.body.iter(qn("w:p")):
        anc = p.getparent()
        skip = False
        while anc is not None:  # a text box's fallback copy would count twice
            if anc.tag.endswith("}Fallback") or anc.tag == qn("w:txbxContent"):
                skip = True
                break
            anc = anc.getparent()
        if not skip:
            out.append(Paragraph(p, document._body))
    return out


def find_bibliography(document):
    """Return (heading_paragraph, [(number, paragraph, text), ...])."""
    paras = body_paragraphs(document)
    start = None
    for i in range(len(paras) - 1, -1, -1):  # last matching heading wins
        if BIB_HEADING_RE.match(paras[i].text):
            start = i
            break
    if start is None:
        raise SystemExit("Could not find a 'References' / 'Bibliography' heading.")
    entries = []
    seq = 0
    for p in paras[start + 1:]:
        text = p.text.strip()
        if not text:
            continue
        if is_heading(p) and not NUM_PREFIX_RE.match(text):
            break  # next section (e.g. Appendix) starts
        m = NUM_PREFIX_RE.match(text)
        if m:
            num = int(next(g for g in m.groups() if g))
            text = text[m.end():].strip()
        elif entries and not has_numbering(p) and len(text) < 40:
            break
        else:
            num = seq + 1
        seq = num
        entries.append((num, p, text))
    if not entries:
        raise SystemExit("Found the references heading but no entries below it.")
    return paras[start], entries


# --------------------------------------------------------------------------
# Parsing a reference string (offline fallback)
# --------------------------------------------------------------------------

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
URL_RE = re.compile(r"https?://\S+")
YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})[a-z]?\b")
INITIAL_RE = re.compile(r"^(?:[A-Z]\.?-?){1,4}$|^[A-Z][a-z]?\.$")


def _clean(s):
    return re.sub(r"\s+", " ", s or "").strip(" .,;:")


def parse_name(raw):
    raw = _clean(raw)
    raw = re.sub(r"^(and|&)\s+", "", raw)
    if not raw or raw.lower() in ("et al", "et al."):
        return None
    if "," in raw:  # "Smith, J. A."
        fam, giv = raw.split(",", 1)
        return {"family": _clean(fam), "given": _clean(giv)}
    toks = raw.split()
    if len(toks) == 1:
        return {"family": toks[0], "given": ""}
    if INITIAL_RE.match(toks[0]):  # "J. A. Smith"
        i = 0
        while i < len(toks) - 1 and INITIAL_RE.match(toks[i]):
            i += 1
        return {"family": " ".join(toks[i:]), "given": " ".join(toks[:i])}
    if re.fullmatch(r"[A-Z]{1,3}", toks[-1]):  # Vancouver "Smith JA"
        return {"family": " ".join(toks[:-1]), "given": toks[-1]}
    return {"family": toks[-1], "given": " ".join(toks[:-1])}


def parse_authors(seg, style):
    seg = re.sub(r"\bet al\.?", "", seg)
    if style == "apa":  # Harvard/APA: "Smith, J.A., Lee, K. and Liv Stranddorf"
        toks = [x.strip() for x in re.split(r",|;|\s+and\s+|\s*&\s*", seg) if x.strip(" .")]
        # "J.", "D.-C.", "A. M.", "D. K.", "M.Jr.", "JA" - but not short surnames like "Hu" or "Li"
        initials = re.compile(r"(?:[A-Z][a-z]?\.\s*-?\s*)+(?:[A-Z]\.?)?(?:\s*(?:Jr|Sr)\.?)?|[A-Z]{1,3}")
        if not any(initials.fullmatch(x) for x in toks):
            seg = _clean(seg)  # no initials at all: an organisation or a single full name
            if len(seg.split()) <= 3 and not re.search(r",|&|\band\b", seg):
                return [parse_name(seg)]
            return [{"literal": seg, "family": ""}] if seg else []
        names, i = [], 0
        while i < len(toks):
            if i + 1 < len(toks) and initials.fullmatch(toks[i + 1]) and not initials.fullmatch(toks[i]):
                names.append({"family": _clean(toks[i]), "given": _clean(toks[i + 1])})
                i += 2
            else:
                names.append(parse_name(toks[i]))
                i += 1
    else:
        parts = re.split(r",\s*(?:and\s+|&\s*)?|\s+and\s+|\s*&\s*|;\s*", seg)
        names = [parse_name(p) for p in parts]
    return [n for n in names if n and (n.get("family") or n.get("literal"))]


def parse_reference(text):
    item = {"type": "article-journal"}
    t = text.strip()
    m = DOI_RE.search(t)
    if m:
        item["DOI"] = clean_doi(m.group(1))
        t = (t[:m.start()] + t[m.end():])
    t = re.sub(r"\b(doi|DOI)\s*:?\s*$", "", t.strip())
    m = URL_RE.search(t)
    if m:
        item["URL"] = m.group(0).rstrip(".,;)")
        t = t[:m.start()] + t[m.end():]
    t = re.sub(r"\[\s*(?:online|accessed[^\]]*|viewed[^\]]*|cited[^\]]*)\]\.?", " ", t, flags=re.I)
    t = re.sub(r"\(\s*accessed[^)]*\)\.?", " ", t, flags=re.I)
    t = re.sub(r"\b(Available (at|from)|Retrieved from|\[Online\]\.?|Accessed[^.]*)\s*:?", "", t)
    t = _clean(t)

    vip = None
    m = re.search(r"vol\.\s*(\w+)(?:,\s*no\.\s*(\w+))?(?:,\s*pp?\.\s*([\w–\-]+))?", t)
    if m:
        vip = m.groups()
    else:
        m = re.search(r"(\d+)\s*\(([\w\-–/ ]+)\)\s*[:,]\s*([A-Za-z]?\d+(?:\s*[–\-]\s*[A-Za-z]?\d+)?)", t) \
            or re.search(r";\s*(\d+)\s*:\s*([A-Za-z]?\d+(?:[–\-][A-Za-z]?\d+)?)", t) \
            or re.search(r",\s*(\d+),\s*(?:pp?\.\s*)?([A-Za-z]?\d+\s*[–\-]\s*[A-Za-z]?\d+)", t)
        if m:
            g = m.groups()
            vip = (g[0], g[1], g[2]) if len(g) == 3 else (g[0], None, g[1])
    if not vip:
        m = re.search(r"[,\]]\s*(\d{1,4})\s*\((\d[\w\-–/]*)\)\s*[,.]?\s*$", t)
        if m:
            vip = (m.group(1), m.group(2), None)
    if vip:
        if vip[0]:
            item["volume"] = vip[0]
        if vip[1]:
            item["issue"] = vip[1].strip()
        if vip[2]:
            item["page"] = vip[2].replace("–", "-").replace(" ", "")

    q = re.search(r"[\"“‘](.+?)[,.]?[\"”’](?=[\s,.(]|$)", t)
    apa = re.match(r"^([^\d\"“‘]+?)\s*\(((?:1[6-9]|20)\d{2}|n\.\s?d\.?)[a-z]?(?:,[^)]*)?\)\.?\s*(.+)$", t)
    law = re.match(r"^([A-Z][\w ,'’&()-]*?\b(?:Act|Regulations?|Directive|Order)\s+((?:1[6-9]|20)\d{2}))\b", t)
    if law and not apa:  # legislation: "Energy Act 2004, c. 20, Part 2"
        item.update(type="legislation", title=_clean(law.group(1)),
                    issued={"date-parts": [[int(law.group(2))]]})
        rest = _clean(t[law.end():])
        if rest:
            item["number"] = rest[:80]
        return item
    if apa:  # Harvard/APA: Authors (Year). Title. Journal, vol(issue), pages.  |  ... (Year) 'Title', Journal
        item["author"] = parse_authors(apa.group(1), "apa")
        if apa.group(2)[0].isdigit():
            item["issued"] = {"date-parts": [[int(apa.group(2))]]}
        rest = apa.group(3)
        qt = re.match(r"^[\"“‘](.+?)[,.]?[\"”’](?=[\s,.(]|$)\s*,?\s*(.*)$", rest)
        if qt:
            item["title"] = _clean(qt.group(1))
            cont = re.split(r",\s*(?:\[online\]\s*)?\d|\.\s|\bAvailable\b", qt.group(2), maxsplit=1)[0]
        else:
            parts = re.split(r"(?<=[a-z0-9\)\]?!])\.\s+", rest, maxsplit=1)
            item["title"] = _clean(parts[0])
            cont = re.split(r",\s*(?:\[online\]\s*)?\d|\.\s|\bAvailable\b", parts[1], maxsplit=1)[0] if len(parts) > 1 else ""
        cont = _clean(re.sub(r"\[online\]", "", cont))
        if cont:
            item["container-title"] = cont
    elif q:  # IEEE / ACS style: authors, "Title," Journal, ...   (or a reference that starts with its title)
        item["author"] = parse_authors(t[:q.start()], "ieee")
        item["title"] = _clean(q.group(1))
        rest = t[q.end():]
        rest = re.sub(r"^[\s,]*in\s+", "", rest)
        ty = re.match(r"^[\s,]*\(((?:1[6-9]|20)\d{2})[a-z]?\)\s*", rest)  # 'Title' (2025) Journal, ...
        if ty:
            item["issued"] = {"date-parts": [[int(ty.group(1))]]}
            rest = rest[ty.end():]
        # Optica/AIP style: Journal 13, 5721 (2020)  |  Journal (2017)
        op = re.match(r"^\s*(.+?)(?:\s+(\d+[A-Za-z]?)(?:,\s*([A-Za-z]?\d+(?:\s*[–\-]\s*[A-Za-z]?\d+)?))?)?"
                      r"\s*\([^)]*?((?:1[6-9]|20)\d{2})\)", rest.strip(" ,"))
        if op:
            cont = op.group(1)
            if op.group(2):
                item["volume"] = op.group(2)
            if op.group(3):
                item["page"] = op.group(3).replace("–", "-").replace(" ", "")
            tr = re.match(r"^(.+?)\s+Tech(?:nical)?\.?\s+Rep(?:ort)?\.?$", _clean(cont))
            if tr:
                item["type"], item["publisher"] = "report", tr.group(1)
                if item.pop("volume", None):
                    item["number"] = op.group(2)
                cont = ""
        else:
            cont = re.split(r",|\bvol\.", rest.strip(" ,"), maxsplit=1)[0]
        if _clean(cont):  # keep the final "." of abbreviated journal names
            item["container-title"] = re.sub(r"\s+", " ", cont).strip(" ,;:")
    elif re.match(r"^(.+?),\s+([^,]+?)\s*\(([^()]+),\s*((?:1[6-9]|20)\d{2})\)", t):
        bk = re.match(r"^(.+?),\s+([^,]+?)\s*\(([^()]+),\s*((?:1[6-9]|20)\d{2})\)", t)
        item.update(type="book", title=_clean(bk.group(2)), publisher=_clean(bk.group(3)),
                    issued={"date-parts": [[int(bk.group(4))]]})
        item["author"] = parse_authors(bk.group(1), "ieee")
    else:  # Vancouver: Authors. Title. Journal. Year;Vol(Issue):Pages.
        parts = re.split(r"(?<=[a-z0-9\)\]A-Z?!])\.\s+", t)
        if len(parts) >= 2:
            seg = parts[0]
            if "," not in seg and not re.search(r"\b[A-Z]{1,3}\b", seg) and len(seg.split()) > 1:
                item["author"] = [{"literal": _clean(seg)}]  # organisation as author
            else:
                item["author"] = parse_authors(seg, "vancouver")
            item["title"] = _clean(parts[1])
            if len(parts) >= 3:
                cont = re.split(r"[;,]?\s*(1[6-9]|20)\d{2}", parts[2])[0]
                pub = re.match(r"^([^:]{2,40}):\s*(.+)$", _clean(cont))
                if pub and "volume" not in item:
                    item["publisher-place"], item["publisher"] = pub.group(1), pub.group(2)
                    item["type"] = "book"
                else:
                    item["container-title"] = _clean(cont)
        else:
            item["title"] = t

    if "issued" not in item and not (apa and not apa.group(2)[0].isdigit()):  # keep "n.d." undated
        years = YEAR_RE.findall(t)
        if years:
            item["issued"] = {"date-parts": [[int(years[-1])]]}
    lowered = text.lower()
    if item.get("type") in ("report", "book"):
        pass
    elif re.search(r"\b(proc\.(?!\s*natl)|proceedings\b(?! of the national)|conference\b|symposium\b|workshop\b)", lowered):
        item["type"] = "paper-conference"
    elif re.search(r"\b(thesis|dissertation)\b", lowered):
        item["type"] = "thesis"
    elif item.get("type") == "book" or (re.search(r"\b(eds?\.|edition\b|press\b|publisher\b)", lowered)
                                        and "container-title" not in item):
        item["type"] = "book"
    elif "volume" not in item and "DOI" not in item and "container-title" not in item and (
            "URL" in item or re.search(r"\[online\]|\baccessed\b", lowered)):
        item["type"] = "webpage"
    return {k: v for k, v in item.items() if v}


# --------------------------------------------------------------------------
# Crossref enrichment
# --------------------------------------------------------------------------

def http_json(url, headers=None, data=None, method=None, timeout=30):
    headers = dict(headers or {})
    # some API firewalls (Mendeley's included) reject Python's default User-Agent
    headers.setdefault("User-Agent", "citrello/1.0 (reference converter)")
    headers.setdefault("Accept", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                # in the web app (pyodide-http), a 404 or 500 comes back as an ordinary answer: make it the same
                # error as in Python ("not found" must stay different from a broken answer)
                status = getattr(r, "status", None) or (r.getcode() if hasattr(r, "getcode") else None) or 200
                if status >= 400:
                    raise urllib.error.HTTPError(url, status, f"HTTP {status}", r.headers, io.BytesIO(body))
                return json.loads(body) if body else None, r.headers
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:  # "too many requests": wait as asked (a little), then once more
                try:
                    wait = min(float(e.headers.get("Retry-After") or 2), 10)
                except (TypeError, ValueError, AttributeError):
                    wait = 2
                time.sleep(wait)
                continue
            raise  # the server answered (404...): not a connection problem
        except Exception as e:
            # a dropped connection or a timeout, or in the web app a browser "NetworkError": try once more, then
            # report it as "no connection" (callers tell that apart from "not found")
            browser = "NetworkError" in str(e) or e.__class__.__name__ == "JsException"
            refused = isinstance(e, urllib.error.URLError) and isinstance(getattr(e, "reason", None), ConnectionRefusedError)
            if attempt == 1 and (browser or isinstance(e, TimeoutError) or
                                 (isinstance(e, (urllib.error.URLError, OSError)) and not refused)):
                time.sleep(1.5)
                continue
            if browser:
                raise urllib.error.URLError(str(e)) from e
            raise


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def trim_csl(csl):
    out = {k: v for k, v in csl.items() if k in CSL_KEEP and v}
    for k, v in list(out.items()):  # other tools send numbers for volume, issue, page...: keep text
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = str(v)
    for k in ("container-title", "title", "ISSN", "ISBN"):
        if isinstance(out.get(k), list):
            out[k] = out[k][0] if out[k] else ""
    for k in ("author", "editor"):
        if k in out:
            out[k] = [{x: str(a[x]) for x in ("family", "given", "literal") if a.get(x)} for a in out[k]
                      if isinstance(a, dict)]
    if "issued" in out:
        iss = out["issued"] if isinstance(out["issued"], dict) else {}
        parts = iss.get("date-parts") or [[None]]
        if not parts[0] or parts[0] == [None]:
            y = re.search(r"\b(1[5-9]|20)\d{2}\b", str(iss.get("raw") or iss.get("literal") or ""))
            parts = [[int(y.group(0))]] if y else [[None]]
        out["issued"] = {"date-parts": [[int(p) if str(p).isdigit() else p for p in parts[0][:3]]]}
    return {k: v for k, v in out.items() if v}


CROSSREF = os.environ.get("CITRELLO_CROSSREF", "https://api.crossref.org")
CR_TYPES = {"journal-article": "article-journal", "proceedings-article": "paper-conference",
            "book-chapter": "chapter", "book": "book", "monograph": "book", "edited-book": "book",
            "reference-book": "book", "posted-content": "article", "report": "report",
            "dataset": "dataset", "dissertation": "thesis", "standard": "standard"}


def _first(v):
    return (v[0] if v else "") if isinstance(v, list) else (v or "")


def crossref_to_csl(m):
    """Crossref /works record -> CSL-JSON (the subset Mendeley uses)."""
    people = lambda lst: [{"family": p["family"], "given": p.get("given", "")} if p.get("family")
                          else {"literal": p.get("name", "")} for p in lst or []]
    date = next((m[k]["date-parts"] for k in ("issued", "published-print", "published-online")
                 if m.get(k, {}).get("date-parts", [[None]])[0][0]), None)
    csl = {"type": CR_TYPES.get(m.get("type"), "article"), "title": _first(m.get("title")),
           "container-title": _first(m.get("container-title")), "DOI": m.get("DOI"), "URL": m.get("URL"),
           "volume": m.get("volume"), "issue": m.get("issue"), "page": m.get("page") or m.get("article-number"),
           "publisher": m.get("publisher"), "ISSN": _first(m.get("ISSN")), "ISBN": _first(m.get("ISBN")),
           "author": people(m.get("author")), "editor": people(m.get("editor"))}
    if date:
        csl["issued"] = {"date-parts": date}
    return trim_csl(csl)


def crossref_meta(m):
    """Facts about the online record used by the reliability check."""
    updates = [u.get("type", "") for u in m.get("updated-by") or []]
    title = _first(m.get("title"))
    return {"retracted": any("retraction" in u for u in updates) or bool(re.match(r"^\s*retracted\b", title, re.I)),
            "is_notice": any("retraction" in u.get("type", "") for u in m.get("update-to") or [])
                         or bool(re.match(r"^\s*(retraction|withdrawal)( notice)?\s*[:\-]", title, re.I)),
            "preprint": m.get("type") == "posted-content" or m.get("subtype") == "preprint",
            "corrected": any(u in ("correction", "erratum", "corrigendum") for u in updates)}


DOI_ORG = os.environ.get("CITRELLO_DOIORG", "https://doi.org")


def _doi_org_csl(doi, headers):
    """Metadata for a DOI from any registration agency (Crossref, DataCite, mEDRA...) via doi.org.
    Returns (csl, status) with status 'found', 'missing' (DOI does not exist) or 'unknown'."""
    h = dict(headers, Accept="application/vnd.citationstyles.csl+json")
    try:
        csl, _ = http_json(DOI_ORG + "/" + urllib.parse.quote(doi, safe="/"), h)
        return (trim_csl(csl), "found") if isinstance(csl, dict) and csl.get("title") else (None, "unknown")
    except urllib.error.HTTPError as e:
        return None, "missing" if e.code == 404 else "unknown"


def crossref_lookup(raw, parsed, email):
    """Return (csl, source, meta). source: crossref-doi | doi-org | crossref-search | None."""
    headers = {"User-Agent": USER_AGENT.format(email=email or "anonymous@example.com")}
    typed_doi = parsed.get("DOI")
    meta_extra, doi_missing = {}, False
    try:
        if typed_doi:
            cands = doi_candidates(typed_doi)
            for cand in cands:
                try:
                    res, _ = http_json(CROSSREF + "/works/" + urllib.parse.quote(cand), headers)
                except urllib.error.HTTPError as e:
                    if e.code != 404:
                        raise
                    continue
                m = res["message"]
                meta = crossref_meta(m)
                if cand != norm_doi(typed_doi):
                    meta["doi_cleaned"] = (typed_doi, cand)
                return crossref_to_csl(m), "crossref-doi", meta
            statuses = []
            for cand in cands:  # not a Crossref DOI: try every agency through doi.org
                csl, status = _doi_org_csl(cand, headers)
                statuses.append(status)
                if csl:
                    meta = {"agency": "doi.org"}
                    if cand != norm_doi(typed_doi):
                        meta["doi_cleaned"] = (typed_doi, cand)
                    return csl, "doi-org", meta
            doi_missing = bool(statuses) and all(st == "missing" for st in statuses)
            meta_extra = {"doi_not_found": typed_doi} if doi_missing else {"doi_unchecked": typed_doi}
        q = urllib.parse.urlencode({"query.bibliographic": raw[:500], "rows": 1})
        res, _ = http_json(CROSSREF + "/works?" + q, headers)
        items = res["message"]["items"]
        if not items:
            return None, None, meta_extra
        m = items[0]
        title = _first(m.get("title"))
        # accept only when the found title really occurs in the reference text
        ratio = difflib.SequenceMatcher(None, norm(title), norm(parsed.get("title", raw))).ratio()
        if norm(title) and (norm(title) in norm(raw) or ratio > 0.85):
            meta = crossref_meta(m)
            if typed_doi:
                meta["doi_replaced"] = (typed_doi, m.get("DOI"))
            return crossref_to_csl(m), "crossref-search", meta
        return None, None, meta_extra
    except (urllib.error.URLError, TimeoutError, OSError) as e:  # no connection: not the same as "not found"
        print(f"    Crossref lookup failed: {e}", file=sys.stderr)
        return None, None, {"error": f"No connection to Crossref ({e})"}
    except (KeyError, ValueError) as e:
        print(f"    Crossref lookup failed: {e}", file=sys.stderr)
    return None, None, meta_extra


def resolve_reference(num, text, offline=False, email=None):
    """One bibliography entry -> reference record (metadata + what we know about its reliability)."""
    parsed = parse_reference(text)
    csl, source, meta = (None, None, {}) if offline else crossref_lookup(text, parsed, email)
    if not csl:
        csl, source = parsed, "parsed"
    return {"number": num, "raw": text, "source": source, "csl": csl, "typed": parsed, "meta": meta}


# --------------------------------------------------------------------------
# Reliability check
# --------------------------------------------------------------------------

PREPRINT_RE = re.compile(r"\b(arxiv|biorxiv|medrxiv|chemrxiv|ssrn|research square|preprints?\.org|"
                         r"techrxiv|osf preprints|preprint)\b", re.I)


def _words(s):
    return {w for w in norm(s).split() if len(w) > 3}


def assess_references(refs):
    """Add r["checks"] = [{"level": "problem"|"check", "text": ...}] and r["reliability"] to each reference."""
    seen = {}
    for r in refs:
        c, typed, meta, raw = r["csl"], r.get("typed") or {}, r.get("meta") or {}, r["raw"]
        checks = []
        add = lambda level, text: checks.append({"level": level, "text": text})
        online = r["source"] in ("crossref-doi", "crossref-search", "doi-org")
        is_law = c.get("type") == "legislation"

        if meta.get("retracted"):
            add("problem", "This paper has been RETRACTED")
        if meta.get("is_notice"):
            add("problem", "The DOI points to a retraction/withdrawal notice, not the paper")
        if meta.get("doi_not_found") and not meta.get("doi_replaced"):
            add("problem", f"The DOI {meta['doi_not_found']} does not exist (checked at doi.org): typo?")
        if meta.get("doi_replaced"):
            old_doi, new_doi = meta["doi_replaced"]
            add("check", f"The DOI {old_doi} was not found; the paper was found by its title with DOI {new_doi}")
        if meta.get("doi_unchecked"):
            add("check", f"The DOI {meta['doi_unchecked']} could not be checked (not in Crossref; doi.org gave no details)")
        if online:
            rec_words, raw_words = _words(c.get("title", "")), _words(raw)
            overlap = len(rec_words & raw_words) / len(rec_words) if rec_words else 1
            fam = norm((c.get("author") or [{}])[0].get("family", ""))
            author_ok = not fam or f" {fam.split()[-1]} " in f" {norm(raw)} "
            typed_ref = bool(typed.get("title"))  # nothing to compare when only a DOI was given
            if not typed_ref:
                pass
            elif r["source"] == "crossref-doi" and overlap < 0.5 and not author_ok:
                add("problem", f"The DOI seems to point to a different paper: “{c.get('title', '')[:90]}”. "
                               "Your typed details are used instead; please correct the DOI")
                r["csl"] = c = dict(typed)  # never import the wrong paper into Mendeley
            elif overlap < 0.5:
                add("check", f"Title differs from the online record: “{c.get('title', '')[:90]}”")
            def fams(lst):
                return [norm(a.get("family") or a.get("literal", "")).split()[-1:] for a in lst or []]
            tf, cf = [f[0] for f in fams(typed.get("author")) if f], [f[0] for f in fams(c.get("author")) if f]
            if typed_ref and tf and cf and tf[0] not in cf and cf[0] not in tf:
                add("check", f"Authors differ from the publisher record: you had {', '.join(tf[:3])}; "
                             f"the record says {', '.join(cf[:3])}")
            ty, cy = year_of(typed), year_of(c)
            if ty and cy and ty != cy:
                add("check", f"Year differs: you wrote {ty}, the publisher record says {cy}"
                             + (" (often online vs print date)" if abs(int(ty) - int(cy)) == 1 else ""))
            if r["source"] == "crossref-search":
                add("check", "Found online by title search, not by DOI. Confirm it is the right paper"
                             + (f" (DOI {c['DOI']})" if c.get("DOI") else ""))
            if meta.get("corrected"):
                add("check", "A correction/erratum has been published for this paper")
        elif r["source"] == "parsed":
            add("check", "Not found online; the details were read from your text")
            if not c.get("title") or (not c.get("author") and not year_of(c)) and not is_law:
                add("problem", "Key details (title, author or year) could not be read")
        if not c.get("DOI") and not is_law:
            add("check", "No DOI")
        if meta.get("preprint") or PREPRINT_RE.search(raw) or PREPRINT_RE.search(c.get("container-title", "")):
            add("check", "Preprint: may not be peer-reviewed")
        if c.get("type") == "webpage" or (not c.get("DOI") and re.search(r"\[online\]|https?://", raw, re.I)
                                          and not c.get("volume") and not is_law):
            add("check", "Web page / grey literature: no formal publication record")
        if not year_of(c) and not is_law:
            add("check", "No date (n.d.)")
        if not c.get("author") and not is_law and c.get("title"):
            add("check", "No author")
        key = norm_doi(c.get("DOI")) or norm(c.get("title", ""))[:80]
        if key and key in seen:
            add("check", f"Listed twice: same as reference {seen[key]}")
        elif key:
            seen[key] = r["number"]
        # de-duplicate messages, keep order
        uniq = []
        for ch in checks:
            if ch not in uniq:
                uniq.append(ch)
        r["checks"] = uniq
        r["reliability"] = ("problem" if any(ch["level"] == "problem" for ch in uniq)
                            else "check" if uniq else "verified" if online else "complete")
    return refs


def write_check_csv(refs, path):
    import csv
    label = {"verified": "Verified", "check": "Check", "problem": "Problem", "complete": "Complete"}
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig so Excel shows accents
        w = csv.writer(f)
        w.writerow(["#", "Reliability", "Issues", "Reference as typed", "DOI", "Title found"])
        for r in refs:
            w.writerow([r["number"], label[r.get("reliability", "check")],
                        "; ".join(ch["text"] for ch in r.get("checks", [])), r["raw"],
                        r["csl"].get("DOI", ""), r["csl"].get("title", "")])


# --------------------------------------------------------------------------
# Export: RIS / BibTeX
# --------------------------------------------------------------------------

RIS_TYPES = {"article-journal": "JOUR", "book": "BOOK", "chapter": "CHAP",
             "paper-conference": "CPAPER", "thesis": "THES", "report": "RPRT",
             "webpage": "ELEC", "dataset": "DATA"}
BIB_TYPES = {"article-journal": "article", "book": "book", "chapter": "incollection",
             "paper-conference": "inproceedings", "thesis": "phdthesis",
             "report": "techreport", "webpage": "misc"}


def year_of(item):
    try:
        return str(item["issued"]["date-parts"][0][0] or "")
    except (KeyError, IndexError, TypeError):
        return ""


def write_ris(refs, path):
    lines = []
    for r in refs:
        c = r["csl"]
        lines.append(f"TY  - {RIS_TYPES.get(c.get('type'), 'GEN')}")
        for a in c.get("author", []):
            lines.append("AU  - " + (a.get("literal") or f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")))
        for a in c.get("editor", []):
            lines.append("ED  - " + f"{a.get('family', '')}, {a.get('given', '')}".strip(", "))
        for tag, key in (("TI", "title"), ("T2", "container-title"), ("VL", "volume"),
                         ("IS", "issue"), ("DO", "DOI"), ("UR", "URL"), ("PB", "publisher"),
                         ("CY", "publisher-place"), ("SN", "ISSN"), ("ET", "edition")):
            if c.get(key):
                lines.append(f"{tag}  - {c[key]}")
        if c.get("ISBN"):
            lines.append(f"SN  - {c['ISBN']}")
        if year_of(c):
            lines.append(f"PY  - {year_of(c)}")
        if c.get("page"):
            sp, _, ep = str(c["page"]).partition("-")
            lines.append(f"SP  - {sp}")
            if ep:
                lines.append(f"EP  - {ep}")
        lines.append(f"N1  - citrello #{r['number']}")
        lines.append("ER  - ")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_bib(refs, path):
    out, used = [], set()
    for r in refs:
        c = r["csl"]
        fam = norm((c.get("author") or [{}])[0].get("family", "ref")).replace(" ", "") or "ref"
        key = f"{fam}{year_of(c)}"
        base, n = key, 1
        while key in used:
            key = base + "abcdefghijklmnopqrstuvwxyz"[n - 1]
            n += 1
        used.add(key)
        r["citekey"] = key
        f = {"title": c.get("title"), "year": year_of(c), "volume": c.get("volume"),
             "number": c.get("issue"), "pages": (c.get("page") or "").replace("-", "--") or None,
             "doi": c.get("DOI"), "url": c.get("URL"), "publisher": c.get("publisher"),
             "isbn": c.get("ISBN"), "issn": c.get("ISSN")}
        if c.get("author"):
            f["author"] = " and ".join(a.get("literal") or f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                                       for a in c["author"])
        t = c.get("type")
        cont_key = {"article-journal": "journal", "paper-conference": "booktitle",
                    "chapter": "booktitle"}.get(t, "journal" if t not in ("book", "thesis", "report", "webpage") else None)
        if cont_key and c.get("container-title"):
            f[cont_key] = c["container-title"]
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in f.items() if v)
        out.append(f"@{BIB_TYPES.get(t, 'misc')}{{{key},\n{body}\n}}\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------

def cmd_extract(args):
    document = docx.Document(args.docx)
    _, entries = find_bibliography(document)
    refs = []
    print(f"Found {len(entries)} reference(s) in the bibliography.")
    for num, _, text in entries:
        r = resolve_reference(num, text, args.offline, args.email)
        refs.append(r)
        print(f"  [{num}] {r['source']:15s} {r['csl'].get('title', '')[:70]}")
    assess_references(refs)
    base = os.path.splitext(args.out or "refs.json")[0]
    write_check_csv(refs, base + "_check.csv")
    write_ris(refs, base + ".ris")
    write_bib(refs, base + ".bib")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(refs, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {base}.json (review/edit), {base}.ris and {base}.bib (import into Mendeley).")
    counts = {k: sum(r["reliability"] == k for r in refs) for k in ("verified", "check", "problem")}
    print(f"\nReliability: {counts['verified']} verified, {counts['check']} to check, {counts['problem']} problem(s)"
          f" - full list in {base}_check.csv")
    for r in refs:
        if r["reliability"] == "problem":
            print(f"  PROBLEM [{r['number']}] " + "; ".join(ch["text"] for ch in r["checks"] if ch["level"] == "problem"))
    return base + ".json"


# --------------------------------------------------------------------------
# Mendeley API connector
# --------------------------------------------------------------------------

API = "https://api.mendeley.com"
TOKEN_FILE = os.path.expanduser("~/.citrello_mendeley.json")
MENDELEY_TYPES = {"article-journal": "journal", "book": "book", "chapter": "book_section",
                  "paper-conference": "conference_proceedings", "thesis": "thesis",
                  "report": "report", "webpage": "web_page", "patent": "patent"}
DOC_MEDIA = "application/vnd.mendeley-document.1+json"


def _token_request(fields, client_id, client_secret):
    form = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    attempts = [  # credentials as HTTP Basic auth, then inside the form body
        (dict(form, Authorization="Basic " + auth), fields),
        (form, dict(fields, client_id=client_id, client_secret=client_secret)),
    ]
    errors = []
    for headers, body in attempts:
        try:
            tok, _ = http_json(API + "/oauth/token", headers, urllib.parse.urlencode(body).encode(), "POST")
            break
        except urllib.error.HTTPError as e:
            errors.append(f"HTTP {e.code}: {e.read().decode(errors='ignore')[:300]}")
    else:
        sys.exit("Mendeley refused the login:\n  " + "\n  ".join(errors) +
                 "\nCheck the client ID/secret and that the app's redirect URL is exactly "
                 "http://localhost:8585/callback")
    tok.update(client_id=client_id, client_secret=client_secret)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tok, f)
    os.chmod(TOKEN_FILE, 0o600)
    return tok


def cmd_auth(args):
    redirect = f"http://localhost:{args.port}/callback"
    state = secrets.token_urlsafe(16)
    result = {}
    if args.manual:  # e.g. when citrello runs on a remote machine
        url = API + "/oauth/authorize?" + urllib.parse.urlencode(
            {"client_id": args.client_id, "redirect_uri": redirect, "response_type": "code",
             "scope": "all", "state": state})
        print("1. Open this link and log in to Mendeley:\n   " + url)
        print("2. Your browser then shows an error page at localhost - that is expected.")
        back = input("3. Paste the full address of that error page here: ").strip()
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(back).query)
        if qs.get("state", [""])[0] != state or "code" not in qs:
            sys.exit("That address does not contain a valid login code.")
        _token_request({"grant_type": "authorization_code", "code": qs["code"][0],
                        "redirect_uri": redirect}, args.client_id, args.client_secret)
        print(f"Logged in. Token stored in {TOKEN_FILE}")
        return

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in qs.items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"citrello: Mendeley login complete, you can close this tab.")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("localhost", args.port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = API + "/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": args.client_id, "redirect_uri": redirect, "response_type": "code",
         "scope": "all", "state": state})
    print("Opening your browser to log in to Mendeley...\nIf it does not open, visit:\n" + url)
    webbrowser.open(url)
    server.timeout = 300
    while "code" not in result and "error" not in result:
        server.handle_request()
    if result.get("state") != state or "code" not in result:
        sys.exit(f"Login failed: {result}")
    _token_request({"grant_type": "authorization_code", "code": result["code"],
                    "redirect_uri": redirect}, args.client_id, args.client_secret)
    print(f"Logged in. Token stored in {TOKEN_FILE}")


def mendeley_call(path, method="GET", body=None, media=None, retry=True):
    if not os.path.exists(TOKEN_FILE):
        sys.exit("Not logged in. Run:  citrello.py auth --client-id ... --client-secret ...")
    tok = json.load(open(TOKEN_FILE))
    headers = {"Authorization": "Bearer " + tok["access_token"]}
    data = json.dumps(body).encode() if body is not None else None
    if media:  # Content-Type only with a body: the web app's browser may only send what Mendeley allows
        headers.update({"Accept": media, **({"Content-Type": media} if data is not None else {})})
    try:
        return http_json(path if path.startswith("http") else API + path, headers, data, method)
    except urllib.error.HTTPError as e:
        if e.code == 401 and retry and tok.get("refresh_token"):
            _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
                           tok["client_id"], tok["client_secret"])
            return mendeley_call(path, method, body, media, retry=False)
        raise SystemExit(f"Mendeley API error {e.code}: {e.read().decode(errors='ignore')[:500]}")


def norm_doi(doi):
    doi = (doi or "").strip().lower()
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.rstrip(".,; ")


def clean_doi(doi):
    """Remove junk that web clippers and exports glue onto DOIs:
    '10.1002/pip.2843;issue:issue:doi', '10.1002/we.1801(accessed:', '10.1063/1.4812391/138579' (kept; see doi_candidates)."""
    d = norm_doi(doi)
    d = re.split(r";(?=[a-z]+:)", d)[0]                      # ;issue:issue:doi  ;wgroup:string:publication
    d = re.split(r"\((?:accessed|viewed|retrieved)", d)[0]    # (accessed: ...
    while d.endswith(")") and d.count(")") > d.count("("):
        d = d[:-1]
    d = re.sub(r"[(\[]$", "", d)
    return d.rstrip(".,;: ")


def doi_candidates(doi):
    """The cleaned DOI, then the same with trailing '/segments' removed
    ('10.1063/1.4812391/138579' -> '10.1063/1.4812391')."""
    d = clean_doi(doi)
    out, parts = [d], d.split("/")
    while len(parts) > 2 and len("/".join(parts[:-1]).split("/", 1)[1]) >= 3:
        parts = parts[:-1]
        out.append("/".join(parts))
    return [c for c in out if re.match(r"^10\.\d{4,9}/\S+$", c)]


class LibraryIndex:
    """A library, searchable by DOI and title, to avoid duplicates.

    Two references with different DOIs are never the same paper, and neither are titles that differ only in a
    number ("... Part 1" / "... Part 2", "... 2019 report" / "... 2020 report")."""

    def __init__(self):
        self.by_doi, self.by_title, self.entries = {}, {}, []

    def add(self, doc_id, title, doi=None, year=None):
        d = norm_doi(doi)
        if d:
            self.by_doi[d] = doc_id
        t = norm(title)
        if t:
            self.by_title.setdefault(t, []).append((doc_id, d))
            self.entries.append((t, str(year or ""), doc_id, d))

    @staticmethod
    def _numbers(t):
        return re.findall(r"\d+", t)

    def find(self, csl):
        """Return (doc_id, how it matched) or (None, None)."""
        doi = norm_doi(csl.get("DOI"))
        if doi and doi in self.by_doi:
            return self.by_doi[doi], "same DOI"
        title = norm(csl.get("title", ""))
        if not title:
            return None, None
        for doc_id, d in self.by_title.get(title, []):
            if not (doi and d and doi != d):
                return doc_id, "same title"
        if len(title) <= 20:  # short titles only count when identical
            return None, None
        year = year_of(csl)
        nums = self._numbers(title)
        best, best_ratio = None, 0.0
        sm = difflib.SequenceMatcher(None)
        sm.set_seq2(title)  # difflib caches the second sequence: compare many titles against this one
        n = len(title)
        for t, y, doc_id, d in self.entries:
            if year and y and year != y:
                continue
            if doi and d and doi != d:
                continue
            if 2 * min(n, len(t)) / (n + len(t)) < 0.93:  # lengths too different to reach 93%
                continue
            if self._numbers(t) != nums:
                continue
            sm.set_seq1(t)
            if sm.real_quick_ratio() < 0.93 or sm.quick_ratio() < 0.93:
                continue
            ratio = sm.ratio()
            if ratio > best_ratio:
                best, best_ratio = doc_id, ratio
        if best_ratio >= 0.93:
            return best, f"near-identical title ({best_ratio:.0%})"
        return None, None


def library_index():
    idx, url, total = LibraryIndex(), "/documents?limit=500&view=bib", 0
    while url:
        docs, headers = mendeley_call(url, media=DOC_MEDIA)
        for d in docs:
            idx.add(d["id"], d.get("title"), (d.get("identifiers") or {}).get("doi"), d.get("year"))
        total += len(docs)
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", "") or "")
        url = nxt.group(1) if nxt else None
    print(f"Your library has {total} document(s).")
    return idx


def csl_to_mendeley(c):
    doc = {"title": c.get("title") or "Untitled", "type": MENDELEY_TYPES.get(c.get("type"), "generic")}
    if c.get("author"):
        doc["authors"] = [{"last_name": a.get("family") or a.get("literal", ""),
                           "first_name": a.get("given", "")} for a in c["author"]]
    if c.get("editor"):
        doc["editors"] = [{"last_name": a.get("family", ""), "first_name": a.get("given", "")} for a in c["editor"]]
    y = year_of(c)
    if y.isdigit():
        doc["year"] = int(y)
    for src, dst in (("container-title", "source"), ("volume", "volume"), ("issue", "issue"),
                     ("page", "pages"), ("publisher", "publisher"), ("publisher-place", "city"),
                     ("edition", "edition")):
        if c.get(src):
            doc[dst] = str(c[src])
    ids = {k.lower(): c[k] for k in ("DOI", "ISBN", "ISSN") if c.get(k)}
    if ids:
        doc["identifiers"] = ids
    if c.get("URL"):
        doc["websites"] = [c["URL"]]
    return doc


def cmd_push(args):
    refs = json.load(open(args.refs, encoding="utf-8"))
    print("Reading your Mendeley library to avoid duplicates...")
    idx = library_index()
    added = linked = 0
    dry = getattr(args, "dry_run", False)
    for r in refs:
        c = r["csl"]
        hit, how = idx.find(c)
        if hit:
            r["mendeley_id"] = hit
            linked += 1
            print(f"  [{r['number']}] already in library ({how}) - skipped, will cite the existing entry")
            continue
        if dry:
            print(f"  [{r['number']}] would be added: {c.get('title', '')[:70]}")
            idx.add(f"(new #{r['number']})", c.get("title"), c.get("DOI"), year_of(c))
            r.pop("mendeley_id", None)
            added += 1
            continue
        doc, _ = mendeley_call("/documents", "POST", csl_to_mendeley(c), DOC_MEDIA)
        r["mendeley_id"] = doc["id"]
        idx.add(doc["id"], c.get("title"), c.get("DOI"), year_of(c))  # catches repeats within the list
        added += 1
        print(f"  [{r['number']}] added: {c.get('title', '')[:70]}")
        with open(args.refs, "w", encoding="utf-8") as f:  # save progress after every addition
            json.dump(refs, f, indent=2, ensure_ascii=False)
    if dry:
        print(f"\nDry run: {added} would be added, {linked} already in your library. Nothing was changed.")
        return
    with open(args.refs, "w", encoding="utf-8") as f:
        json.dump(refs, f, indent=2, ensure_ascii=False)
    print(f"\nAdded {added}, already in library {linked}. IDs saved in {args.refs}.")
    print("Sync Mendeley Reference Manager before opening the converted document.")


# --------------------------------------------------------------------------
# convert: rewrite the .docx
# --------------------------------------------------------------------------

BRACKET_RE = re.compile(r"\[\s*(\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*)\s*\]")
PAREN_RE = re.compile(r"\(\s*(\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*)\s*\)")
SUP_RE = re.compile(r"\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,;]\s*\d+(?:\s*[-–—]\s*\d+)?)*")
OPAQUE = "\x00"


def expand(numbers):
    out = []
    for part in re.split(r"\s*[,;]\s*", numbers.strip()):
        if re.search(r"[-–—]", part):
            a, b = [int(x) for x in re.split(r"\s*[-–—]\s*", part)]
            if b < a or b - a > 50:
                return None
            out.extend(range(a, b + 1))
        elif part:
            out.append(int(part))
    return out


HARMLESS = {qn("w:rPr"), qn("w:t"), qn("w:lastRenderedPageBreak")}


def run_is_simple(r):
    return all(ch.tag in HARMLESS for ch in r)


def run_text(r):
    return "".join(t.text or "" for t in r.findall(qn("w:t")))


def set_run_text(r, text):
    ts = r.findall(qn("w:t"))
    for t in ts[1:]:
        r.remove(t)
    t = ts[0] if ts else OxmlElement("w:t")
    if not ts:
        r.append(t)
    t.text = text
    t.set(qn("xml:space"), "preserve")


def is_superscript(r):
    rpr = r.find(qn("w:rPr"))
    va = rpr.find(qn("w:vertAlign")) if rpr is not None else None
    return va is not None and va.get(qn("w:val")) == "superscript"


def segments(p):
    """List of (run_or_None, start, end, text, superscript) over the paragraph."""
    segs, pos, depth = [], 0, 0
    for r in p.iter(qn("w:r")):
        fcs = [fc.get(qn("w:fldCharType")) for fc in r.findall(qn("w:fldChar"))]
        if "begin" in fcs:
            depth += 1
        editable = depth == 0 and run_is_simple(r) and r.getparent().tag in (qn("w:p"), qn("w:hyperlink"))
        text = run_text(r) if editable else OPAQUE
        if "end" in fcs:
            depth = max(0, depth - 1)
        if text:
            segs.append((r if editable else None, pos, pos + len(text), text, is_superscript(r)))
            pos += len(text)
    return segs


def split_run(r, k):
    """Split run r at character k; return the new right-hand run."""
    text = run_text(r)
    right = copy.deepcopy(r)
    for el in right.findall(qn("w:lastRenderedPageBreak")):
        right.remove(el)
    set_run_text(r, text[:k])
    set_run_text(right, text[k:])
    r.addnext(right)
    return right


def make_run(rpr, child):
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    r.append(child)
    return r


def fld(kind):
    e = OxmlElement("w:fldChar")
    e.set(qn("w:fldCharType"), kind)
    return e


def text_el(tag, text):
    e = OxmlElement(tag)
    e.text = text
    e.set(qn("xml:space"), "preserve")
    return e


def field_runs(rpr, instruction, display):
    return [make_run(rpr, fld("begin")),
            make_run(rpr, text_el("w:instrText", instruction)),
            make_run(rpr, fld("separate")),
            make_run(rpr, text_el("w:t", display)),
            make_run(rpr, fld("end"))]


def item_uri(ref):
    mid = ref.get("mendeley_id") or str(uuid.uuid5(uuid.NAMESPACE_URL, "citrello:" + ref["raw"]))
    return f"http://www.mendeley.com/documents/?uuid={mid}"


def citation_instruction(items, display):
    """items: [{"ref": ref, "prefix": str, "locator": str, "label": str, "suppress": bool}, ...]"""
    out = []
    for i, it in enumerate(items, 1):
        data = dict(it["ref"]["csl"])
        data["id"] = f"ITEM-{i}"
        ci = {"id": f"ITEM-{i}", "itemData": data, "uris": [item_uri(it["ref"])]}
        if it.get("prefix"):
            ci["prefix"] = it["prefix"]
        if it.get("locator"):
            ci["locator"], ci["label"] = it["locator"], it.get("label", "page")
        if it.get("suppress"):
            ci["suppress-author"] = 1  # narrative citation: "Smith et al. (2020)"
        out.append(ci)
    payload = {"citationItems": out,
               "mendeley": {"formattedCitation": display, "plainTextFormattedCitation": display,
                            "previouslyFormattedCitation": display},
               "properties": {"noteIndex": 0},
               "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"}
    return "ADDIN CSL_CITATION " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ---- numbered citations: [1], [2-4], superscript, (1) ----

def numeric_finder(by_num, styles):
    def find(p, full, sup, report):
        found = []
        if "bracket" in styles:
            found += [(m.start(), m.end(), m.group(1)) for m in BRACKET_RE.finditer(full)]
        if "paren" in styles:
            found += [(m.start(), m.end(), m.group(1)) for m in PAREN_RE.finditer(full)]
        if "superscript" in styles:
            found += [(m.start(), m.end(), m.group(0)) for m in SUP_RE.finditer(full)
                      if set(sup[m.start():m.end()]) == {"1"}]
        out = []
        for s, e, nums_txt in found:
            nums = expand(nums_txt)
            if not nums or any(n not in by_num for n in nums):
                report.append(f"SKIPPED (number not in bibliography): {full[s:e]!r} "
                              f"in: {full[max(0, s - 40):e + 10]!r}")
                continue
            out.append((s, e, [{"ref": by_num[n]} for n in nums]))
        plain = "".join(t.text or "" for t in p.iter(qn("w:t")))
        expected = len(BRACKET_RE.findall(plain)) if "bracket" in styles else 0
        seen = len([f for f in found if full[f[0]] == "["])
        if expected > seen:
            report.append(f"NOT CONVERTED ({expected - seen} bracketed number(s) inside a run with special "
                          f"content - please insert manually): {plain[:80]!r}...")
        return out
    return find


# ---- author-year citations: (Smith et al., 2020), Smith and Lee (2019a) ----

YEAR = r"(?:1[5-9]\d{2}|20\d{2})[a-z]?|n\.\s?d\.?|in\s+press|forthcoming"
YEAR_ANY_RE = re.compile(rf"(?<![\w/.\-])(?:{YEAR})(?![\w/])", re.I)
PREFIX_RE = re.compile(r"^(?:e\.\s?g\.|i\.\s?e\.|see\s+also|see|cf\.|compare|for\s+example|for\s+a\s+review,?\s+see|"
                       r"reviewed\s+(?:in|by)|as\s+cited\s+in|also|including)\s*[,:]?\s+", re.I)
LOC_RE = re.compile(r",?\s*\b(pp?|paras?|ch|chap|sec|fig|figs|tab|vol)\.?\s*(\d[\w]*(?:\s*[–\-]\s*\w+)?)\s*$", re.I)
LOC_LABEL = {"p": "page", "pp": "page", "para": "paragraph", "paras": "paragraph", "ch": "chapter",
             "chap": "chapter", "sec": "section", "fig": "figure", "figs": "figure", "tab": "table",
             "vol": "volume"}
YEARS_TAIL_RE = re.compile(rf"[,\s]\s*((?:{YEAR})(?:\s*,\s*(?:(?:{YEAR})|[a-z](?![\w.])))*)\s*$", re.I)
CAP = r"[A-ZÀ-ÖØ-ÞĀ-Ž]"
PARTICLE = r"(?:(?:[Vv]an|[Vv]on|[Dd]e|[Dd]er|[Dd]en|[Dd]u|[Dd]a|[Dd]i|[Ll]e|[Ll]a|[Dd]el|[Dd]ella|[Dd]os|[Tt]en|[Tt]er|[Aa]l|[Ee]l)\s+){0,3}"
NAME = rf"{PARTICLE}{CAP}[\w’'\-]+"
NAMESEQ = rf"{NAME}(?:\s+et\s+al\.?|(?:\s*,\s*{NAME})*\s*,?\s+(?:and|&)\s+{NAME})?"
NARRATIVE_RE = re.compile(rf"(?P<names>{NAMESEQ})\s+\((?P<inner>[^()]{{1,60}})\)")
GROUP_RE = re.compile(r"\(([^()]{3,600})\)|\[([^\[\]]{3,600})\]")


def norm_year(y):
    y = re.sub(r"\s+", "", y.lower())
    return "n.d." if y.startswith("n") else y


def author_year_keys(refs):
    """For every reference: its author part (normalised), year and first-author length."""
    keys, seen_raw = [], set()
    for r in refs:
        raw = r["raw"]
        if norm(raw) in seen_raw:
            continue  # exact duplicate entry: citations go to the first copy
        seen_raw.add(norm(raw))
        m = re.search(rf"\(\s*({YEAR})\s*\)", raw, re.I) or YEAR_ANY_RE.search(raw)
        seg = raw[:m.start()] if m else raw[:100]
        year = norm_year(m.group(1) if m.groups() else m.group(0)) if m else ""
        cut = re.search(r",|\band\b|&|;|\(", seg)
        first_len = len(norm(seg[:cut.start()])) if cut and cut.start() > 0 else len(norm(seg))
        keys.append({"ref": r, "seg": norm(seg), "year": year, "first_len": first_len})
    groups = {}  # identical author lists in the same year -> 2019a, 2019b
    for k in keys:
        if k["year"] and k["year"][-1].isdigit():
            groups.setdefault((k["seg"], k["year"]), []).append(k)
    for g in groups.values():
        if len(g) > 1:
            for i, k in enumerate(g):
                k["year"] += "abcdefghijklmnopqrstuvwxyz"[i]
    return keys


def split_names(txt):
    txt = re.sub(r"\bet\s+al\.?", "", txt)
    return [p.strip() for p in re.split(r"\s*,\s*|\s+and\s+|\s*&\s*", txt) if p.strip()]


def expand_years(s):
    out = []
    for tok in re.split(r"\s*,\s*", s.strip()):
        if re.fullmatch(r"[a-z]", tok) and out:
            out.append(re.sub(r"[a-z]$", "", out[-1]) + tok)  # 2019a, b -> 2019a, 2019b
        else:
            out.append(tok)
    return out


def parse_group(text):
    """'e.g. Smith et al., 2019; Lee, 2020a, b, p. 5' -> [{prefix, names, year, locator, label}], or None."""
    items, prev = [], None
    for part in re.split(r"\s*;\s*", text.strip()):
        if not part:
            continue
        prefix = ""
        m = PREFIX_RE.match(part)
        if m:
            prefix, part = m.group(0).strip(), part[m.end():]
        loc = label = None
        m = LOC_RE.search(part)
        if m:
            label, loc, part = LOC_LABEL[m.group(1).lower()], m.group(2), part[:m.start()]
        m = YEARS_TAIL_RE.search(" " + part)
        if not m:
            return None
        names_txt = (" " + part)[:m.start()].strip(" ,")
        names = split_names(names_txt) if names_txt else prev
        if not names:
            return None
        prev = names
        for y in expand_years(m.group(1)):
            items.append({"prefix": prefix, "names": names, "year": y, "locator": loc, "label": label})
            prefix = ""
    return items


def match_citation(names, year, keys):
    """Return (ref, None) or (None, reason)."""
    year, first = norm_year(year), norm(names[0])
    if not first:
        return None, "no author"
    cands = []
    for k in keys:
        ky = k["year"]
        exact = ky == year
        if not (exact or (ky[:-1] == year and ky[-1:].isalpha()) or (year[:-1] == ky and year[-1:].isalpha())):
            continue
        seg = f" {k['seg']} "
        pos = seg.find(f" {first} ")
        if pos < 0:
            words = first.split()  # a title cited by its first words: 'The impact of ...'
            if len(words) >= 3 and k["seg"].startswith(" ".join(words[:3])):
                pos = 0
            else:
                continue
        if pos > k["first_len"] + 1:
            continue  # the cited name must be the FIRST author
        if any(f" {norm(n)} " not in seg for n in names[1:]):
            continue
        cands.append((0 if exact else 1, pos, k))
    if not cands:
        return None, "no reference with this author and year"
    cands.sort(key=lambda c: (c[0], c[1]))
    if len(cands) > 1 and cands[0][:2] == cands[1][:2]:
        return None, "matches more than one reference (add a/b to the year?)"
    return cands[0][2]["ref"], None


def years_only(inner):
    """'2020', '2019a, b', '2020, p. 5' -> [{year, locator, label}]; None if there is anything else."""
    out = []
    for part in re.split(r"\s*;\s*", inner.strip()):
        loc = label = None
        m = LOC_RE.search(part)
        if m:
            label, loc, part = LOC_LABEL[m.group(1).lower()], m.group(2), part[:m.start()]
        if not re.fullmatch(rf"\s*(?:{YEAR})(?:\s*,\s*(?:(?:{YEAR})|[a-z]))*\s*", part, re.I):
            return None
        out += [{"year": y, "locator": loc, "label": label} for y in expand_years(part)]
    return out


def looks_like_names(txt):
    return bool(re.match(rf"^\s*(?:{PREFIX_RE.pattern[1:]})?[‘'\"“]?{CAP}", txt))


def author_year_finder(refs):
    keys = author_year_keys(refs)

    def find(p, full, sup, report):
        out, taken = [], []
        for m in NARRATIVE_RE.finditer(full):  # Smith et al. (2020)  -> field on "(2020)"
            parsed = years_only(m.group("inner"))
            if not parsed:
                continue  # "(...)" holds more than years -> handled as a normal group below
            names = split_names(m.group("names"))
            items, failed = [], None
            for it in parsed:
                ref, why = None, None
                for k in range(len(names)):  # tolerate a capitalised word before the names
                    ref, why = match_citation(names[k:] if k else names, it["year"], keys)
                    if ref or len(names) == 1:
                        break
                if not ref:
                    failed = why
                    break
                items.append({"ref": ref, "suppress": True, "locator": it["locator"], "label": it["label"]})
            s, e = m.start("inner") - 1, m.end()
            if failed:
                report.append(f"NOT CONVERTED ({failed}): {full[m.start():e]!r}")
            else:
                out.append((s, e, items))
            taken.append((s, e))
        for m in GROUP_RE.finditer(full):  # (Smith, 2019; Lee and Kim, 2020)
            s, e = m.span()
            if any(a < e and s < b for a, b in taken) or not YEAR_ANY_RE.search(m.group(0)):
                continue
            text = m.group(1) or m.group(2)
            parsed = parse_group(text)
            if parsed is None or not parsed:
                if looks_like_names(text) and len(text) < 200:
                    report.append(f"NOT RECOGNISED as a citation: {m.group(0)!r}")
                continue
            if all(len(it["names"]) == 1 and not re.search(CAP, it["names"][0]) for it in parsed):
                continue  # e.g. "(installed in 2016)"
            items, failed = [], None
            for it in parsed:
                ref, why = match_citation(it["names"], it["year"], keys)
                if not ref:
                    failed = f"{why}: {', '.join(it['names'])} {it['year']}"
                    break
                items.append({"ref": ref, "prefix": it["prefix"], "locator": it["locator"], "label": it["label"]})
            if failed:
                report.append(f"NOT CONVERTED ({failed}): {m.group(0)!r}")
                continue
            out.append((s, e, items))
        # bare "(2012)" with no recognisable author right before it
        for m in re.finditer(rf"\(\s*(?:{YEAR})\s*\)", full, re.I):
            if not any(a <= m.start() < b for a, b in taken):
                report.append(f"NOT CONVERTED (year without a recognisable author before it): "
                              f"{full[max(0, m.start() - 50):m.end()]!r}")
        return out
    return find


def convert_paragraph(p, finder, report, cited):
    segs = segments(p)
    full = "".join(s[3] for s in segs)
    sup = "".join(("1" if s[4] else "0") * len(s[3]) for s in segs)
    found = sorted(finder(p, full, sup, report), key=lambda f: f[0])
    count = 0
    for s, e, items in reversed(found):
        covered = [seg for seg in segs if seg[1] < e and seg[2] > s]
        if not covered or any(seg[0] is None for seg in covered) or len({seg[0].getparent() for seg in covered}) > 1:
            report.append(f"SKIPPED (inside another field/complex run): {full[s:e]!r}")
            continue
        first, last = covered[0], covered[-1]
        runs = [seg[0] for seg in covered]
        # split the boundary runs so the citation occupies whole runs
        if last[2] > e:
            split_run(last[0], e - last[1])
        if first[1] < s:
            right = split_run(first[0], s - first[1])
            if first[0] is last[0]:
                runs = [right]
            else:
                runs[0] = right
        rpr = runs[0].find(qn("w:rPr"))
        display = full[s:e]
        anchor = runs[0]
        for fr in field_runs(rpr, citation_instruction(items, display), display):
            anchor.addprevious(fr)
        for r in runs:
            r.getparent().remove(r)
        cited.update(it["ref"]["number"] for it in items)
        count += 1
    return count


def remove_bibliography_fields(body):
    """Unwrap Mendeley bibliography fields left by an earlier run (the text stays)."""
    runs = list(body.iter(qn("w:r")))
    kind = lambda r: (r.find(qn("w:fldChar")).get(qn("w:fldCharType")) if r.find(qn("w:fldChar")) is not None else None)
    i = 0
    while i < len(runs):
        if kind(runs[i]) != "begin":
            i += 1
            continue
        instr, j = "", i + 1
        while j < len(runs) and kind(runs[j]) is None:
            instr += "".join(t.text or "" for t in runs[j].findall(qn("w:instrText")))
            j += 1
        if "CSL_BIBLIOGRAPHY" not in instr or j >= len(runs) or kind(runs[j]) != "separate":
            i += 1
            continue
        depth, k = 1, j + 1
        while k < len(runs) and depth:
            depth += {"begin": 1, "end": -1}.get(kind(runs[k]), 0)
            k += 1
        for r in runs[i:j + 1] + ([runs[k - 1]] if depth == 0 else []):
            if r.getparent() is not None:
                r.getparent().remove(r)
        i = k


def detect_mode(entries):
    numbered = sum(1 for _, p, _ in entries if NUM_PREFIX_RE.match(p.text) or has_numbering(p))
    return "numeric" if numbered >= len(entries) / 2 else "author-year"


def convert_document(docx_path, refs, styles=("bracket", "superscript"), mode="auto", out=None):
    """Convert citations; save to `out` (or just analyse when out is None)."""
    by_num = {r["number"]: r for r in refs}
    document = docx.Document(docx_path)
    body = document.element.body
    remove_bibliography_fields(body)
    heading, entries = find_bibliography(document)
    if mode == "auto":
        mode = detect_mode(entries)
    finder = numeric_finder(by_num, set(styles)) if mode == "numeric" else author_year_finder(refs)
    bib_paras = {id(e[1]._p) for e in entries}
    report, total, cited = [], 0, set()
    for p_el in list(body.iter(qn("w:p"))):
        if p_el is heading._p:
            break  # citations live before the reference list
        if id(p_el) in bib_paras:
            continue
        total += convert_paragraph(p_el, finder, report, cited)

    # Bibliography field spanning the reference-list paragraphs
    first_p, last_p = entries[0][1]._p, entries[-1][1]._p
    rpr_src = first_p.find(qn("w:r"))
    rpr = rpr_src.find(qn("w:rPr")) if rpr_src is not None else None
    begin = [make_run(rpr, fld("begin")),
             make_run(rpr, text_el("w:instrText", "ADDIN Mendeley Bibliography CSL_BIBLIOGRAPHY ")),
             make_run(rpr, fld("separate"))]
    ppr = first_p.find(qn("w:pPr"))
    for i, r in enumerate(begin):
        if ppr is not None:
            ppr.addnext(r) if i == 0 else begin[i - 1].addnext(r)
        else:
            first_p.insert(i, r)
    last_p.append(make_run(rpr, fld("end")))
    if out:
        document.save(out)
    return {"converted": total, "entries": len(entries), "report": report, "out": out,
            "mode": mode, "cited": sorted(cited)}


def cmd_convert(args):
    refs = json.load(open(args.refs, encoding="utf-8"))
    out = args.out or os.path.splitext(args.docx)[0] + "_mendeley.docx"
    res = convert_document(args.docx, refs, args.styles.split(","), getattr(args, "mode", "auto"), out)
    print(f"Citation style: {res['mode']}.")
    print(f"Converted {res['converted']} citation(s); bibliography of {res['entries']} entries is now a Mendeley field.")
    print(f"Saved {out}")
    if res["report"]:
        rep = os.path.splitext(out)[0] + "_report.txt"
        with open(rep, "w", encoding="utf-8") as f:
            f.write("\n".join(res["report"]) + "\n")
        print(f"{len(res['report'])} possible citation(s) were left untouched - see {rep}")
    uncited = [r["number"] for r in refs if r["number"] not in set(res["cited"])]
    if uncited:
        print(f"References never cited in the text: {uncited}")
    if any(not r.get("mendeley_id") for r in refs):
        print("Note: references have no Mendeley ID yet (run `push` for direct library links, "
              "or import refs.ris; the citations carry full metadata either way).")
    return res


def cmd_all(args):
    json_path = cmd_extract(argparse.Namespace(docx=args.docx, out=args.refs, offline=args.offline,
                                               email=args.email))
    if args.push:
        cmd_push(argparse.Namespace(refs=json_path))
    cmd_convert(argparse.Namespace(docx=args.docx, refs=json_path, out=args.out, styles=args.styles,
                                   mode=args.mode))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="bibliography -> refs.json / refs.ris / refs.bib")
    e.add_argument("docx")
    e.add_argument("-o", "--out", default="refs.json")
    e.add_argument("--offline", action="store_true", help="skip Crossref, parse locally only")
    e.add_argument("--email", help="your email for Crossref's polite pool (optional)")
    e.set_defaults(func=cmd_extract)

    a = sub.add_parser("auth", help="log in to Mendeley (one time)")
    a.add_argument("--client-id", required=True)
    a.add_argument("--client-secret", required=True)
    a.add_argument("--port", type=int, default=8585)
    a.add_argument("--manual", action="store_true", help="paste the redirect URL instead of a local server")
    a.set_defaults(func=cmd_auth)

    p = sub.add_parser("push", help="add refs.json entries to your Mendeley library")
    p.add_argument("refs", nargs="?", default="refs.json")
    p.add_argument("--dry-run", action="store_true", help="only show what would be added")
    p.set_defaults(func=cmd_push)

    styles_help = "comma list of citation forms to detect: bracket, superscript, paren (default: bracket,superscript)"
    c = sub.add_parser("convert", help="replace typed citations with Mendeley fields")
    c.add_argument("docx")
    c.add_argument("refs", nargs="?", default="refs.json")
    c.add_argument("-o", "--out")
    c.add_argument("--styles", default="bracket,superscript", help=styles_help)
    c.add_argument("--mode", choices=["auto", "numeric", "author-year"], default="auto",
                   help="citation style of the article (default: detect)")
    c.set_defaults(func=cmd_convert)

    al = sub.add_parser("all", help="extract + (push) + convert")
    al.add_argument("docx")
    al.add_argument("--refs", default="refs.json")
    al.add_argument("-o", "--out")
    al.add_argument("--push", action="store_true")
    al.add_argument("--offline", action="store_true")
    al.add_argument("--email")
    al.add_argument("--styles", default="bracket,superscript", help=styles_help)
    al.add_argument("--mode", choices=["auto", "numeric", "author-year"], default="auto")
    al.set_defaults(func=cmd_all)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
