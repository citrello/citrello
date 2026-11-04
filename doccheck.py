"""
Check the references of any Word document, whichever reference manager made it (or none).

Reads the citations that Zotero, Mendeley, EndNote, Citrello and Word's own citation tool store inside the
document; otherwise uses the typed reference list. Then:
- checks every reference online (retracted, DOI that doesn't exist or points to another paper, authors or year
  that don't match the publisher's record, no DOI, preprint, web page);
- finds the same paper listed twice;
- finds citation mistakes: a citation pointing to a reference that isn't in the list, references never cited,
  a reference list that doesn't match the citations (not updated);
and can add every reference to the Citrello library.
"""
import base64
import html
import json
import re
import zipfile
import xml.etree.ElementTree as ET

import docx
from docx.oxml.ns import qn

import citeformat
import citrello_core as core

MANAGERS = {"zotero": "Zotero", "mendeley": "Mendeley or Citrello", "endnote": "EndNote", "word": "Word's own citations",
            "typed": "typed by hand", "other": "another reference manager"}


# ---------------------------------------------------------------------------
# Reading citation fields
# ---------------------------------------------------------------------------

def _fields(document):
    """[(instr, fldData text)] for every field in the body, footnotes included where python-docx exposes them."""
    out = []
    body = document.element.body
    for f in citeformat.find_fields(body):
        data = ""
        fc = f["begin"].find(qn("w:fldChar"))
        fd = fc.find(qn("w:fldData")) if fc is not None else None
        if fd is not None and fd.text:
            raw = base64.b64decode(re.sub(r"\s+", "", fd.text) + "==")
            for enc in ("utf-8", "utf-16"):
                try:
                    data = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
        out.append((f["instr"], data))
    for el in body.iter(qn("w:fldSimple")):
        out.append((el.get(qn("w:instr")) or "", ""))
    return out


def _csl_items(instr):
    """CSL items from a Zotero / Mendeley / Citrello citation field."""
    m = re.search(r"CSL_CITATION\s+(\{.*\})\s*$", instr, re.S)
    if not m:
        return []
    try:
        payload = json.loads(m.group(1))
    except ValueError:
        return []
    items = []
    for ci in payload.get("citationItems", []):
        d = dict(ci.get("itemData") or {})
        ident = (ci.get("uris") or [None])[0] or ci.get("id") or d.get("id")
        if d:
            items.append((str(ident or ""), d))
    return items


def _text(el, path):
    found = el.find(path)
    return "".join(found.itertext()).strip() if found is not None else ""


def _endnote_items(xml_text):
    """CSL items from EndNote's <EndNote><Cite>...<record> XML."""
    xml_text = html.unescape(xml_text) if "&lt;EndNote" in xml_text else xml_text
    m = re.search(r"<EndNote>.*</EndNote>", xml_text, re.S)
    if not m:
        return []
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return []
    items = []
    for cite in root.iter("Cite"):
        rec = cite.find("record")
        if rec is None:
            continue
        ident = _text(rec, "rec-number") or _text(cite, "RecNum")
        kind = (rec.find("ref-type").get("name") if rec.find("ref-type") is not None else "") or ""
        c = {"type": {"Journal Article": "article-journal", "Book": "book", "Book Section": "chapter",
                      "Conference Proceedings": "paper-conference", "Conference Paper": "paper-conference",
                      "Thesis": "thesis", "Report": "report", "Web Page": "webpage"}.get(kind, "article-journal"),
             "title": _text(rec, "titles/title"),
             "container-title": _text(rec, "titles/secondary-title") or _text(rec, "periodical/full-title"),
             "volume": _text(rec, "volume"), "issue": _text(rec, "number"), "page": _text(rec, "pages"),
             "publisher": _text(rec, "publisher"), "DOI": core.norm_doi(_text(rec, "electronic-resource-num")),
             "URL": _text(rec, "urls/related-urls/url")}
        authors = []
        for a in rec.iter("author"):
            name = "".join(a.itertext()).strip()
            if name:
                fam, _, given = name.partition(",")
                authors.append({"family": fam.strip(), "given": given.strip()} if given else {"literal": fam.strip()})
        if authors:
            c["author"] = authors
        y = re.search(r"\d{4}", _text(rec, "dates/year") or _text(cite, "Year"))
        if y:
            c["issued"] = {"date-parts": [[int(y.group(0))]]}
        c = {k: v for k, v in c.items() if v}
        if c.get("title"):
            items.append((f"endnote:{ident}", c))
    return items


def _word_sources(path):
    """Word's built-in citation tool keeps sources in customXml (b:Sources). -> {tag: csl}"""
    ns = {"b": "http://schemas.openxmlformats.org/officeDocument/2006/bibliography"}
    out = {}
    try:
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not name.startswith("customXml/item") or not name.endswith(".xml"):
                    continue
                data = z.read(name)
                if b"bibliography" not in data:
                    continue
                root = ET.fromstring(data)
                for src in root.findall("b:Source", ns):
                    t = lambda tag: (src.findtext(f"b:{tag}", default="", namespaces=ns) or "").strip()  # noqa: E731
                    c = {"type": {"JournalArticle": "article-journal", "Book": "book", "BookSection": "chapter",
                                  "ConferenceProceedings": "paper-conference", "Report": "report",
                                  "InternetSite": "webpage", "DocumentFromInternetSite": "webpage"}.get(t("SourceType"), "article"),
                         "title": t("Title"), "container-title": t("JournalName") or t("BookTitle") or t("ConferenceName"),
                         "volume": t("Volume"), "issue": t("Issue"), "page": t("Pages"), "publisher": t("Publisher"),
                         "DOI": core.norm_doi(t("DOI")), "URL": t("URL")}
                    people = src.findall(".//b:Author/b:Author/b:NameList/b:Person", ns)
                    if people:
                        c["author"] = [{"family": p.findtext("b:Last", "", ns), "given": p.findtext("b:First", "", ns)}
                                       for p in people]
                    corp = src.findtext(".//b:Author/b:Author/b:Corporate", default="", namespaces=ns)
                    if corp and not people:
                        c["author"] = [{"literal": corp}]
                    y = re.search(r"\d{4}", t("Year"))
                    if y:
                        c["issued"] = {"date-parts": [[int(y.group(0))]]}
                    if t("Tag") and c["title"]:
                        out[t("Tag")] = {k: v for k, v in c.items() if v}
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        pass
    return out


def read_citations(path):
    """-> {"manager", "citations": [[ids]], "items": {id: csl}, "bibliography_field": bool, "other": int}"""
    document = docx.Document(path)
    managers, citations, items, bib_field, other = set(), [], {}, False, 0
    word_sources = None
    for instr, data in _fields(document):
        s = instr.strip()
        up = s.upper()
        group = []
        if "CSL_CITATION" in up:
            managers.add("zotero" if "ZOTERO_ITEM" in up else "mendeley")
            for ident, c in _csl_items(s):
                items.setdefault(ident, c)
                group.append(ident)
        elif up.startswith("ADDIN EN.CITE"):
            managers.add("endnote")
            for ident, c in _endnote_items(data or s):
                items.setdefault(ident, c)
                group.append(ident)
        elif re.match(r"CITATION\s", s):
            managers.add("word")
            if word_sources is None:
                word_sources = _word_sources(path)
            for tag in re.findall(r"(?:^CITATION|\\m)\s+(\S+)", s):
                if tag in word_sources:
                    items.setdefault("word:" + tag, word_sources[tag])
                    group.append("word:" + tag)
        elif re.search(r"ZOTERO_BIBL|CSL_BIBLIOGRAPHY|EN\.REFLIST|^BIBLIOGRAPHY", up):
            bib_field = True
        elif re.search(r"ADDIN (RW\.|PAPERS2|REFMGR|CITAVI|F1000|EN\.)", up):
            other += 1
            managers.add("other")
        if group:
            citations.append(group)
    return {"managers": sorted(managers), "citations": citations, "items": items, "bibliography_field": bib_field,
            "other": other}


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _raw(c):
    names = ", ".join((a.get("family") or a.get("literal") or "") for a in c.get("author", [])[:3])
    parts = [names, f"({core.year_of(c)})" if core.year_of(c) else "", c.get("title", ""), c.get("container-title", ""),
             c.get("volume", ""), c.get("page", ""), f"doi:{c['DOI']}" if c.get("DOI") else ""]
    return ". ".join(p for p in parts if p)


def _duplicates(refs):
    idx, groups = core.LibraryIndex(), {}
    for r in refs:
        hit, how = idx.find(r["csl"])
        if hit is not None:
            groups.setdefault(hit, {"numbers": [hit], "how": how})["numbers"].append(r["number"])
        else:
            idx.add(r["number"], r["csl"].get("title"), r["csl"].get("DOI"), core.year_of(r["csl"]))
    return list(groups.values())


def check(path, offline=False, email=None, progress=None):
    """The full report for one .docx."""
    say = progress or (lambda *a: None)
    fields = read_citations(path)
    document = docx.Document(path)
    try:
        _, entries = core.find_bibliography(document)
    except SystemExit:
        entries = []
    citation_issues, refs = [], []

    if fields["items"]:
        source = [m for m in fields["managers"] if m != "other"][0] if [m for m in fields["managers"] if m != "other"] else "other"
        order = []
        for group in fields["citations"]:
            for ident in group:
                if ident not in order:
                    order.append(ident)
        for n, ident in enumerate(order, 1):
            c = fields["items"][ident]
            refs.append({"number": n, "raw": _raw(c), "source": source, "csl": dict(c), "typed": dict(c), "meta": {},
                         "manager_id": ident, "cited": True})
        if entries and abs(len(entries) - len(refs)) > 0:
            citation_issues.append({"level": "problem" if len(entries) < len(refs) else "check",
                                    "text": f"The reference list has {len(entries)} entries, but the document cites "
                                            f"{len(refs)} different references. Refresh the bibliography in "
                                            f"{MANAGERS.get(source, 'your reference manager')}."})
        if not entries and not fields["bibliography_field"]:
            citation_issues.append({"level": "check", "text": "No reference list was found at the end of the document."})
        if fields["other"]:
            citation_issues.append({"level": "check", "text": f"{fields['other']} citation(s) from another reference "
                                    "manager couldn't be read and aren't checked."})
    elif entries:
        source = "typed"
        for i, (num, _, text) in enumerate(entries, 1):
            say(i, len(entries), "Reading the reference list")
            refs.append({**core.resolve_reference(num, text, True), "cited": None, "typed_text": True})
        try:
            dry = core.convert_document(path, refs)  # finds the typed citations; nothing is saved
            cited = set(dry["cited"])
            for r in refs:
                r["cited"] = r["number"] in cited
            for msg in dry["report"]:
                citation_issues.append({"level": "problem", "text": msg})
            nums = [e[0] for e in entries]
            missing = [n for n in range(1, max(nums) + 1) if n not in nums] if dry["mode"] == "numeric" else []
            if missing:
                citation_issues.append({"level": "problem", "text": "The numbered list skips " +
                                        ", ".join(f"[{n}]" for n in missing[:10]) + "."})
        except (SystemExit, Exception) as e:  # citations not recognised: still check the list itself
            citation_issues.append({"level": "check", "text": f"The citations in the text couldn't be matched ({e})."})
    else:
        raise ValueError("No references found: the document has no citations from a reference manager and no "
                         "'References' or 'Bibliography' heading with a list below it.")

    # online check, one reference at a time
    for i, r in enumerate(refs, 1):
        say(i, len(refs), "Checking references online" if not offline else "Checking references")
        if offline:
            continue
        typed = core.parse_reference(r["raw"]) if r.get("typed_text") else r["typed"]
        try:
            csl, src, meta = core.crossref_lookup(r["raw"], typed, email)
        except ConnectionError:
            offline = True
            continue
        if csl:
            r.update(csl=csl, meta=meta, typed=typed, lookup=src)
    core.assess_references(refs)

    dups = _duplicates(refs)
    never = [r["number"] for r in refs if r.get("cited") is False]
    if never:
        citation_issues.append({"level": "check", "text": f"{len(never)} reference(s) in the list are never cited: "
                                + ", ".join(f"[{n}]" for n in never[:15]) + ("…" if len(never) > 15 else "")})
    for d in dups:
        citation_issues.append({"level": "problem", "text": "The same paper is listed more than once: " +
                                " = ".join(f"[{n}]" for n in d["numbers"]) + f" ({d['how']})."})
    counts = {k: sum(r["reliability"] == k for r in refs) for k in ("verified", "check", "problem")}
    return {"source": source, "manager": MANAGERS.get(source, source), "managers": fields["managers"],
            "online": not offline, "references": len(refs), "counts": counts, "duplicates": dups,
            "citation_issues": citation_issues, "refs": refs}
