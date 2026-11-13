"""Turning a document's citations into Citrello for Word's citations, in place.

The Word add-in checks the document with "Check and fix" (doccheck.check), then asks for a plan: which paragraphs
hold citations made some other way (Zotero, Mendeley Desktop and Mendeley Cite, EndNote, Word's own citations,
the web app's fields, [@Key] markers, typed citations with a typed list) and where the reference list is. For each
of those paragraphs the add-in sends Word's own XML of the paragraph (Office.js getOoxml); convert_paragraph() wraps
each citation in a content control tagged CITRELLO_CITATION_v1_<base64 JSON> and gives it back. The text stays as
it was: the add-in then formats everything in the chosen style with Word's Track Changes on, so what changes shows
as revisions.

Paragraphs are found again in Word by their text (sig: letters and digits only) and how many earlier paragraphs
have the same text (nth).
"""
import base64
import copy
import json
import re
import secrets
import unicodedata

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.parser import oxml_parser
from lxml import etree

import citeformat
import citrello_core as core
import doccheck
import library

TAG = "CITRELLO_CITATION_v1_"
BIB = "CITRELLO_BIBLIOGRAPHY"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
KEEP = ("locator", "label", "prefix", "suffix", "suppress-author")
MANAGER_OF_FIELD = (("ZOTERO_ITEM", "zotero"), ("CSL_CITATION", "mendeley"), ("EN.CITE", "endnote"))


def sig(text):
    """A paragraph's text as letters and digits (the add-in computes the same from Word's paragraph.text)."""
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^0-9a-z]+", "", s)


def para_text(p):
    return "".join(t.text or "" for t in p.iter(qn("w:t")))


def body_paras(body):
    """The body's paragraphs in order, as Word's body.paragraphs lists them (text boxes left out)."""
    return [p for p in body.iter(qn("w:p")) if not any(a.tag == qn("w:txbxContent") for a in p.iterancestors())]


def payload_tag(items):
    payload = {"citationID": secrets.token_hex(4), "citationItems": items, "properties": {"noteIndex": 0}}
    return TAG + base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode()


def make_sdt(items, title="Citrello citation"):
    sdt = OxmlElement("w:sdt")
    pr = OxmlElement("w:sdtPr")
    alias = OxmlElement("w:alias")
    alias.set(qn("w:val"), title)
    tag = OxmlElement("w:tag")
    tag.set(qn("w:val"), payload_tag(items))
    ident = OxmlElement("w:id")
    ident.set(qn("w:val"), str(secrets.randbelow(2 ** 31)))
    pr.extend([alias, tag, ident])
    content = OxmlElement("w:sdtContent")
    sdt.extend([pr, content])
    return sdt, content


# ---------------------------------------------------------------------------
# The plan: which references the document cites, under which keys
# ---------------------------------------------------------------------------

class Plan:
    def __init__(self, path, report):
        self.path, self.report = path, report
        data = library.load()
        self.library = data
        index = library._index(data["items"])
        by_id = {it["id"]: it for it in data["items"]}
        canon = {}
        for d in report.get("duplicates", []):  # the same paper listed twice: one reference
            for n in d["numbers"][1:]:
                canon[n] = d["numbers"][0]
        self.num_key, self.ident_key, self.csl = {}, {}, {}
        taken = {it["key"] for it in data["items"]}
        refs = {r["number"]: r for r in report["refs"]}
        for r in report["refs"]:
            base = refs.get(canon.get(r["number"], r["number"]), r)
            if base["number"] in self.num_key:
                key = self.num_key[base["number"]]
            else:
                hit, _ = index.find(base["csl"])
                if hit and hit in by_id:  # in the library: its key, and the library's details
                    key = by_id[hit]["key"]
                    self.csl[key] = dict(by_id[hit]["csl"])
                else:
                    key = library.make_key(base["csl"], taken)
                    taken.add(key)
                    self.csl[key] = dict(base["csl"])
                self.num_key[base["number"]] = key
            self.num_key[r["number"]] = key
            if r.get("manager_id"):
                self.ident_key[r["manager_id"]] = key
        self.finder = None
        self.typed_notes = []
        document = docx.Document(path)
        try:
            heading, entries = core.find_bibliography(document)
        except SystemExit:
            heading, entries = None, []
        self.entries = entries
        if report["source"] == "typed" and entries:
            mode = core.detect_mode(entries)
            by_num = {r["number"]: r for r in report["refs"]}
            self.finder = core.numeric_finder(by_num, {"bracket", "superscript"}) if mode == "numeric" \
                else core.author_year_finder(report["refs"])
        self.document = document
        self.heading = heading
        self.word_sources = None

    def item(self, key, extra=None):
        c = dict(self.csl.get(key) or {})
        c["id"] = key
        out = {"id": key, "itemData": c}
        out.update({k: v for k, v in (extra or {}).items() if v not in (None, "", False) and k in KEEP})
        return out

    def key_for(self, ident, csl):
        if ident in self.ident_key:
            return self.ident_key[ident]
        key = library.make_key(csl, set(self.csl))  # cited but not in the report (shouldn't happen)
        self.csl[key] = dict(csl)
        self.ident_key[ident] = key
        return key

    # ----- what one citation field holds -----
    def field_items(self, instr, data):
        s = instr.strip()
        up = s.upper()
        out = []
        if "CSL_CITATION" in up:
            m = re.search(r"CSL_CITATION\s+(\{.*\})\s*$", s, re.S)
            try:
                payload = json.loads(m.group(1)) if m else {}
            except ValueError:
                payload = {}
            for ci in payload.get("citationItems", []):
                d = dict(ci.get("itemData") or {})
                ident = str((ci.get("uris") or [None])[0] or ci.get("id") or d.get("id") or "")
                if d:
                    out.append(self.item(self.key_for(ident, d), ci))
            manager = "zotero" if "ZOTERO_ITEM" in up else "mendeley"
        elif up.startswith("ADDIN EN.CITE"):
            for ident, c in doccheck._endnote_items(data or s):
                out.append(self.item(self.key_for(ident, c)))
            manager = "endnote"
        elif re.match(r"CITATION\s", s):
            if self.word_sources is None:
                self.word_sources = doccheck._word_sources(self.path)
            for tag in re.findall(r"(?:^CITATION|\\m)\s+(\S+)", s):
                if tag in self.word_sources:
                    out.append(self.item(self.key_for("word:" + tag, self.word_sources[tag])))
            manager = "word"
        else:
            return None, None
        return out, manager

    def marker_finder(self):
        def find(p, full, sup, report):
            out = []
            for m in citeformat.MARKER_RE.finditer(full):
                items = []
                for g in citeformat.parse_marker(m.group(1)) or []:
                    it = library.get(self.library, g["key"])
                    if not it:
                        items = None
                        break
                    key = self.ident_key.get("key:" + it["key"]) or it["key"]
                    self.csl.setdefault(key, dict(it["csl"]))
                    items.append({"key": key, "locator": g.get("locator"), "label": g.get("label"),
                                  "prefix": g.get("prefix"), "suppress": g.get("suppress")})
                if items:
                    out.append((m.start(), m.end(), items))
            return out
        return find


# ---------------------------------------------------------------------------
# One paragraph
# ---------------------------------------------------------------------------

def _fld(el):
    fc = el.find(qn("w:fldChar")) if el.tag == qn("w:r") else None
    return (fc.get(qn("w:fldCharType")), fc) if fc is not None else (None, None)


def _fld_data(fc):
    fd = fc.find(qn("w:fldData")) if fc is not None else None
    if fd is None or not fd.text:
        return ""
    raw = base64.b64decode(re.sub(r"\s+", "", fd.text) + "==")
    for enc in ("utf-8", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _fields(parent, plan, counts, notes):
    """Citation fields among `parent`'s children -> content controls (the result text kept). Returns the count."""
    n = 0
    children = list(parent)
    i = 0
    while i < len(children):
        kind, fc = _fld(children[i])
        if kind != "begin":
            i += 1
            continue
        depth, j, instr, sep = 1, i + 1, "", None
        while j < len(children) and depth:
            k, _ = _fld(children[j])
            if k == "begin":
                depth += 1
            elif k == "end":
                depth -= 1
            elif k == "separate" and depth == 1 and sep is None:
                sep = j
            if depth == 1 and sep is None and children[j].tag == qn("w:r"):
                instr += "".join(t.text or "" for t in children[j].findall(qn("w:instrText")))
            j += 1
        if depth:  # the field goes on in another paragraph (a reference list): not a citation
            break
        end = j - 1
        items, manager = plan.field_items(instr, _fld_data(fc))
        if items:
            sdt, content = make_sdt(items)
            for el in children[(sep + 1 if sep else end):end]:
                if el.tag == qn("w:r") and _fld(el)[0] is None and el.find(qn("w:instrText")) is None:
                    content.append(copy.deepcopy(el))
            children[i].addprevious(sdt)
            for el in children[i:end + 1]:
                parent.remove(el)
            counts[manager] = counts.get(manager, 0) + 1
            n += 1
        elif manager:
            notes.append(f"A {doccheck.MANAGERS.get(manager, manager)} citation without its papers' details was left as it is.")
        i = end + 1
    for el in list(parent.findall(qn("w:fldSimple"))):
        items, manager = plan.field_items(el.get(qn("w:instr")) or "", "")
        if items:
            sdt, content = make_sdt(items)
            for r in el.findall(qn("w:r")):
                content.append(copy.deepcopy(r))
            el.addprevious(sdt)
            parent.remove(el)
            counts[manager] = counts.get(manager, 0) + 1
            n += 1
    return n


def _mendeley_cite(parent, plan, counts):
    n = 0
    for sdt in list(parent.findall(qn("w:sdt"))):
        tag = sdt.find(qn("w:sdtPr") + "/" + qn("w:tag"))
        val = tag.get(qn("w:val")) if tag is not None else ""
        if not val.upper().startswith("MENDELEY_CITATION"):
            continue
        payload = doccheck._b64json(re.sub(r"^MENDELEY_CITATION(?:_v\d+)?_", "", val, flags=re.I)) or {}
        items = []
        for ci in payload.get("citationItems", []):
            d = dict(ci.get("itemData") or {})
            ident = str((ci.get("uris") or [None])[0] or ci.get("id") or d.get("id") or "")
            if d:
                items.append(plan.item(plan.key_for(ident, d), ci))
        if not items:
            continue
        tag.set(qn("w:val"), payload_tag(items))
        pr = sdt.find(qn("w:sdtPr"))
        alias = pr.find(qn("w:alias"))
        if alias is None:
            alias = OxmlElement("w:alias")
            pr.insert(0, alias)
        alias.set(qn("w:val"), "Citrello citation")
        counts["mendeley_cite"] = counts.get("mendeley_cite", 0) + 1
        n += 1
    return n


def _wrap_spans(p, finder, make_items, manager, counts, notes):
    """Typed citations and [@Key] markers: the runs holding each one go into a content control."""
    segs = core.segments(p)
    full = "".join(s[3] for s in segs)
    sup = "".join(("1" if s[4] else "0") * len(s[3]) for s in segs)
    report = []
    found = sorted(finder(p, full, sup, report), key=lambda f: f[0])
    notes += report
    n = 0
    for s, e, items in reversed(found):
        covered = [seg for seg in segs if seg[1] < e and seg[2] > s]
        if not covered or any(seg[0] is None for seg in covered) or len({seg[0].getparent() for seg in covered}) > 1:
            notes.append(f"Left as it is (inside another field): {full[s:e]!r}")
            continue
        first, last = covered[0], covered[-1]
        runs = [seg[0] for seg in covered]
        if last[2] > e:
            core.split_run(last[0], e - last[1])
        if first[1] < s:
            right = core.split_run(first[0], s - first[1])
            runs = [right] if first[0] is last[0] else [right] + runs[1:]
        sdt, content = make_sdt(make_items(items))
        runs[0].addprevious(sdt)
        for r in runs:
            content.append(r)
        n += 1
    if n:
        counts[manager] = counts.get(manager, 0) + n
    return n


def convert_p(p, plan, notes=None):
    """Convert the citations in one w:p element (changed in place). -> {manager: count}"""
    counts, notes = {}, notes if notes is not None else []
    for parent in [p] + p.findall(qn("w:hyperlink")):
        _fields(parent, plan, counts, notes)
        _mendeley_cite(parent, plan, counts)
    if plan.finder:
        def typed(items):
            return [plan.item(plan.num_key[it["ref"]["number"]],
                              {"locator": it.get("locator"), "label": it.get("label"), "prefix": it.get("prefix"),
                               "suppress-author": it.get("suppress")}) for it in items]
        _wrap_spans(p, plan.finder, typed, "typed", counts, notes)
    _wrap_spans(p, plan.marker_finder(),
                lambda items: [plan.item(it["key"], {"locator": it["locator"], "label": it["label"], "prefix": it["prefix"],
                                                     "suppress-author": it["suppress"]}) for it in items],
                "citrello", counts, notes)
    return counts


def make_plan(path, report):
    """-> (Plan, summary for the add-in)"""
    plan = Plan(path, report)
    body = plan.document.element.body
    paras = body_paras(body)
    bib_ids = {id(e[1]._p) for e in plan.entries}
    heading_at = next((i for i, p in enumerate(paras) if plan.heading is not None and p is plan.heading._p), None)
    seen, targets, counts, notes, already = {}, [], {}, [], 0
    for i, p in enumerate(paras):
        s = sig(para_text(p))
        nth = seen.get(s, 0)
        seen[s] = nth + 1
        already += sum(1 for t in p.iter(qn("w:tag")) if (t.get(qn("w:val")) or "").startswith(TAG))
        if not s or id(p) in bib_ids:
            continue
        if plan.finder and heading_at is not None and i >= heading_at:
            saved = plan.finder
            plan.finder = None  # typed citations live before the reference list
            c = convert_p(copy.deepcopy(p), plan, notes)
            plan.finder = saved
        else:
            c = convert_p(copy.deepcopy(p), plan, notes)
        if c:
            targets.append({"sig": s, "nth": nth, "count": sum(c.values())})
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v
    # the reference list
    bib = None
    tags = [(t.get(qn("w:val")) or "") for t in body.iter(qn("w:tag"))]
    if any(t.upper().startswith("CITRELLO_BIBLIOGRAPHY") for t in tags):
        bib = {"kind": "done"}
    elif any(t.upper().startswith("MENDELEY_BIBLIOGRAPHY") for t in tags):
        bib = {"kind": "cc", "tag": "MENDELEY_BIBLIOGRAPHY"}
    elif plan.entries:
        def where(p_el):
            s = sig(para_text(p_el))
            return {"sig": s, "nth": sum(1 for q in paras[:paras.index(p_el)] if sig(para_text(q)) == s)}
        bib = {"kind": "paragraphs", "first": where(plan.entries[0][1]._p), "last": where(plan.entries[-1][1]._p),
               "count": len(plan.entries)}
    names = {k: doccheck.MANAGERS.get(k, k) for k in counts}
    names["typed"] = "typed by hand"
    names["citrello"] = "[@Key] markers"
    return plan, {"citations": sum(counts.values()), "by": {names[k]: v for k, v in counts.items()},
                  "already": already, "paragraphs": targets, "bibliography": bib, "notes": notes[:20]}


def convert_ooxml(plan, pkg):
    """Word's XML of a paragraph (flat OPC from getOoxml) -> (the same with citations converted, count)."""
    try:
        root = etree.fromstring(pkg.encode("utf-8") if isinstance(pkg, str) else pkg, oxml_parser)
    except etree.XMLSyntaxError:
        raise ValueError("Word's copy of the paragraph couldn't be read.")
    total = 0
    for body in root.iter(f"{{{W}}}body"):
        for p in body.findall(f"{{{W}}}p"):
            total += sum(convert_p(p, plan).values())
    return etree.tostring(root, encoding="unicode"), total
