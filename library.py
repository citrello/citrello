"""
Citrello library - your references in one file on your computer, shared by the
Citrello app and the Claude connector.

Stored as JSON (CSL-JSON items, the open format Zotero and Mendeley also use) in
~/Citrello/library.json, or $CITRELLO_HOME/library.json. Put that folder in
OneDrive/Dropbox to have it on several computers.
"""
import contextlib
import json
import os
import re
import sys
import shutil
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser

import citrello_core as core

HOME = os.environ.get("CITRELLO_HOME") or os.path.join(os.path.expanduser("~"), "Citrello")
# data of an earlier development version, picked up once (see migrate_legacy_data)
OLD_HOME = os.environ.get("CITRELLO_LEGACY_HOME") or os.path.join(os.path.expanduser("~"), "Refcite")


def library_path():
    return os.path.join(HOME, "library.json")


def migrate_legacy_data():
    """Copy the library, settings and Mendeley login of an earlier development version once.
    The old files are left untouched as a backup. Returns what was copied."""
    done = []
    try:
        if not os.path.exists(library_path()) and os.path.exists(os.path.join(OLD_HOME, "library.json")):
            shutil.copytree(OLD_HOME, HOME, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".library.lock", "*.tmp"))
            done.append("library")
        for old, new in ((".refcite_app.json", ".citrello_app.json"), (".refcite_mendeley.json", ".citrello_mendeley.json")):
            o, n = os.path.expanduser("~/" + old), os.path.expanduser("~/" + new)
            if os.path.exists(o) and not os.path.exists(n):
                shutil.copyfile(o, n)
                done.append(new)
    except OSError as e:  # never block start-up over this
        print(f"Could not copy earlier data: {e}")
    return done


# --------------------------------------------------------------------------
# Storage: reload before every read, lock + atomic replace on every write,
# so the app and the Claude connector can use the library at the same time.
# --------------------------------------------------------------------------

@contextlib.contextmanager
def _lock(timeout=10):
    os.makedirs(HOME, exist_ok=True)
    path = os.path.join(HOME, ".library.lock")
    start = time.time()
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            with contextlib.suppress(OSError):  # a crashed process left the lock behind
                if time.time() - os.path.getmtime(path) > 30:
                    os.remove(path)
                    continue
            if time.time() - start > timeout:
                raise RuntimeError("The library is busy; try again in a moment.")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            os.remove(path)


def load():
    try:
        with open(library_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data.setdefault("version", 1)
    data.setdefault("items", [])
    data.setdefault("projects", [])
    return data


def _save(data):
    os.makedirs(HOME, exist_ok=True)
    tmp = library_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, library_path())
    try:  # keep automatic .bib exports (LaTeX) up to date
        import latex
        latex.run_exports(data)
    except Exception:
        pass


@contextlib.contextmanager
def editing():
    """with editing() as data: ...  - changes are saved when the block ends."""
    with _lock():
        data = load()
        yield data
        _save(data)


# --------------------------------------------------------------------------
# Keys, duplicates, search
# --------------------------------------------------------------------------

# letters that don't split into a plain letter plus an accent: Ørsted -> Orsted, Weiß -> Weiss, Łukasz -> Lukasz
_LATIN = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "Ae", "œ": "oe", "Œ": "Oe", "ß": "ss", "ł": "l", "Ł": "L",
                        "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th", "ı": "i", "ħ": "h", "Ħ": "H"})


def _ascii(s):
    return unicodedata.normalize("NFKD", (s or "").translate(_LATIN)).encode("ascii", "ignore").decode()


def make_key(csl, taken):
    """Readable citation key like Topham2017 (Topham2017b if taken)."""
    a = (csl.get("author") or csl.get("editor") or [{}])[0]
    name = a.get("family") or a.get("literal") or ""
    if not name and csl.get("type") == "webpage" and csl.get("container-title"):
        name = re.sub(r"^www\.", "", csl["container-title"]).split(".")[0].split()[0]  # site name: Orsted2025
    name = re.sub(r"[^A-Za-z]", "", _ascii(name.split()[-1] if " " in name and a.get("family") else
                                          (name.split()[0] if name else "")))
    if not name:
        words = [w for w in re.findall(r"[A-Za-z]+", _ascii(csl.get("title") or ""))
                 if w.lower() not in ("the", "a", "an", "on", "of")]
        name = words[0] if words else "Ref"
    base = name[:1].upper() + name[1:] + (core.year_of(csl) or "nd")
    key, n = base, 0
    while key in taken:
        n += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26] * (1 + (n - 1) // 26)
    return key


def _index(items):
    idx = core.LibraryIndex()
    for it in items:
        idx.add(it["id"], it["csl"].get("title"), it["csl"].get("DOI"), core.year_of(it["csl"]))
    return idx


def find_duplicate(data, csl, index=None):
    """index: a LibraryIndex of data's items, kept up to date by the caller (import_items), so a large import
    doesn't rebuild it for every reference."""
    hit, how = (index or _index(data["items"])).find(csl)
    if hit:
        return next(it for it in data["items"] if it["id"] == hit), how
    return None, None


def get(data, key_or_id):
    k = (key_or_id or "").lstrip("@")
    return next((it for it in data["items"] if it["key"] == k or it["id"] == k), None)


def short_ref(it):
    c = it["csl"]
    names = [a.get("family") or a.get("literal", "") for a in c.get("author", [])]
    au = (names[0] + (" et al." if len(names) > 2 else f" & {names[1]}" if len(names) == 2 else "")) if names else ""
    return f"{au} ({core.year_of(c) or 'n.d.'}) {c.get('title', '')}".strip()


def search(data, query="", project=None, limit=None):
    words = core.norm(query).split()
    out = []
    for it in data["items"]:
        if project and project not in it.get("projects", []):
            continue
        c = it["csl"]
        hay = core.norm(" ".join([it["key"], c.get("title", ""), c.get("container-title", ""),
                                     core.year_of(c), c.get("DOI", ""), " ".join(it.get("tags", [])),
                                     " ".join(a.get("family") or a.get("literal", "") for a in c.get("author", []))]))
        if all(w in hay for w in words):
            out.append(it)
    out.sort(key=lambda it: it.get("added", ""), reverse=True)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------
# Adding references
# --------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


KEY_OK_RE = re.compile(r"^[A-Za-z0-9_][\w:.+\-/]{0,79}$")


def add_csl(data, csl, project=None, source="manual", raw=None, extra=None, key=None, index=None):
    """Add one CSL item (or return the existing copy). Returns (item, created, how_matched).
    `key`: the key to use if it is free (e.g. the BibTeX key from an imported .bib)."""
    csl = core.trim_csl(dict(csl)) if csl else {}
    if not csl.get("title"):
        raise ValueError("A reference needs at least a title.")
    existing, how = find_duplicate(data, csl, index)
    if existing:
        if project and project not in existing.setdefault("projects", []):
            existing["projects"].append(project)
        for k, v in (extra or {}).items():  # e.g. remember the Mendeley/Zotero id
            existing.setdefault(k, v)
        return existing, False, how
    taken = {i["key"] for i in data["items"]}
    if not (key and KEY_OK_RE.match(key) and key not in taken):
        key = make_key(csl, taken)
    it = {"id": str(uuid.uuid4()), "key": key,
          "csl": csl, "projects": [project] if project else [], "tags": [], "added": _now(),
          "source": source}
    if raw:
        it["raw"] = raw
    it.update(extra or {})
    data["items"].append(it)
    if index is not None:
        index.add(it["id"], csl.get("title"), csl.get("DOI"), core.year_of(csl))
    if project and project not in data["projects"]:
        data["projects"].append(project)
    return it, True, None


# --------------------------------------------------------------------------
# Links: find the paper behind a URL
# --------------------------------------------------------------------------

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36 Citrello/2.0")
URL_ONLY_RE = re.compile(r"^\s*<?(https?://\S+?)>?\s*$", re.I)


def doi_from_url(url):
    """DOI hidden in a publisher link, or None. Nature and arXiv IDs are turned into their DOIs."""
    u = urllib.parse.unquote(url)
    path = re.split(r"[?#]", u)[0]
    m = core.DOI_RE.search(path)
    if m:
        d = re.sub(r"/(full|abstract|abs|pdf|epdf|html|fulltext|references|figures)$", "", m.group(1), flags=re.I)
        return core.clean_doi(re.sub(r"\.pdf$", "", d, flags=re.I))
    m = re.search(r"nature\.com/articles/([a-z0-9][a-z0-9.\-]+?)(?:\.pdf)?/?$", path, re.I)
    if m:
        return "10.1038/" + m.group(1).lower()
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?", path, re.I)
    if m:
        return "10.48550/arxiv." + m.group(1).lower()
    return None


def _doi_from_pii(url, email):
    """ScienceDirect links carry Elsevier's PII; Crossref knows the matching DOI."""
    m = re.search(r"/pii/(S?[0-9X()\-]{15,22})", url, re.I)
    if not m:
        return None
    pii = re.sub(r"[()\-]", "", m.group(1)).upper()
    headers = {"User-Agent": core.USER_AGENT.format(email=email or "anonymous@example.com")}
    res, _ = core.http_json(core.CROSSREF + "/works?" + urllib.parse.urlencode(
        {"filter": f"alternative-id:{pii}", "rows": 1}), headers)
    items = res.get("message", {}).get("items", [])
    return items[0]["DOI"] if items else None


class _Meta(HTMLParser):
    """Collects <meta> tags (citation_*, dc.*, og:*, prism.*) and the <title>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self._in_title = {}, "", False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and a.get("content"):
                self.meta.setdefault(name, []).append(a["content"].strip())
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _fetch_page(url):
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,*/*;q=0.8",
                                               "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read(3_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
        final = r.geturl()
    p = _Meta()
    p.feed(raw.decode(charset, errors="replace"))
    return p.meta, re.sub(r"\s+", " ", p.title).strip(), final


def _first(meta, *names):
    for n in names:
        if meta.get(n):
            return meta[n][0]
    return ""


def _csl_from_meta(meta, title, url):
    """Scholarly page without a usable DOI, or an ordinary web page -> CSL."""
    scholarly = bool(_first(meta, "citation_title", "dc.title", "prism.publicationname", "citation_journal_title"))
    c = {"title": _first(meta, "citation_title", "dc.title", "og:title", "twitter:title") or title, "URL": url}
    authors = meta.get("citation_author") or meta.get("dc.creator") or meta.get("author") or []
    c["author"] = [p for p in (core.parse_name(a) if "," not in a else
                               {"family": a.split(",")[0].strip(), "given": a.split(",", 1)[1].strip()} for a in authors) if p]
    date = _first(meta, "citation_publication_date", "citation_date", "citation_online_date", "dc.date",
                  "article:published_time", "prism.publicationdate", "date")
    y = re.search(r"(1[6-9]|20)\d{2}", date)
    if y:
        c["issued"] = {"date-parts": [[int(y.group(0))]]}
    if scholarly:
        c["type"] = "paper-conference" if _first(meta, "citation_conference_title") else "article-journal"
        c["container-title"] = _first(meta, "citation_journal_title", "citation_conference_title", "prism.publicationname")
        for k, n in (("volume", "citation_volume"), ("issue", "citation_issue"), ("publisher", "citation_publisher")):
            if _first(meta, n):
                c[k] = _first(meta, n)
        fp, lp = _first(meta, "citation_firstpage"), _first(meta, "citation_lastpage")
        if fp:
            c["page"] = fp + (f"-{lp}" if lp and lp != fp else "")
    else:
        c["type"] = "webpage"
        c["container-title"] = _first(meta, "og:site_name", "application-name") or urllib.parse.urlparse(url).netloc
    c["title"] = re.sub(r"\s+[|\-–]\s+" + re.escape(c.get("container-title") or "~~") + r"\s*$", "", c["title"] or "")
    return {k: v for k, v in c.items() if v}, scholarly


def resolve_url(url, email=None):
    """A link -> ("doi", doi) or ("csl", csl, scholarly). Raises ValueError when nothing usable is found."""
    doi = doi_from_url(url)
    if doi:
        return ("doi", doi)
    try:
        doi = _doi_from_pii(url, email)
    except Exception:
        doi = None
    if doi:
        return ("doi", doi)
    try:
        meta, title, final = _fetch_page(url)
    except urllib.error.HTTPError as e:
        raise ValueError(f"The website refused to show the page to Citrello (HTTP {e.code}). "
                         "Paste the paper's DOI or its title instead.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(f"Could not open the link ({e}). Check the internet connection, or paste the DOI or title.")
    doi = doi_from_url(final) or core.clean_doi(_first(meta, "citation_doi", "dc.identifier", "dc.identifier.doi",
                                                          "prism.doi", "bepress_citation_doi", "doi"))
    if doi and re.match(r"^10\.\d{4,9}/\S+$", doi):
        return ("doi", doi)
    csl, scholarly = _csl_from_meta(meta, title, final)
    if not csl.get("title"):
        raise ValueError("No paper details were found on that page. Paste the DOI or the title instead.")
    return ("csl", csl, scholarly)


def _rec_from_page_csl(csl, scholarly, raw, email):
    if scholarly:  # a paper without a DOI on its page: let Crossref confirm it by title
        rec = core.resolve_reference(1, f"{csl.get('title', '')}. {csl.get('container-title', '')}", False, email)
        if rec["source"] != "parsed":
            core.assess_references([rec])
            return rec
    rec = {"number": 1, "raw": raw, "source": "web", "csl": csl, "typed": {}, "meta": {}}
    core.assess_references([rec])
    return rec


def resolve(text, offline=False, email=None):
    """A DOI, a link (doi.org or a publisher's page), or a typed reference -> resolved record with checks."""
    text = text.strip()
    m = URL_ONLY_RE.match(text)
    if m and not re.search(r"(dx\.)?doi\.org/", text, re.I) and not offline:
        found = resolve_url(m.group(1), email)
        if found[0] == "doi":
            text = "doi:" + found[1]
        else:
            return _rec_from_page_csl(found[1], found[2], text, email)
    m = core.DOI_RE.search(text)
    if m and len(text) < len(m.group(0)) + 25:  # just a DOI (or doi.org link)
        text = "doi:" + m.group(1).rstrip(".,;)")
    rec = core.resolve_reference(1, text, offline, email)
    core.assess_references([rec])
    return rec


def resolve_clip(url, title="", meta=None, selection="", email=None):
    """What the browser button sends (the page's address, title, citation <meta> tags and any selected text)
    -> resolved record. The page was read in the user's own browser, so sites that block robots still work."""
    meta = {k.lower(): (v if isinstance(v, list) else [v]) for k, v in (meta or {}).items()}
    sel = re.sub(r"\s+", " ", selection or "").strip()
    if len(sel) >= 15:  # the user selected a reference (or just its title) on the page: that is what they want
        rec = resolve(sel, email=email)
        if rec["source"] == "parsed" and not (rec["csl"].get("author") and core.year_of(rec["csl"])):
            raise ValueError(f"Could not find the selected text online: “{sel[:120]}”. Select the whole reference "
                             "(authors, year and title), or clear the selection to add this page's paper.")
        return rec
    doi = core.clean_doi(_first(meta, "citation_doi", "dc.identifier.doi", "prism.doi", "bepress_citation_doi"))
    if not re.match(r"^10\.\d{4,9}/\S+$", doi or ""):
        ident = core.DOI_RE.search(" ".join(meta.get("dc.identifier", [])))
        doi = core.clean_doi(ident.group(1)) if ident else None
    doi = doi or doi_from_url(url)
    if not doi:
        try:
            doi = _doi_from_pii(url, email)
        except Exception:
            doi = None
    if doi:
        return resolve("doi:" + doi, email=email)
    csl, scholarly = _csl_from_meta(meta, title, url)
    if not csl.get("title"):
        raise ValueError("No paper details were found on this page. Select the reference text and click the button again.")
    return _rec_from_page_csl(csl, scholarly, url, email)


def add_reference(text, project=None, offline=False, email=None):
    """Resolve and add. Returns (item, created, how_matched)."""
    rec = resolve(text, offline, email)
    if not rec["csl"].get("title"):
        raise ValueError("Could not find this reference online (no internet, or the DOI is not registered with "
                         "Crossref). Add it with the full reference text instead.")
    with editing() as data:
        it, created, how = add_csl(data, rec["csl"], project, rec["source"], raw=text,
                                   extra={"checks": rec["checks"], "reliability": rec["reliability"]})
        return it, created, how


def add_records(records, project=None):
    """Bulk add resolved records (from a converted document). Returns {number: item}."""
    out = {}
    with editing() as data:
        for r in records:
            it, _, _ = add_csl(data, r["csl"], project, r.get("source", "document"), raw=r.get("raw"),
                               extra={k: r[k] for k in ("checks", "reliability", "mendeley_id") if r.get(k)})
            out[r["number"]] = it
    return out


# --------------------------------------------------------------------------
# Import: RIS, BibTeX, CSL-JSON (Zotero and Mendeley export all three)
# --------------------------------------------------------------------------

RIS_TO_CSL = {"JOUR": "article-journal", "JFULL": "article-journal", "MGZN": "article-magazine",
              "NEWS": "article-newspaper", "BOOK": "book", "EBOOK": "book", "EDBOOK": "book",
              "CHAP": "chapter", "ECHAP": "chapter", "CONF": "paper-conference", "CPAPER": "paper-conference",
              "THES": "thesis", "RPRT": "report", "ELEC": "webpage", "WEB": "webpage", "DATA": "dataset",
              "PAT": "patent", "STAT": "legislation", "BILL": "bill", "GEN": "article"}


def _person(name):
    name = name.strip().strip(",")
    if not name:
        return None
    if "," in name:
        fam, giv = name.split(",", 1)
        return {"family": fam.strip(), "given": giv.strip()}
    p = core.parse_name(name)
    return p if p and p.get("family") else {"literal": name}


def parse_ris(text):
    items, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])  -\s?(.*)$", line.strip("\ufeff"))
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"type": RIS_TO_CSL.get(val, "article"), "author": [], "editor": []}
            continue
        if cur is None:
            continue
        if tag == "ER":
            items.append(cur)
            cur = None
        elif tag in ("AU", "A1") and _person(val):
            cur["author"].append(_person(val))
        elif tag in ("ED", "A2", "A3") and _person(val):
            cur["editor"].append(_person(val))
        elif tag in ("TI", "T1", "CT") and not cur.get("title"):
            cur["title"] = val
        elif tag in ("T2", "JO", "JF", "BT") and not cur.get("container-title"):
            cur["container-title"] = val
        elif tag == "JA" and not cur.get("container-title"):
            cur["container-title"] = val
        elif tag in ("PY", "Y1", "DA") and "issued" not in cur:
            y = re.search(r"\d{4}", val)
            if y:
                cur["issued"] = {"date-parts": [[int(y.group(0))]]}
        elif tag == "VL":
            cur["volume"] = val
        elif tag in ("IS", "CP"):
            cur["issue"] = val
        elif tag == "SP":
            cur["page"] = val
        elif tag == "EP" and cur.get("page"):
            cur["page"] += "-" + val
        elif tag in ("DO",):
            cur["DOI"] = core.norm_doi(val)
        elif tag in ("UR", "L2") and not cur.get("URL"):
            cur["URL"] = val
        elif tag == "PB":
            cur["publisher"] = val
        elif tag == "CY":
            cur["publisher-place"] = val
        elif tag == "SN":
            cur["ISBN" if re.sub(r"[^0-9Xx]", "", val).__len__() in (10, 13) else "ISSN"] = val
        elif tag == "ET":
            cur["edition"] = val
        elif tag == "AB":
            cur["abstract"] = val
        elif tag == "N1" and not cur.get("DOI"):
            d = core.DOI_RE.search(val)
            if d:
                cur["DOI"] = core.norm_doi(d.group(1))
    return [{k: v for k, v in c.items() if v} for c in items]


BIB_TO_CSL = {"article": "article-journal", "book": "book", "inbook": "chapter", "incollection": "chapter",
              "inproceedings": "paper-conference", "conference": "paper-conference", "phdthesis": "thesis",
              "mastersthesis": "thesis", "techreport": "report", "online": "webpage", "misc": "article",
              "unpublished": "manuscript", "proceedings": "book", "manual": "book", "dataset": "dataset"}
LATEX = {r"\"o": "ö", r"\"a": "ä", r"\"u": "ü", r"\"O": "Ö", r"\"A": "Ä", r"\"U": "Ü", r"'e": "é", r"'a": "á",
         r"'i": "í", r"'o": "ó", r"'u": "ú", r"`e": "è", r"`a": "à", r"^e": "ê", r"~n": "ñ", r"c c": "ç",
         r"ss": "ß", r"o": "ø", r"aa": "å", r"l": "ł", r"'c": "ć", r"'s": "ś", r"'n": "ń", r"'z": "ź"}


def _delatex(s):
    s = re.sub(r"\{\\([^{}]+?)\}", lambda m: LATEX.get(m.group(1).replace("{", "").replace("}", ""), m.group(1)), s)
    s = re.sub(r"\\([\"'`^~])\{?([A-Za-z])\}?", lambda m: LATEX.get(m.group(1) + m.group(2), m.group(2)), s)
    s = s.replace("\\&", "&").replace("--", "–").replace("~", " ")
    return re.sub(r"\s+", " ", s.replace("{", "").replace("}", "")).strip()


def parse_bibtex(text):
    items, i = [], 0
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", text):
        etype = m.group(1).lower()
        if etype in ("comment", "string", "preamble"):
            continue
        pos, depth, fields, start = m.end(), 1, {}, m.end()
        while pos < len(text) and depth:  # find the closing brace of the entry
            depth += {"{": 1, "}": -1}.get(text[pos], 0)
            pos += 1
        body = text[start:pos - 1]
        for fm in re.finditer(r"(\w[\w\-]*)\s*=\s*", body):
            j, val = fm.end(), ""
            if j < len(body) and body[j] == "{":
                d, k = 1, j + 1
                while k < len(body) and d:
                    d += {"{": 1, "}": -1}.get(body[k], 0)
                    k += 1
                val = body[j + 1:k - 1]
            elif j < len(body) and body[j] == '"':
                k = body.find('"', j + 1)
                val = body[j + 1:k]
            else:
                val = re.match(r"[^,\n]*", body[j:]).group(0)
            fields.setdefault(fm.group(1).lower(), _delatex(val))
        c = {"type": BIB_TO_CSL.get(etype, "article"), "title": fields.get("title"),
             "container-title": fields.get("journal") or fields.get("journaltitle") or fields.get("booktitle"),
             "volume": fields.get("volume"), "issue": fields.get("number") or fields.get("issue"),
             "page": (fields.get("pages") or "").replace("–", "-"), "DOI": core.norm_doi(fields.get("doi")),
             "URL": fields.get("url"), "publisher": fields.get("publisher") or fields.get("institution")
             or fields.get("school"), "ISBN": fields.get("isbn"), "ISSN": fields.get("issn"),
             "abstract": fields.get("abstract")}
        for role in ("author", "editor"):
            if fields.get(role):
                c[role] = [p for p in (_person(n) for n in re.split(r"\s+and\s+", fields[role])) if p]
        y = re.search(r"\d{4}", fields.get("year") or fields.get("date") or "")
        if y:
            c["issued"] = {"date-parts": [[int(y.group(0))]]}
        c = {k: v for k, v in c.items() if v}
        c["_key"], c["_raw"] = m.group(2), text[m.start():pos]  # for LaTeX checks; dropped on import
        items.append(c)
        i += 1
    return items


def parse_any(text, filename=""):
    name = filename.lower()
    t = text.lstrip("\ufeff \n\r\t")
    if name.endswith(".json") or t.startswith("[") or t.startswith("{"):
        data = json.loads(t)
        if isinstance(data, dict):
            data = data.get("items", [data])
        return [it.get("csl", it) for it in data]  # also accepts a Citrello library file
    if name.endswith(".bib") or t.startswith("@"):
        return parse_bibtex(text)
    if re.search(r"^TY  -", t, re.M):
        return parse_ris(text)
    raise ValueError("Unrecognised file. Use RIS (.ris), BibTeX (.bib) or CSL-JSON (.json).")


def import_items(cslist, project=None, source="import", extra_key=None, keymap=None):
    """Add many CSL items, skipping duplicates. Returns (added, skipped).
    BibTeX keys are kept when free; `keymap` (a dict) collects {bib key: library key} where they differ."""
    added = skipped = 0
    with editing() as data:
        index = _index(data["items"])  # built once, updated as items are added
        for c in cslist:
            extra = {}
            if extra_key and c.get("_id"):
                extra[extra_key] = c["_id"]
            want = c.get("_key")
            more = [p for p in (c.get("_projects") or []) if p]  # e.g. the item's Zotero collections
            c = {k: v for k, v in c.items() if not k.startswith("_")}
            rec = {"number": 0, "raw": "", "source": source, "csl": c, "typed": {}}
            try:
                core.assess_references([rec])  # offline checks: DOI, date, author, web page, preprint
                extra.update(checks=rec["checks"], reliability=rec["reliability"])
            except Exception:
                extra.update(checks=[], reliability="check")
            try:
                it, created, _ = add_csl(data, c, project, source, extra=extra, key=want, index=index)
            except Exception as e:  # one odd reference (no title, unusual data) never stops a whole import
                if not isinstance(e, ValueError):
                    print(f"Skipped a reference that couldn't be read: {e!r}", file=sys.stderr)
                skipped += 1
                continue
            if keymap is not None and want and it["key"] != want:
                keymap[want] = it["key"]
            for p in more:
                if p not in it.setdefault("projects", []):
                    it["projects"].append(p)
                if p not in data["projects"]:
                    data["projects"].append(p)
            added += created
            skipped += not created
    return added, skipped


# --------------------------------------------------------------------------
# Accounts: Mendeley (via citrello's connector) and Zotero (web API key)
# --------------------------------------------------------------------------

def mendeley_to_csl(d):
    types = {v: k for k, v in core.MENDELEY_TYPES.items()}
    c = {"type": types.get(d.get("type"), "article"), "title": d.get("title"),
         "container-title": d.get("source"), "volume": d.get("volume"), "issue": d.get("issue"),
         "page": d.get("pages"), "publisher": d.get("publisher"), "publisher-place": d.get("city"),
         "DOI": (d.get("identifiers") or {}).get("doi"), "ISBN": (d.get("identifiers") or {}).get("isbn"),
         "ISSN": (d.get("identifiers") or {}).get("issn"), "URL": (d.get("websites") or [None])[0],
         "_id": d.get("id")}
    for role, key in (("author", "authors"), ("editor", "editors")):
        if d.get(key):
            c[role] = [{"family": p.get("last_name", ""), "given": p.get("first_name", "")} for p in d[key]]
    if d.get("year"):
        c["issued"] = {"date-parts": [[int(d["year"])]]}
    return {k: v for k, v in c.items() if v}


def import_from_mendeley(project=None):
    items, url = [], "/documents?limit=500&view=bib"
    while url:
        docs, headers = core.mendeley_call(url, media=core.DOC_MEDIA)
        items += [mendeley_to_csl(d) for d in docs]
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", "") or "")
        url = nxt.group(1) if nxt else None
    return import_items(items, project, "mendeley", extra_key="mendeley_id")


def export_to_mendeley(items):
    """Add library items missing from the Mendeley account. Returns (added, already_there)."""
    import contextlib as _c
    import io
    with _c.redirect_stdout(io.StringIO()):
        idx = core.library_index()
    added = present = 0
    with editing() as data:
        for it in data["items"]:
            if it["id"] not in {i["id"] for i in items}:
                continue
            hit, _ = idx.find(it["csl"])
            if hit:
                it["mendeley_id"] = hit
                present += 1
                continue
            doc, _ = core.mendeley_call("/documents", "POST", core.csl_to_mendeley(it["csl"]),
                                           core.DOC_MEDIA)
            it["mendeley_id"] = doc["id"]
            idx.add(doc["id"], it["csl"].get("title"), it["csl"].get("DOI"), core.year_of(it["csl"]))
            added += 1
    return added, present


ZOTERO_API = os.environ.get("CITRELLO_ZOTERO_API", "https://api.zotero.org")


def _zotero_get(url, api_key, tries=6):
    """One page from Zotero's web API, waiting when Zotero asks to slow down (429/503, Backoff, Retry-After)."""
    wait = 2
    for attempt in range(tries):
        try:
            res, headers = core.http_json(url, {"Zotero-API-Key": api_key, "Zotero-API-Version": "3"}, timeout=60)
            backoff = headers.get("Backoff")
            if backoff and backoff.isdigit():
                time.sleep(min(int(backoff), 30))
            return res, headers
        except urllib.error.HTTPError as e:
            if e.code in (403, 401):
                raise ValueError("Zotero refused the API key. Create a key at zotero.org/settings/keys with "
                                 "\u201cAllow library access\u201d ticked, then paste it in Settings \u2192 Zotero.")
            if e.code == 404:
                raise ValueError("Zotero didn't find that user ID. Use the number shown as \u201cYour user ID for use in "
                                 "API calls\u201d at zotero.org/settings/keys (not your username).")
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                retry = e.headers.get("Retry-After") if e.headers else None
                time.sleep(min(int(retry), 60) if retry and retry.isdigit() else wait)
                wait = min(wait * 2, 30)
                continue
            raise ValueError(f"Zotero answered with an error ({e.code}). Please try again in a few minutes.")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt < tries - 1:
                time.sleep(wait)
                wait = min(wait * 2, 30)
                continue
            raise ValueError(f"Couldn't reach Zotero ({getattr(e, 'reason', e)}). Check your internet connection.")


def import_from_zotero(user_id, api_key, project=None):
    """Zotero web library -> Citrello (key from zotero.org/settings/keys, read access). Any size: 100 per page."""
    user_id = str(user_id).strip()
    items, start, total = [], 0, None
    while True:
        url = f"{ZOTERO_API}/users/{urllib.parse.quote(user_id)}/items/top?format=json&include=csljson&limit=100&start={start}"
        res, headers = _zotero_get(url, api_key)
        batch = res if isinstance(res, list) else (res or {}).get("items", [])
        for entry in batch:
            data = entry.get("data") or {}
            if data.get("itemType") in ("attachment", "note", "annotation"):
                continue  # a PDF or note on its own, not a reference
            c = dict(entry.get("csljson") or {})
            if not c:
                continue
            c["_id"] = entry.get("key") or (c.get("id") or "").split("/")[-1]
            c.pop("id", None)
            items.append(c)
        if total is None:
            try:
                total = int(headers.get("Total-Results") or 0)
            except ValueError:
                total = 0
        start += 100
        if not batch or start >= total:
            break
    return import_items(items, project, "zotero", extra_key="zotero_key")


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def export(items, fmt, path):
    recs = [{"number": i + 1, "csl": it["csl"]} for i, it in enumerate(items)]
    if fmt == "ris":
        core.write_ris(recs, path)
    elif fmt in ("bib", "biblatex"):
        import latex  # clean BibTeX/biblatex with Citrello's keys, so [@Key] and \cite{Key} both work
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(latex.bibtex(items, "biblatex" if fmt == "biblatex" else "bibtex"))
    elif fmt == "json":
        out = []
        for it in items:
            c = dict(it["csl"])
            c["id"] = it["key"]
            out.append(c)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    else:
        raise ValueError("Format must be ris, bib, biblatex or json")
    return path


def delete(keys):
    keys = {k.lstrip("@") for k in keys}
    with editing() as data:
        before = len(data["items"])
        data["items"] = [it for it in data["items"] if it["key"] not in keys and it["id"] not in keys]
        return before - len(data["items"])


def set_projects(key, projects):
    with editing() as data:
        it = get(data, key)
        if not it:
            raise KeyError(key)
        it["projects"] = sorted({p for p in projects if p})
        for p in it["projects"]:
            if p not in data["projects"]:
                data["projects"].append(p)
        return it


def update_item(key, csl):
    with editing() as data:
        it = get(data, key)
        if not it:
            raise KeyError(key)
        it["csl"] = core.trim_csl(csl)
        return it


# --------------------------------------------------------------------------
# Verify online: check library items against Crossref
# --------------------------------------------------------------------------

CORE_FIELDS = ("type", "title", "author", "editor", "container-title", "volume", "issue", "page",
               "issued", "DOI", "publisher", "ISSN")
DIFF_FIELDS = (("title", "title"), ("author", "authors"), ("container-title", "journal/book"),
               ("volume", "volume"), ("issue", "issue"), ("page", "pages"), ("issued", "year"), ("DOI", "DOI"))


def _as_text(c):
    """A library item written out as a reference, for Crossref's search and the checks."""
    names = ", ".join(a.get("literal") or f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                      for a in c.get("author", [])[:6])
    parts = [f"{names} ({core.year_of(c) or 'n.d.'})." if names else f"({core.year_of(c) or 'n.d.'}).",
             c.get("title", "") + ".", c.get("container-title") or c.get("publisher") or ""]
    for k in ("volume", "issue", "page"):
        if c.get(k):
            parts.append(str(c[k]))
    if c.get("DOI"):
        parts.append("doi:" + c["DOI"])
    return " ".join(p for p in parts if p)


def _show(field, v):
    if field == "author":
        return f"{len(v)} author(s)" if v else "none"
    if field == "issued":
        try:
            return str(v["date-parts"][0][0])
        except (KeyError, IndexError, TypeError):
            return "none"
    return str(v) if v else "none"


def verify_item(it, email=None):
    """Check one library item online. Returns (new_csl, checks, reliability, changes, found_online)."""
    old = dict(it["csl"])
    raw = _as_text(old)
    csl, source, meta = core.crossref_lookup(raw, {k: old[k] for k in ("title", "DOI") if old.get(k)}, email)
    if meta.get("error"):
        raise ConnectionError(meta["error"])  # leave the item as it is
    rec = {"number": 0, "raw": raw, "source": source or "library", "csl": csl or old, "typed": old, "meta": meta}
    core.assess_references([rec])
    new = rec["csl"]
    if source and new is not old:  # the publisher record is the authority for the bibliographic core...
        merged = dict(old)
        merged.update({k: v for k, v in new.items() if v})
        # ...but keep what it does not have (article numbers, "SSRN Electronic Journal", ...) and
        # don't shorten "8214-8229" to "8214"
        op, np_ = str(old.get("page") or ""), str(new.get("page") or "")
        if op and np_ and "-" not in np_ and re.match(re.escape(np_) + r"\s*[-–]", op):
            merged["page"] = old["page"]
        new = core.trim_csl(merged)
    else:
        new = dict(old)
        if meta.get("doi_cleaned"):
            new["DOI"] = meta["doi_cleaned"][1]
    def same_text(x, y):  # ignore case, accents, hyphen/dash variants and punctuation
        f = lambda t: re.sub(r"[\W_]+", " ", "".join(ch for ch in unicodedata.normalize("NFKD", t or "")
                                                     if not unicodedata.combining(ch)).lower()).strip()
        return f(x) == f(y)

    changes = []
    for field, label in DIFF_FIELDS:
        a, b = _show(field, old.get(field)), _show(field, new.get(field))
        if field == "DOI":
            if core.norm_doi(old.get("DOI")) != core.norm_doi(new.get("DOI")):
                changes.append(f"{label}: {a} → {b}")
            continue
        if field == "author" and old.get(field) and new.get(field):
            a = ", ".join(x.get("family") or x.get("literal", "") for x in old[field][:3])
            b = ", ".join(x.get("family") or x.get("literal", "") for x in new[field][:3])
        if field == "title":
            if not same_text(a, b):
                changes.append(f"{label}: “{a[:60]}” → “{b[:60]}”")
        elif field in ("container-title", "page") and same_text(a, b):
            continue
        elif a != b:
            changes.append(f"{label}: {a} → {b}")
    checks = [c for c in rec["checks"] if c["text"] != "Not found online; the details were read from your text"]
    if not source:
        checks.insert(0, {"level": "check", "text": "Not found on Crossref (common for web pages, reports and books)"})
    reliability = ("problem" if any(c["level"] == "problem" for c in checks)
                   else "check" if checks else "verified" if source else "complete")
    return new, checks, reliability, changes, bool(source)


def repair_verified():
    """Earlier versions of verify dropped details the publisher record lacked and shortened page ranges.
    Restore them from the copy kept before verification. Returns how many references were repaired."""
    fixed = 0
    with editing() as data:
        for it in data["items"]:
            prev, cur = it.get("previous_csl"), it["csl"]
            if not prev:
                continue
            changed = False
            for k, v in prev.items():
                if v and not cur.get(k):
                    cur[k] = v
                    changed = True
            op, cp = str(prev.get("page") or ""), str(cur.get("page") or "")
            if op and cp and "-" not in cp and re.match(re.escape(cp) + r"\s*[-–]", op):
                cur["page"] = prev["page"]
                changed = True
            fixed += changed
    return fixed


def verify(keys=None, include_verified=False, email=None, progress=None):
    """Verify library items (all, or the given keys). Saves after each item.

    Returns {"checked", "verified", "check", "problem", "not_found", "changed": [{key, changes}]}.
    """
    repaired = repair_verified()
    data = load()
    items = [it for it in data["items"] if (not keys or it["key"] in keys)
             and (include_verified or it.get("reliability") != "verified")]
    out = {"checked": 0, "verified": 0, "check": 0, "problem": 0, "not_found": 0, "changed": [], "problems": [],
           "to_check": len(items), "stopped": None, "repaired": repaired}
    failures = 0
    for i, it in enumerate(items, 1):
        if progress:
            progress(i, len(items), it["key"])
        try:
            new, checks, rel, changes, found = verify_item(it, email)
        except ConnectionError as e:
            failures += 1
            if failures >= 2 and out["checked"] == 0 or failures >= 5:
                out["stopped"] = str(e) + ". Nothing was changed for the remaining references; try again later."
                break
            out["problems"].append({"key": it["key"], "error": str(e)})
            continue
        except Exception as e:  # anything else: report and carry on
            out["problems"].append({"key": it["key"], "error": str(e)})
            continue
        failures = 0
        with editing() as d:
            cur = get(d, it["id"])
            if cur:
                if changes:
                    cur.setdefault("previous_csl", cur["csl"])
                cur.update(csl=new, checks=checks, reliability=rel, checked_online=_now())
        out["checked"] += 1
        out[rel if rel in ("verified", "check", "problem") else "check"] += 1
        out["not_found"] += not found
        if changes:
            out["changed"].append({"key": it["key"], "changes": changes})
        if rel == "problem":
            out["problems"].append({"key": it["key"], "issues": [c["text"] for c in checks if c["level"] == "problem"]})
        time.sleep(0.05)  # be gentle with Crossref
    return out
