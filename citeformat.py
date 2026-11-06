"""
Formatting and live Word documents for Citrello.

- Citation styles: the open CSL style repository (~10,000 journal styles, the same
  ones Zotero and Mendeley use). Common styles ship with Citrello; any other is
  downloaded by its id the first time it is used (e.g. "elsevier-harvard").
- Markers: write citations as [@Topham2017], [@Topham2017; @Vetters2024, p. 5],
  [see @Smith2019] or [-@Smith2019] (year only, after "Smith (2019)").
- update_document(): markers -> live citation fields, every citation and the
  bibliography re-rendered in the chosen style.
- create_document(): text with markers (light Markdown) -> new .docx with live citations.
"""
import copy
import glob
import html
import json
import os
import re
import sys
import urllib.request
import warnings
from html.parser import HTMLParser

import docx
from docx.oxml.ns import qn

import library
import citrello_core as core

warnings.filterwarnings("ignore", module="citeproc")
from citeproc import (Citation, CitationItem, CitationStylesBibliography,  # noqa: E402
                      CitationStylesStyle, formatter)
from citeproc.source import Locator  # noqa: E402
from citeproc.source.json import CiteProcJSON  # noqa: E402

STYLE_REPO = "https://raw.githubusercontent.com/citation-style-language/styles/master/"
DEFAULT_STYLE = "apa"


# --------------------------------------------------------------------------
# Styles
# --------------------------------------------------------------------------

def _bundled_dir():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "styles")


def _user_dir():
    return os.path.join(library.HOME, "styles")


def style_info(path):
    head = open(path, encoding="utf-8").read(6000)
    title = re.search(r"<title>([^<]+)</title>", head)
    fmt = re.search(r'citation-format="([^"]+)"', head)
    return {"id": os.path.basename(path)[:-4], "title": html.unescape(title.group(1)) if title else "",
            "format": fmt.group(1) if fmt else "author-date"}


def list_styles():
    seen, out = set(), []
    for d in (_user_dir(), _bundled_dir()):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".csl") and f not in seen:
                    seen.add(f)
                    info = style_info(os.path.join(d, f))
                    if info["format"] != "note":  # footnote styles are not supported
                        out.append(info)
    return sorted(out, key=lambda s: s["title"].lower())


_CATALOG = None


def _fold(s):
    """Lowercase, no accents, '&' as 'and', punctuation as spaces: for matching style names."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def catalog():
    """Every style in the CSL repository (styles/catalog.json.gz, built by packaging/build_style_catalog.py):
    [{"id", "title", "short", "format"}]. Empty if the file is missing."""
    global _CATALOG
    if _CATALOG is None:
        import gzip
        try:
            with gzip.open(os.path.join(_bundled_dir(), "catalog.json.gz"), "rt", encoding="utf-8") as f:
                raw = json.load(f)
            cols = raw["columns"]
            _CATALOG = [dict(zip(cols, row)) for row in raw["styles"]]
            for s in _CATALOG:
                s["_t"], s["_s"], s["_i"] = _fold(s["title"]), _fold(s["short"]), s["id"].replace("-", " ")
        except (OSError, ValueError, KeyError):
            _CATALOG = []
    return _CATALOG


def search_styles(query, limit=40):
    """Styles whose name matches every word of the query (a journal name, an abbreviation such as "IEEE" or
    "J Clin Oncol", or a style id). Installed styles first, then the closest names."""
    q = _fold(query)
    if not q:
        return []
    words = q.split()
    installed = {s["id"] for s in list_styles()}
    out = []
    for s in catalog():
        hay = (s["_t"] + " " + s["_s"] + " " + s["_i"]).split()
        if not all(any(h.startswith(w) for h in hay) for w in words):
            continue
        t = s["_t"]
        score = (0 if s["id"] in installed else 1,
                 0 if q in (t, s["_s"], s["_i"]) else 1 if t.startswith(q) or s["_s"].startswith(q) else
                 2 if (" " + q) in (" " + t) else 3,
                 s["format"] == "note", len(t))
        out.append((score, s))
    out.sort(key=lambda x: x[0])
    return [{"id": s["id"], "title": s["title"], "short": s["short"], "format": s["format"],
             "installed": s["id"] in installed, "usable": s["format"] != "note"} for _, s in out[:limit]]


def style_path(style_id):
    """Local path of a style, downloading it (and its parent) if needed."""
    style_id = re.sub(r"[^a-z0-9\-]", "", (style_id or DEFAULT_STYLE).lower().strip())
    for d in (_user_dir(), _bundled_dir()):
        p = os.path.join(d, style_id + ".csl")
        if os.path.exists(p):
            return _independent(p)
    os.makedirs(_user_dir(), exist_ok=True)
    p = os.path.join(_user_dir(), style_id + ".csl")
    for sub in ("", "dependent/"):
        try:
            req = urllib.request.Request(STYLE_REPO + sub + style_id + ".csl",
                                         headers={"User-Agent": "citrello/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            with open(p, "wb") as f:
                f.write(data)
            return _independent(p)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
    raise ValueError(f"Unknown citation style '{style_id}'. Find style ids at https://www.zotero.org/styles")


_SAMPLE = {"type": "article-journal", "title": "A sample article", "container-title": "Journal of Examples",
           "author": [{"family": "Smith", "given": "Jane"}, {"family": "Lee", "given": "Kim"}],
           "issued": {"date-parts": [[2020]]}, "volume": "3", "issue": "2", "page": "10-20", "DOI": "10.1000/xyz"}


def install_style(style_id):
    """Download a style (once) and make sure Citrello can format with it. Returns its info, with "id" being the
    journal's own id and name (a journal alias keeps its name; the rules come from its parent style).
    Raises ValueError with a plain message otherwise."""
    sid = re.sub(r"[^a-z0-9\-]", "", (style_id or "").lower().strip())
    path = style_path(sid)
    info = style_info(path)
    for d in (_user_dir(), _bundled_dir()):
        own = os.path.join(d, sid + ".csl")
        if os.path.exists(own) and own != path:
            mine = style_info(own)
            info = dict(mine, format="note" if mine["format"] == "note" else info["format"])
            break
    try:
        if info["format"] == "note":
            raise ValueError("Footnote styles are not supported yet.")
        try:
            r = Renderer(sid, {"sample": _SAMPLE})
            r.render(r.add([{"key": "sample"}]))
            r.bibliography()
        except Exception:
            raise ValueError(f"Citrello can't format with “{info['title']}” yet. Pick a similar style, such as "
                             "the publisher's general one.")
    except ValueError:
        own = os.path.join(_user_dir(), sid + ".csl")  # don't leave an unusable style in the list
        if os.path.exists(own):
            os.remove(own)
        raise
    return info


def _independent(path):
    """Dependent styles (journal aliases) point to a parent style that holds the rules."""
    head = open(path, encoding="utf-8").read(6000)
    m = re.search(r'<link[^>]*href="https?://www\.zotero\.org/styles/([^"]+)"[^>]*rel="independent-parent"', head) \
        or re.search(r'<link[^>]*rel="independent-parent"[^>]*href="https?://www\.zotero\.org/styles/([^"]+)"', head)
    return style_path(m.group(1)) if m else path


# --------------------------------------------------------------------------
# Rendering with citeproc
# --------------------------------------------------------------------------

LOC_LABELS = {"page", "chapter", "section", "figure", "table", "paragraph", "volume", "line", "note", "verse"}


def _csl_for_citeproc(csl, key):
    c = {k: v for k, v in csl.items() if v}
    c["id"] = key
    if c.get("DOI") and re.search(r"doi\.org/", c.get("URL", ""), re.I):
        c.pop("URL")  # the style prints the DOI; a doi.org URL would repeat it
    c.setdefault("type", "article")
    if c.get("type") == "article" and c.get("container-title"):
        c["type"] = "article-journal"
    for role in ("author", "editor"):
        if role in c:
            c[role] = [{k: v for k, v in p.items() if k in ("family", "given", "literal")} for p in c[role]]
    return c


class Renderer:
    """Render citation groups and a bibliography in one style."""

    def __init__(self, style_id, items):
        self.path = style_path(style_id)
        self.info = style_info(self.path)
        self.style = CitationStylesStyle(self.path, validate=False)
        self.source = CiteProcJSON([_csl_for_citeproc(c, k) for k, c in items.items()])
        self.bib = CitationStylesBibliography(self.style, self.source, formatter.html)
        self.groups = []

    def add(self, group):
        """group: [{"key", "locator", "label", "prefix", "suppress"}] -> index"""
        cites = []
        for it in group:
            args = {}
            if it.get("locator"):
                label = it.get("label") if it.get("label") in LOC_LABELS else "page"
                args["locator"] = Locator(label, str(it["locator"]))
            if it.get("prefix"):
                args["prefix"] = it["prefix"].rstrip() + " "
            cites.append(CitationItem(it["key"].lower(), **args))
        c = Citation(cites)
        self.bib.register(c)
        self.groups.append((c, group))
        return len(self.groups) - 1

    def render(self, index):
        c, group = self.groups[index]
        out = str(self.bib.cite(c, lambda item: None))
        if self.info["format"] in ("author-date", "author") and group and all(g.get("suppress") for g in group):
            # "Smith et al. (2020)": the names are in the sentence, the field shows the year
            years = re.findall(r"\b(\d{4}[a-z]?|n\.d\.)", re.sub(r"<[^>]+>", "", out))
            if years:
                loc = group[0].get("locator")
                out = "(" + ", ".join(years) + (f", p. {loc}" if loc else "") + ")"
        return out

    def bibliography(self):
        entries = []
        self.bib.sort()  # the style's own order: alphabetical (author-date) or by first citation (numeric)
        for e in self.bib.bibliography():
            s = str(e)
            s = re.sub(r"^(\[\d+\]|\(\d+\)|\d+\.)(?=\S)", r"\1 ", s)  # "[1]Title" -> "[1] Title"
            s = re.sub(r"^<sup>(\d+)</sup>(?=\S)", r"\1. ", s)
            entries.append(s)
        return entries


# --------------------------------------------------------------------------
# HTML snippets (citeproc output) -> Word runs
# --------------------------------------------------------------------------

class _Runs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts = [], []

    def handle_starttag(self, tag, attrs):
        style = dict(attrs).get("style", "")
        self.stack.append("smallcaps" if "small-caps" in style else tag)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if data:
            self.parts.append((data, set(self.stack)))


def html_runs(snippet, rpr=None):
    p = _Runs()
    p.feed(snippet)
    runs = []
    for text, fmt in p.parts:
        r = core.OxmlElement("w:r")
        props = copy.deepcopy(rpr) if rpr is not None else core.OxmlElement("w:rPr")
        for tag, el in (("i", "w:i"), ("em", "w:i"), ("b", "w:b"), ("strong", "w:b"), ("smallcaps", "w:smallCaps")):
            if tag in fmt and props.find(qn(el)) is None:
                props.append(core.OxmlElement(el))
        if "sup" in fmt or "sub" in fmt:
            for old in props.findall(qn("w:vertAlign")):
                props.remove(old)
            va = core.OxmlElement("w:vertAlign")
            va.set(qn("w:val"), "superscript" if "sup" in fmt else "subscript")
            props.append(va)
        if len(props):
            r.append(props)
        r.append(core.text_el("w:t", text))
        runs.append(r)
    return runs


# --------------------------------------------------------------------------
# Fields in a document
# --------------------------------------------------------------------------

def _kind(r):
    fc = r.find(qn("w:fldChar"))
    return fc.get(qn("w:fldCharType")) if fc is not None else None


def find_fields(body):
    """All complex fields: [{begin, separate, end, instr, result: [runs]}] in document order."""
    fields, stack = [], []
    for r in body.iter(qn("w:r")):
        k = _kind(r)
        if k == "begin":
            stack.append({"begin": r, "separate": None, "end": None, "instr": "", "result": []})
            continue
        if not stack:
            continue
        top = stack[-1]
        if k == "separate":
            top["separate"] = r
        elif k == "end":
            top["end"] = r
            fields.append(stack.pop())
        elif top["separate"] is None:
            top["instr"] += "".join(t.text or "" for t in r.findall(qn("w:instrText")))
        elif len(stack) == 1:
            top["result"].append(r)
    order = {id(r): i for i, r in enumerate(body.iter(qn("w:r")))}
    fields.sort(key=lambda f: order[id(f["begin"])])
    return fields


def citation_payload(instr):
    m = re.search(r"CSL_CITATION\s+(\{.*\})\s*$", instr, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


MARKER_RE = re.compile(r"\[((?:[^\[\]]*?-?@[A-Za-z0-9_][\w:.#$%&+?<>~/\-]*)(?:[^\[\]]*))\]")
MARKER_PART_RE = re.compile(r"^\s*(?P<prefix>[^@]*?)\s*(?P<suppress>-)?@(?P<key>[A-Za-z0-9_][\w:.#$%&+?<>~/\-]*?)"
                            r"(?:\s*,\s*(?P<loc>.+?))?\s*$")


def parse_marker(inner):
    group = []
    for part in inner.split(";"):
        m = MARKER_PART_RE.match(part)
        if not m:
            return None
        it = {"key": m.group("key"), "prefix": m.group("prefix").strip(), "suppress": bool(m.group("suppress"))}
        if m.group("loc"):
            lm = core.LOC_RE.search(", " + m.group("loc"))
            if lm:
                it["label"], it["locator"] = core.LOC_LABEL[lm.group(1).lower()], lm.group(2)
            else:
                it["label"], it["locator"] = "page", m.group("loc").strip()
        group.append(it)
    return group


def _instr(group, items_by_key, lib_items, display):
    cits = []
    for i, g in enumerate(group, 1):
        it = lib_items.get(g["key"])
        data = dict(items_by_key[g["key"]])
        data["id"] = f"ITEM-{i}"
        mid = (it or {}).get("mendeley_id") or core.uuid.uuid5(core.uuid.NAMESPACE_URL, "citrello:" + g["key"])
        ci = {"id": f"ITEM-{i}", "itemData": data, "uris": [f"http://www.mendeley.com/documents/?uuid={mid}"],
              "citrello-key": g["key"]}
        if g.get("prefix"):
            ci["prefix"] = g["prefix"]
        if g.get("locator"):
            ci["locator"], ci["label"] = g["locator"], g.get("label", "page")
        if g.get("suppress"):
            ci["suppress-author"] = 1
        cits.append(ci)
    payload = {"citationItems": cits,
               "mendeley": {"formattedCitation": display, "plainTextFormattedCitation": display,
                            "previouslyFormattedCitation": display},
               "properties": {"noteIndex": 0},
               "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"}
    return "ADDIN CSL_CITATION " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def convert_markers(body, data, report):
    """Replace [@Key] markers with citation fields. Returns number converted."""
    lib_items = {it["key"]: it for it in data["items"]}
    lower = {k.lower(): k for k in lib_items}
    count = 0
    for p in list(body.iter(qn("w:p"))):
        segs = core.segments(p)
        full = "".join(s[3] for s in segs)
        if "@" not in full:
            continue
        for m in reversed(list(MARKER_RE.finditer(full))):
            group = parse_marker(m.group(1))
            if not group:
                continue
            missing = [g["key"] for g in group if g["key"].lower() not in lower]
            if missing:
                report.append(f"Unknown reference key(s) {', '.join('@' + k for k in missing)} in {m.group(0)!r}: "
                              "add them to the library first")
                continue
            for g in group:
                g["key"] = lower[g["key"].lower()]
            s, e = m.span()
            covered = [sg for sg in segs if sg[1] < e and sg[2] > s]
            if not covered or any(sg[0] is None for sg in covered) or len({sg[0].getparent() for sg in covered}) > 1:
                report.append(f"Could not convert {m.group(0)!r} (inside another field)")
                continue
            first, last = covered[0], covered[-1]
            runs = [sg[0] for sg in covered]
            if last[2] > e:
                core.split_run(last[0], e - last[1])
            if first[1] < s:
                right = core.split_run(first[0], s - first[1])
                runs = [right] if first[0] is last[0] else [right] + runs[1:]
            rpr = runs[0].find(qn("w:rPr"))
            instr = _instr(group, {k: it["csl"] for k, it in lib_items.items()}, lib_items, m.group(0))
            for fr in core.field_runs(rpr, instr, m.group(0)):
                runs[0].addprevious(fr)
            for r in runs:
                r.getparent().remove(r)
            count += 1
    return count


def _match_library(data):
    idx = core.LibraryIndex()
    by_id = {}
    for it in data["items"]:
        idx.add(it["id"], it["csl"].get("title"), it["csl"].get("DOI"), core.year_of(it["csl"]))
        by_id[it["id"]] = it
    return idx, by_id


def _set_result(field, runs):
    old = [r for r in field["result"] if r.find(qn("w:t")) is not None]
    anchor = field["end"]
    for r in runs:
        anchor.addprevious(r)
    for r in old:
        if r.getparent() is not None:
            r.getparent().remove(r)


def update_document(path, out, style_id=None, data=None, add_missing=True):
    """Make every citation live and render citations + bibliography in a style.

    Returns {"citations", "markers", "entries", "style", "report", "keys"}.
    """
    data = data or library.load()
    document = docx.Document(path)
    body = document.element.body
    report = []
    markers = convert_markers(body, data, report)

    removed = remove_emptied_citations(body)
    if removed:
        report.append(f"{removed} citation(s) whose text you deleted were removed")

    idx, by_id = _match_library(data)
    items, groups, fields, new_items = {}, [], [], []
    taken = {it["key"] for it in data["items"]}
    doc_only = core.LibraryIndex()  # references cited in the document but missing from the library
    for f in find_fields(body):
        payload = citation_payload(f["instr"])
        if not payload:
            continue
        group = []
        for ci in payload.get("citationItems", []):
            key = ci.get("citrello-key") or ci.get("refcite-key")
            it = next((x for x in data["items"] if x["key"] == key), None) if key else None
            if not it:
                hit, _ = idx.find(ci.get("itemData", {}))
                it = by_id.get(hit)
            if it:
                key, csl = it["key"], it["csl"]
            else:  # cited in the document but not in the library (e.g. an old Mendeley citation)
                csl = core.trim_csl(ci.get("itemData", {}))
                key, _ = doc_only.find(csl)
                if not key:
                    key = library.make_key(csl, taken)
                    taken.add(key)
                    doc_only.add(key, csl.get("title"), csl.get("DOI"), core.year_of(csl))
                    new_items.append(csl)
            items[key] = csl
            ci["citrello-key"] = key
            group.append({"key": key, "prefix": ci.get("prefix"), "locator": ci.get("locator"),
                          "label": ci.get("label"), "suppress": bool(ci.get("suppress-author"))})
        if group:
            groups.append(group)
            fields.append((f, payload))
    if add_missing and new_items:
        library.import_items(new_items, source="document")
        report.append(f"{len(new_items)} reference(s) found only in the document were added to your library")

    renderer = Renderer(style_id or DEFAULT_STYLE, items) if items else None
    idxs = [renderer.add(g) for g in groups] if renderer else []
    for (f, payload), i in zip(fields, idxs):
        out_html = renderer.render(i)
        rpr = next((r.find(qn("w:rPr")) for r in f["result"] if r.find(qn("w:t")) is not None), None)
        if rpr is not None:  # the style decides superscript, not the old formatting
            rpr = copy.deepcopy(rpr)
            for va in rpr.findall(qn("w:vertAlign")):
                rpr.remove(va)
        prev = f["begin"].getprevious()
        prev_t = prev.find(qn("w:t")) if prev is not None and prev.tag == qn("w:r") else None
        if prev_t is not None and (prev_t.text or "").endswith((" ", "\u00a0")) and out_html[:1].isspace():
            out_html = out_html.lstrip()  # "thick [1]", not "thick  [1]" (some styles add their own space)
        _set_result(f, html_runs(out_html, rpr))
        if out_html.lstrip().startswith("<sup>"):  # superscript styles: "fast¹", not "fast ¹"
            prev = f["begin"].getprevious()
            t = prev.find(qn("w:t")) if prev is not None and prev.tag == qn("w:r") else None
            if t is not None and t.text:
                t.text = t.text.rstrip()
        plain = html.unescape(re.sub(r"<[^>]+>", "", out_html))
        payload["mendeley"] = {"formattedCitation": out_html, "plainTextFormattedCitation": plain,
                               "previouslyFormattedCitation": out_html}
        for r in list(f["begin"].itersiblings()):  # rewrite the instruction with the updated payload
            if r is f["separate"]:
                break
            if r.find(qn("w:instrText")) is not None:
                r.find(qn("w:instrText")).text = "ADDIN CSL_CITATION " + json.dumps(payload, ensure_ascii=False,
                                                                                    separators=(",", ":"))
                for extra in list(r.itersiblings()):
                    if extra is f["separate"]:
                        break
                    if extra.find(qn("w:instrText")) is not None:
                        extra.getparent().remove(extra)
                break

    entries = renderer.bibliography() if renderer else []
    _write_bibliography(document, body, entries, report)
    document.save(out)
    return {"citations": len(groups), "markers": markers, "entries": len(entries),
            "style": renderer.info if renderer else style_info(style_path(style_id or DEFAULT_STYLE)),
            "report": report, "keys": list(items)}


def remove_emptied_citations(body):
    """A citation whose visible text was deleted in Word still hides its data in the field.
    Treat it as deleted, so Update renumbers and drops it from the bibliography."""
    count = 0
    for f in find_fields(body):
        if "CSL_CITATION" not in f["instr"] or f["separate"] is None:
            continue
        shown = "".join(t.text or "" for r in f["result"] for t in r.findall(qn("w:t")))
        if shown.strip():
            continue
        if f["begin"].getparent() is not f["end"].getparent():
            continue  # spans paragraphs: leave it alone
        r, runs = f["begin"], []
        while r is not None:
            runs.append(r)
            if r is f["end"]:
                break
            r = r.getnext()
        if runs[-1] is not f["end"]:
            continue
        prev = f["begin"].getprevious()
        for r in runs:
            r.getparent().remove(r)
        nxt = prev.getnext() if prev is not None else None
        pt = prev.find(qn("w:t")) if prev is not None and prev.tag == qn("w:r") else None
        nt = nxt.find(qn("w:t")) if nxt is not None and nxt.tag == qn("w:r") else None
        if pt is not None and nt is not None and (pt.text or "").endswith(" ") and (nt.text or "")[:1] in " .,;:)":
            pt.text = pt.text.rstrip(" ")  # "thick ." -> "thick."
        count += 1
    return count


BIB_INSTR = "ADDIN Mendeley Bibliography CSL_BIBLIOGRAPHY "


def _write_bibliography(document, body, entries, report):
    bib = next((f for f in find_fields(body) if "CSL_BIBLIOGRAPHY" in f["instr"] or "ZOTERO_BIBL" in f["instr"]), None)
    template_ppr = None
    if bib:
        first_p = bib["begin"].getparent()
        last_p = bib["end"].getparent()
        while first_p.tag != qn("w:p"):
            first_p = first_p.getparent()
        while last_p.tag != qn("w:p"):
            last_p = last_p.getparent()
        template_ppr = first_p.find(qn("w:pPr"))
        anchor = first_p
        old, p = [], first_p
        while p is not None:
            old.append(p)
            if p is last_p:
                break
            p = p.getnext()
    else:
        heading = None
        for para in document.paragraphs:
            if core.BIB_HEADING_RE.match(para.text):
                heading = para
        if heading is None:
            heading = document.add_heading("References", level=1)
        anchor, old = None, []
        after = heading._p
    if not entries:
        return
    new = []
    for i, entry in enumerate(entries):
        p = core.OxmlElement("w:p")
        if template_ppr is not None:
            ppr = copy.deepcopy(template_ppr)
            for num in ppr.findall(qn("w:numPr")):  # the style numbers the list itself
                ppr.remove(num)
            p.append(ppr)
        if i == 0:
            p.append(core.make_run(None, core.fld("begin")))
            p.append(core.make_run(None, core.text_el("w:instrText", BIB_INSTR)))
            p.append(core.make_run(None, core.fld("separate")))
        for r in html_runs(entry):
            p.append(r)
        if i == len(entries) - 1:
            p.append(core.make_run(None, core.fld("end")))
        new.append(p)
    if anchor is not None:
        for p in new:
            anchor.addprevious(p)
        for p in old:
            p.getparent().remove(p)
    else:
        for p in reversed(new):
            after.addnext(p)


# --------------------------------------------------------------------------
# New documents from text with markers (what Claude writes)
# --------------------------------------------------------------------------

def _add_inline(paragraph, text):
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif (part.startswith("*") and part.endswith("*")) or (part.startswith("_") and part.endswith("_")):
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def create_document(text, out, style_id=None, title=None, data=None):
    """Light Markdown (# headings, paragraphs, - bullets, **bold**, *italic*) with [@Key] markers -> .docx"""
    document = docx.Document()
    if title:
        document.add_heading(title, level=0)
    para_lines = []

    def flush():
        if para_lines:
            _add_inline(document.add_paragraph(), " ".join(l.strip() for l in para_lines))
            para_lines.clear()

    for line in text.splitlines():
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        b = re.match(r"^\s*[-*]\s+(.*)$", line)
        n = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if h:
            flush()
            if core.BIB_HEADING_RE.match(h.group(2)):
                continue  # the bibliography is generated
            document.add_heading(h.group(2).strip(), level=len(h.group(1)))
        elif b:
            flush()
            _add_inline(document.add_paragraph(style="List Bullet"), b.group(1))
        elif n:
            flush()
            _add_inline(document.add_paragraph(style="List Number"), n.group(1))
        elif not line.strip():
            flush()
        else:
            para_lines.append(line)
    flush()
    document.add_heading("References", level=1)
    document.save(out)
    return update_document(out, out, style_id, data)


# --------------------------------------------------------------------------
# For the Word tab: Word keeps the document open and changes it itself (so
# Undo works); Citrello only turns citation data into formatted text.
# --------------------------------------------------------------------------

def html_spans(snippet):
    """citeproc HTML -> (plain text, [(start, end, fmt)]) with offsets in UTF-16 units, as Word counts."""
    p = _Runs()
    p.feed(snippet)
    text, spans, pos = "", [], 0
    for chunk, fmt in p.parts:
        chunk = chunk.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        n = len(chunk.encode("utf-16-le")) // 2
        for tag, code in (("i", "i"), ("em", "i"), ("b", "b"), ("strong", "b"), ("sup", "sup"),
                          ("sub", "sub"), ("smallcaps", "sc")):
            if tag in fmt:
                spans.append((pos, pos + n, code))
        text += chunk
        pos += n
    return text, spans


def _group_from_payload(payload, data, idx, by_id, doc_only, taken, new_items):
    group, items = [], {}
    for ci in payload.get("citationItems", []):
        key = ci.get("citrello-key") or ci.get("refcite-key")
        it = next((x for x in data["items"] if x["key"] == key), None) if key else None
        if not it:
            hit, _ = idx.find(ci.get("itemData", {}))
            it = by_id.get(hit)
        if it:
            key, csl = it["key"], it["csl"]
        else:  # cited in the document but missing from the library
            csl = core.trim_csl(ci.get("itemData", {}))
            key, _ = doc_only.find(csl)
            if not key:
                key = library.make_key(csl, taken)
                taken.add(key)
                doc_only.add(key, csl.get("title"), csl.get("DOI"), core.year_of(csl))
                new_items.append(csl)
        items[key] = csl
        group.append({"key": key, "prefix": ci.get("prefix"), "locator": ci.get("locator"),
                      "label": ci.get("label"), "suppress": bool(ci.get("suppress-author"))})
    return group, items


def code_for_group(group, data=None):
    """[{key, locator, label, prefix, suppress}] -> Word field code (Mendeley-compatible)."""
    data = data or library.load()
    lib_items = {it["key"]: it for it in data["items"]}
    lower = {k.lower(): k for k in lib_items}
    missing = [g["key"] for g in group if g["key"].lstrip("@").lower() not in lower]
    if missing:
        raise ValueError("Not in your library: " + ", ".join("@" + k for k in missing))
    for g in group:
        g["key"] = lower[g["key"].lstrip("@").lower()]
    return _instr(group, {k: it["csl"] for k, it in lib_items.items()}, lib_items, "")


def items_from_code(code, data=None):
    """Read a citation field back (for editing): [(key, display, locator, suppress, prefix)]."""
    data = data or library.load()
    payload = citation_payload(code) or {}
    idx, by_id = _match_library(data)
    group, _ = _group_from_payload(payload, data, idx, by_id, core.LibraryIndex(),
                                   {it["key"] for it in data["items"]}, [])
    out = []
    for g in group:
        it = library.get(data, g["key"])
        shown = g["key"]
        if it:
            where = it["csl"].get("container-title") or it["csl"].get("publisher") or ""
            shown = library.short_ref(it) + (f" - {where}" if where else "")
            if it.get("reliability") == "problem":
                shown += " [problem: " + "; ".join(ch["text"] for ch in it.get("checks", []) if ch["level"] == "problem")[:80] + "]"
        out.append((g["key"], shown, g.get("locator") or "", "1" if g.get("suppress") else "0", g.get("prefix") or ""))
    return out


def _hanging(path):
    head = open(path, encoding="utf-8").read()
    m = re.search(r"<bibliography[^>]*>", head)
    return bool(m and ("hanging-indent=\"true\"" in m.group(0) or "second-field-align" in m.group(0)))


def render_codes(codes, style_id=None, data=None, add_missing=True):
    """All citation field codes of a document (in order) -> what Word should show.

    Returns {"style", "hanging", "citations": [{"text", "spans", "code"} | None], "bibliography": [(text, spans)],
             "report": [...]}.
    """
    data = data or library.load()
    idx, by_id = _match_library(data)
    doc_only, taken, new_items = core.LibraryIndex(), {it["key"] for it in data["items"]}, []
    items, groups, payloads = {}, [], []
    bad = set()
    for n, code in enumerate(codes):
        payload = citation_payload(code)
        if not payload:
            if "CSL_CITATION" in (code or ""):
                bad.add(n)  # a citation field whose data is damaged (e.g. cut off)
            groups.append(None)
            payloads.append(None)
            continue
        group, its = _group_from_payload(payload, data, idx, by_id, doc_only, taken, new_items)
        items.update(its)
        groups.append(group or None)
        payloads.append(payload)
    report = []
    if add_missing and new_items:
        library.import_items(new_items, source="document")
        report.append(f"{len(new_items)} reference(s) found only in the document were added to your library")
    renderer = Renderer(style_id or DEFAULT_STYLE, items) if items else None
    order = [renderer.add(g) if (renderer and g) else None for g in groups]
    citations = []
    for g, payload, i in zip(groups, payloads, order):
        if i is None:
            citations.append(None)
            continue
        out_html = renderer.render(i)
        text, spans = html_spans(out_html)
        for ci, gi in zip(payload.get("citationItems", []), g):
            ci["citrello-key"] = gi["key"]
            lib = library.get(data, gi["key"]) if gi["key"] in {x["key"] for x in data["items"]} else None
            if lib:
                ci["itemData"] = dict(lib["csl"], id=ci.get("id", "ITEM-1"))
        payload["mendeley"] = {"formattedCitation": out_html, "plainTextFormattedCitation": text,
                               "previouslyFormattedCitation": out_html}
        code = "ADDIN CSL_CITATION " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        citations.append({"text": text, "spans": spans, "code": code})
    bib = [html_spans(e) for e in (renderer.bibliography() if renderer else [])]
    info = renderer.info if renderer else style_info(style_path(style_id or DEFAULT_STYLE))
    return {"style": info["title"], "hanging": _hanging(renderer.path) if renderer else False,
            "citations": citations, "bibliography": bib, "report": report, "bad": sorted(bad)}


def render_lines(result):
    """render_codes() result -> simple tab-separated lines for the Word macro."""
    clean = lambda s: str(s).replace("\t", " ").replace("\r", " ").replace("\n", " ")
    sp = lambda spans: ";".join(f"{a},{b},{f}" for a, b, f in spans)
    lines = [f"STYLE\t{clean(result['style'])}", f"HANGING\t{1 if result['hanging'] else 0}"]
    for i, c in enumerate(result["citations"]):
        if c:
            lines.append(f"CITE\t{i}\t{clean(c['text'])}\t{sp(c['spans'])}\t{clean(c['code'])}")
        elif i in result.get("bad", ()):
            lines.append(f"BAD\t{i}")
    for text, spans in result["bibliography"]:
        lines.append(f"BIB\t{clean(text)}\t{sp(spans)}")
    for r in result["report"]:
        lines.append(f"REPORT\t{clean(r)}")
    return "\n".join(lines)
