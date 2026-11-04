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
            "typed": "typed by hand", "pdf": "PDF", "other": "another reference manager"}


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
# PDF: the text of the reference list and of the body
# ---------------------------------------------------------------------------

PDF_HEADING_RE = re.compile(r"^\s*(?:\d+\.?\s*)?(references|bibliography|literature cited|works cited|reference list|"
                            r"references and notes|sources)\s*:?\s*$", re.I)
PDF_END_RE = re.compile(r"^\s*(?:\d+\.?\s*)?(appendix|appendices|acknowledg|supplementary|supporting information|"
                        r"author contributions|declaration of|conflicts? of interest|data availability|biograph)", re.I)
NUM_START_RE = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\.)\s+\S")
AUTHOR_START_RE = re.compile(r"^[A-Z\u00C0-\u017F][\w\u00C0-\u017F'\u2019\-]+(?: [A-Z][\w'\-]+)?,\s+(?:[A-Z]\.|[A-Z][a-z]+)")


def pdf_text(path):
    """Text of each page, with running headers/footers and page numbers removed."""
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = [(p.extract_text() or "").splitlines() for p in reader.pages]
    counts = {}
    for lines in pages:
        for ln in set(l.strip() for l in lines):
            counts[ln] = counts.get(ln, 0) + 1
    repeated = {ln for ln, n in counts.items() if n >= 3 and len(pages) >= 4 and len(ln) < 120}
    out = []
    for lines in pages:
        for ln in lines:
            t = ln.strip()
            if not t or t in repeated or re.fullmatch(r"\d{1,4}|page \d+( of \d+)?", t, re.I):
                continue
            out.append(t)
    return out


def split_pdf_references(lines):
    """-> (body lines, [(number, text)]) from the lines of a PDF."""
    start = None
    for i in range(len(lines) - 1, -1, -1):
        if PDF_HEADING_RE.match(lines[i]):
            start = i
            break
    if start is None:
        return lines, []
    body, rest = lines[:start], lines[start + 1:]
    for j, ln in enumerate(rest):
        if PDF_END_RE.match(ln) and len(ln) < 60:
            rest = rest[:j]
            break
    numbered = sum(1 for ln in rest if NUM_START_RE.match(ln))
    by_author = sum(1 for ln in rest if AUTHOR_START_RE.match(ln))
    use_numbers = numbered >= 2 or (numbered == 1 and not by_author)  # short lists count too
    entries, cur, expect = [], None, 1
    for ln in rest:
        m = NUM_START_RE.match(ln) if use_numbers else None
        n = int(m.group(1) or m.group(2)) if m else None
        new = (m and n in (expect, expect + 1)) if use_numbers else (
            AUTHOR_START_RE.match(ln) and (cur is None or re.search(r"(\.|\d)\s*$", cur[1]) or YEAR_IN.search(cur[1])))
        if new:
            if cur:
                entries.append(cur)
            if m:
                expect = n + 1
                ln = ln[m.end(0) - 1:].lstrip()
            cur = [n or len(entries) + 1, ln]
        elif cur:
            cur[1] = (cur[1][:-1] if cur[1].endswith("-") and not cur[1].endswith(" -") else cur[1] + " ") + ln
    if cur:
        entries.append(cur)
    return body, [(n, re.sub(r"\s+", " ", t).strip()) for n, t in entries if len(t) > 25]


YEAR_IN = re.compile(r"\b(19|20)\d{2}[a-z]?\b")


def _pdf_cited(body_text, refs, numbered):
    """Which references the body cites: numbers in [..] for numbered lists; surname and year otherwise."""
    if numbered:
        cited = set()
        for grp in re.findall(r"\[([\d,\s\u2013\-]+)\]", body_text):
            for part in grp.split(","):
                a, _, b = part.replace("\u2013", "-").strip().partition("-")
                if a.strip().isdigit():
                    lo = int(a)
                    hi = int(b) if b.strip().isdigit() else lo
                    cited.update(range(lo, min(hi, lo + 200) + 1))
        return cited
    cited = set()
    for r in refs:
        fam = next((a.get("family") or a.get("literal") for a in r["csl"].get("author", []) if a), "")
        y = core.year_of(r["csl"])
        if fam and y and re.search(re.escape(fam.split()[-1]) + r"[^.;()]{0,60}?" + y, body_text):
            cited.add(r["number"])
    return cited


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
    citation_issues, refs = [], []
    is_pdf = path.lower().endswith(".pdf")
    if is_pdf:
        body, pdf_entries = split_pdf_references(pdf_text(path))
        if not pdf_entries:
            raise ValueError("No reference list was found in this PDF (a heading such as 'References' followed by "
                             "the entries). Scanned PDFs without text can't be read.")
        fields, entries = {"items": {}, "managers": [], "other": 0, "bibliography_field": False}, []
    else:
        fields = read_citations(path)
        document = docx.Document(path)
        try:
            _, entries = core.find_bibliography(document)
        except SystemExit:
            entries = []

    if is_pdf:
        source = "pdf"
        numbered = all(isinstance(n, int) for n, _ in pdf_entries) and len(pdf_entries) >= 2 and \
            [n for n, _ in pdf_entries] == sorted(n for n, _ in pdf_entries)
        for i, (num, text) in enumerate(pdf_entries, 1):
            refs.append({**core.resolve_reference(num, text, True), "cited": None, "typed_text": True})
        body_text = " ".join(body)
        has_brackets = bool(re.search(r"\[\d+(?:[,\u2013\-]\s*\d+)*\]", body_text))
        cited = _pdf_cited(body_text, refs, numbered and has_brackets)
        if (numbered and has_brackets) or not numbered:
            for r in refs:
                r["cited"] = r["number"] in cited
            if numbered and has_brackets:
                listed = {r["number"] for r in refs}
                ghost = sorted(n for n in cited if n not in listed)
                if ghost:
                    citation_issues.append({"level": "problem", "text": "Cited but not in the reference list: " +
                                            ", ".join(f"[{n}]" for n in ghost[:15]) + "."})
        citation_issues.append({"level": "info", "text": "Read from a PDF: citations are matched from the text, "
                                "so check anything flagged as never cited."})
    elif fields["items"]:
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


# ---------------------------------------------------------------------------
# Fixing a Word document
# ---------------------------------------------------------------------------

COMPARE = (("title", "title"), ("container-title", "journal"), ("volume", "volume"), ("issue", "issue"),
           ("page", "pages"), ("DOI", "DOI"))


def _changes(before, after):
    out = []
    for k, label in COMPARE:
        a, b = str(before.get(k) or "").strip(), str(after.get(k) or "").strip()
        if b and core.norm(a) != core.norm(b):
            out.append(f"{label} added" if not a else f"{label} corrected")
    ya, yb = core.year_of(before), core.year_of(after)
    if yb and ya != yb:
        out.append("year added" if not ya else f"year corrected ({ya} → {yb})")
    if len(after.get("author") or []) > len(before.get("author") or []):
        out.append("authors completed")
    return out


def _payload_items(instr, data):
    """The (identifier, citationItem extras) of a citation field, in order."""
    s = instr.strip()
    up = s.upper()
    if "CSL_CITATION" in up:
        m = re.search(r"CSL_CITATION\s+(\{.*\})\s*$", s, re.S)
        try:
            payload = json.loads(m.group(1)) if m else {}
        except ValueError:
            payload = {}
        out = []
        for ci in payload.get("citationItems", []):
            d = ci.get("itemData") or {}
            ident = str((ci.get("uris") or [None])[0] or ci.get("id") or d.get("id") or "")
            extra = {k: ci[k] for k in ("locator", "label", "prefix", "suffix", "suppress-author") if ci.get(k)}
            out.append((ident, extra))
        return out
    if up.startswith("ADDIN EN.CITE"):
        return [(ident, {}) for ident, _ in _endnote_items(data or s)]
    if re.match(r"CITATION\s", s):
        return [("word:" + tag, {}) for tag in re.findall(r"(?:^CITATION|\\m)\s+(\S+)", s)]
    return None


def _set_instr(field, text):
    first = True
    for r in list(field["begin"].itersiblings()):
        if r is field["separate"] or r is field["end"]:
            break
        it = r.find(qn("w:instrText"))
        if it is not None:
            if first:
                it.text = text
                first = False
            else:
                r.getparent().remove(r)
    fc = field["begin"].find(qn("w:fldChar"))
    fd = fc.find(qn("w:fldData")) if fc is not None else None
    if fd is not None:
        fc.remove(fd)


def fix(path, report, out, style_id=None, project=None):
    """Write a corrected copy of the document: live citations, corrected details, duplicates merged, the
    reference list rebuilt in the chosen style. The references are added to the library (they're what the live
    citations point to). Returns a summary of the changes."""
    import library
    if report["source"] == "pdf":
        raise ValueError("A PDF can't be changed. Fix the Word version of the document instead.")
    refs = [r for r in report["refs"] if r["csl"].get("title")]
    before = {r["number"]: dict(r.get("typed") or {}) for r in refs}
    in_lib = library.add_records(refs, project)
    keys = {n: it["key"] for n, it in in_lib.items()}
    data = library.load()
    work = out + ".tmp.docx"

    if report["source"] == "typed":
        core.convert_document(path, refs, ("bracket", "superscript", "paren"), "auto", work)
    else:
        by_ident = {r.get("manager_id"): keys.get(r["number"]) for r in refs}
        lib_items = {it["key"]: it for it in data["items"]}
        document = docx.Document(path)
        body = document.element.body
        for f in citeformat.find_fields(body):
            fc = f["begin"].find(qn("w:fldChar"))
            fd = fc.find(qn("w:fldData")) if fc is not None else None
            raw = ""
            if fd is not None and fd.text:
                try:
                    raw = base64.b64decode(re.sub(r"\s+", "", fd.text) + "==").decode("utf-8", "replace")
                except ValueError:
                    raw = ""
            items = _payload_items(f["instr"], raw)
            up = f["instr"].strip().upper()
            if items is None:
                if re.search(r"ZOTERO_BIBL|CSL_BIBLIOGRAPHY|EN\.REFLIST|^BIBLIOGRAPHY", up):
                    _set_instr(f, "ADDIN Mendeley Bibliography CSL_BIBLIOGRAPHY ")
                continue
            group = []
            for ident, extra in items:
                key = by_ident.get(ident)
                if key and key in lib_items:
                    group.append({"id": key, "itemData": dict(lib_items[key]["csl"], id=key), "citrello-key": key,
                                  **extra})
            if group:
                _set_instr(f, "ADDIN CSL_CITATION " + json.dumps(
                    {"citationItems": group, "properties": {"noteIndex": 0}}, ensure_ascii=False))
        document.save(work)
    res = citeformat.update_document(work, out, style_id, add_missing=False)
    try:
        import os
        os.remove(work)
    except OSError:
        pass

    data = library.load()
    final = {it["key"]: it["csl"] for it in data["items"]}
    corrected, merged, seen = [], [], {}
    for r in refs:
        k = keys.get(r["number"])
        if not k:
            continue
        if k in seen:
            merged.append(f"[{r['number']}] merged into [{seen[k]}]")
            continue
        seen[k] = r["number"]
        ch = _changes(before.get(r["number"], {}), final.get(k, {}))
        if ch:
            corrected.append({"number": r["number"], "title": final[k].get("title", ""), "changes": ch})
    cited_keys = set(res["keys"])
    dropped = [r["number"] for r in refs if r.get("cited") is False and keys.get(r["number"]) not in cited_keys]
    still = [{"number": r["number"], "title": r["csl"].get("title", ""),
              "text": "; ".join(c["text"] for c in r.get("checks", []) if c["level"] == "problem")}
             for r in refs if r.get("reliability") == "problem"]
    # what rebuilding can't fix: citations to references that aren't in the list, citations not recognised
    unfixable = [i["text"] for i in report["citation_issues"] if i["level"] == "problem"
                 and not re.search(r"listed more than once|Refresh the bibliography|skips", i["text"])]
    return {"style": res["style"]["title"], "citations": res["citations"], "entries": res["entries"],
            "corrected": corrected, "merged": merged, "dropped_uncited": dropped, "still_problems": still,
            "unfixable": unfixable + [m for m in res["report"] if "not found" in m.lower()]}
