"""Checking the references of documents made with different reference managers (offline: no lookups)."""
import json
import os
import zipfile
from xml.sax.saxutils import escape

import docx
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

import doccheck

TOPHAM = {"id": "t", "type": "article-journal", "title": "Sustainable decommissioning of an offshore wind farm",
          "author": [{"family": "Topham", "given": "Eva"}], "issued": {"date-parts": [[2017]]},
          "container-title": "Renewable Energy", "DOI": "10.1016/j.renene.2016.10.066"}
VETTERS = {"id": "v", "type": "article-journal", "title": "Sailing through end-of-life challenges: A comprehensive review",
           "author": [{"family": "Vetters", "given": "Jan"}], "issued": {"date-parts": [[2024]]},
           "container-title": "Renewable and Sustainable Energy Reviews", "DOI": "10.1016/j.rser.2024.114486"}


def add_field(paragraph, instr, result="(cite)"):
    runs = [f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="begin"/></w:r>',
            f'<w:r {nsdecls("w")}><w:instrText xml:space="preserve">{escape(instr)}</w:instrText></w:r>',
            f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="separate"/></w:r>',
            f'<w:r {nsdecls("w")}><w:t>{escape(result)}</w:t></w:r>',
            f'<w:r {nsdecls("w")}><w:fldChar w:fldCharType="end"/></w:r>']
    for r in runs:
        paragraph._p.append(parse_xml(r))


def csl_field(items, zotero=False):
    payload = {"citationItems": [{"id": c["id"], "uris": [f"http://zotero.org/users/1/items/{c['id'].upper()}"],
                                  "itemData": c} for c in items]}
    return ("ADDIN ZOTERO_ITEM CSL_CITATION " if zotero else "ADDIN CSL_CITATION ") + json.dumps(payload)


def test_zotero_document_with_duplicate_and_stale_list(tmp_path):
    dup = dict(TOPHAM, id="t2", title=TOPHAM["title"] + ".")  # the same paper saved twice in Zotero
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    add_field(p, csl_field([TOPHAM], zotero=True))
    p = d.add_paragraph("and reviewed ")
    add_field(p, csl_field([VETTERS, dup], zotero=True))
    d.add_heading("References", level=1)
    d.add_paragraph("Topham, E. (2017). Sustainable decommissioning...")
    path = str(tmp_path / "zotero.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    assert rep["source"] == "zotero" and rep["references"] == 3
    assert rep["duplicates"] and sorted(rep["duplicates"][0]["numbers"]) == [1, 3]
    texts = " ".join(i["text"] for i in rep["citation_issues"])
    assert "reference list has 1 entries" in texts and "Refresh the bibliography in Zotero" in texts
    assert "listed more than once" in texts


def test_typed_numeric_document(tmp_path):
    d = docx.Document()
    d.add_paragraph("Costs are high [1], recycling is hard [2] and see also [5].")
    d.add_heading("References", level=1)
    d.add_paragraph("[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, pp.470-480. doi:10.1016/j.renene.2016.10.066")
    d.add_paragraph("[2] Vetters, J., Thomassen, G. and Van Passel, S. (2024) 'Sailing through end-of-life challenges: "
                    "A comprehensive review for offshore wind', Renewable and Sustainable Energy Reviews, 199, 114486.")
    d.add_paragraph("[3] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, 470-480.")
    path = str(tmp_path / "typed.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    assert rep["source"] == "typed" and rep["references"] == 3
    texts = " ".join(i["text"] for i in rep["citation_issues"])
    assert "[3]" in texts and "never cited" in texts            # [3] is never cited
    assert "[5]" in texts                                      # [5] isn't in the list
    assert "listed more than once" in texts                    # [1] and [3] are the same paper


def test_endnote_and_word_citations(tmp_path):
    d = docx.Document()
    p = d.add_paragraph("EndNote cites ")
    en = ("ADDIN EN.CITE <EndNote><Cite><Author>Topham</Author><Year>2017</Year><RecNum>12</RecNum><record>"
          "<rec-number>12</rec-number><ref-type name=\"Journal Article\">17</ref-type><contributors><authors>"
          "<author>Topham, Eva</author><author>McMillan, David</author></authors></contributors><titles>"
          "<title>Sustainable decommissioning of an offshore wind farm</title><secondary-title>Renewable Energy"
          "</secondary-title></titles><dates><year>2017</year></dates>"
          "<electronic-resource-num>10.1016/j.renene.2016.10.066</electronic-resource-num></record></Cite></EndNote>")
    add_field(p, en)
    p = d.add_paragraph("Word cites ")
    add_field(p, "CITATION Vet24 \\l 1033")
    path = str(tmp_path / "mixed.docx")
    d.save(path)
    sources = ('<?xml version="1.0"?><b:Sources xmlns:b="http://schemas.openxmlformats.org/officeDocument/2006/bibliography">'
               "<b:Source><b:Tag>Vet24</b:Tag><b:SourceType>JournalArticle</b:SourceType><b:Title>Sailing through "
               "end-of-life challenges</b:Title><b:Year>2024</b:Year><b:JournalName>Renewable and Sustainable Energy "
               "Reviews</b:JournalName><b:Author><b:Author><b:NameList><b:Person><b:Last>Vetters</b:Last><b:First>Jan"
               "</b:First></b:Person></b:NameList></b:Author></b:Author></b:Source></b:Sources>")
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("customXml/item1.xml", sources)
    got = doccheck.read_citations(path)
    assert set(got["managers"]) == {"endnote", "word"}
    titles = {c["title"] for c in got["items"].values()}
    assert "Sustainable decommissioning of an offshore wind farm" in titles and "Sailing through end-of-life challenges" in titles
    en_item = next(c for c in got["items"].values() if c.get("DOI"))
    assert [a["family"] for a in en_item["author"]] == ["Topham", "McMillan"]


def test_no_references(tmp_path):
    d = docx.Document()
    d.add_paragraph("Just text.")
    path = str(tmp_path / "none.docx")
    d.save(path)
    try:
        doccheck.check(path, offline=True)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "No references found" in str(e)


def make_pdf(path, pages):
    """A minimal text PDF: pages = [[line, ...], ...] (Helvetica 10pt)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        esc_ = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")  # noqa: E731
        stream = "BT /F1 10 Tf 12 TL 50 780 Td " + " ".join(f"({esc_(l)}) Tj T*" for l in lines) + " ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {content} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    open(path, "wb").write(out)


def test_pdf_numbered_references(tmp_path):
    path = str(tmp_path / "paper.pdf")
    make_pdf(path, [
        ["Offshore wind decommissioning", "Costs are often underestimated [1] and recycling is hard [2].",
         "Planning matters too [5]."],
        ["References",
         "[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an",
         "offshore wind farm. Renewable Energy, 102, pp.470-480.",
         "[2] Vetters, J., Thomassen, G. and Van Passel, S. (2024) Sailing through end-of-life",
         "challenges: A comprehensive review for offshore wind. Renewable and Sustainable Energy Reviews, 199.",
         "[3] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore",
         "wind farm. Renewable Energy, 102, 470-480.",
         "Appendix A"]])
    rep = doccheck.check(path, offline=True)
    assert rep["source"] == "pdf" and rep["references"] == 3
    texts = " ".join(i["text"] for i in rep["citation_issues"])
    assert "[5]" in texts and "Cited but not in the reference list" in texts
    assert "never cited" in texts and "[3]" in texts
    assert "listed more than once" in texts
    assert rep["refs"][0]["csl"].get("title", "").startswith("Sustainable decommissioning")


def _fields_of(path):
    import citeformat
    d = docx.Document(path)
    return [f["instr"] for f in citeformat.find_fields(d.element.body)]


def test_fix_zotero_document(tmp_path, monkeypatch):
    import library
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    dup = dict(TOPHAM, id="t2")
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    add_field(p, csl_field([TOPHAM], zotero=True))
    p = d.add_paragraph("and reviewed ")
    add_field(p, csl_field([VETTERS, dup], zotero=True))
    d.add_heading("References", level=1)
    p = d.add_paragraph()
    add_field(p, 'ADDIN ZOTERO_BIBL {"uncited":[]} CSL_BIBLIOGRAPHY', "old list")
    path, out = str(tmp_path / "z.docx"), str(tmp_path / "z_fixed.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    res = doccheck.fix(path, rep, out, "ieee", "Paper")
    assert res["merged"] == ["[3] merged into [1]"] and res["entries"] == 2      # the duplicate became one
    instrs = _fields_of(out)
    assert not any("ZOTERO" in i for i in instrs)                               # now live Citrello citations
    assert sum("CSL_CITATION" in i for i in instrs) == 2 and any("CSL_BIBLIOGRAPHY" in i for i in instrs)
    text = "\n".join(p.text for p in docx.Document(out).paragraphs)
    assert "[1]" in text and "Sustainable decommissioning" in text and "old list" not in text
    assert {"Paper"} <= set(library.load()["projects"])


def test_fix_typed_document(tmp_path, monkeypatch):
    import library
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    d = docx.Document()
    d.add_paragraph("Costs are high [1], recycling is hard [2] and see [3].")
    d.add_heading("References", level=1)
    d.add_paragraph("[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, pp.470-480. doi:10.1016/j.renene.2016.10.066")
    d.add_paragraph("[2] Vetters, J., Thomassen, G. and Van Passel, S. (2024) 'Sailing through end-of-life challenges: "
                    "A comprehensive review for offshore wind', Renewable and Sustainable Energy Reviews, 199, 114486.")
    d.add_paragraph("[3] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, 470-480.")
    path, out = str(tmp_path / "t.docx"), str(tmp_path / "t_fixed.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    res = doccheck.fix(path, rep, out, "apa")
    assert res["merged"] == ["[3] merged into [1]"] and res["entries"] == 2
    text = "\n".join(p.text for p in docx.Document(out).paragraphs)
    assert "Topham" in text and "(Topham" in text   # APA citations in the text


def test_pdf_cannot_be_fixed(tmp_path):
    try:
        doccheck.fix("x.pdf", {"source": "pdf"}, str(tmp_path / "o.docx"))
        raise AssertionError
    except ValueError as e:
        assert "PDF" in str(e)


def test_pdf_short_numbered_list(tmp_path):
    path = str(tmp_path / "short.pdf")
    make_pdf(path, [["Costs are underestimated [1] and planning matters [4]."],
                    ["References", "[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an",
                     "offshore wind farm. Renewable Energy, 102, pp.470-480.",
                     "[2] Burton, T. and Jenkins, N. (2011). Wind Energy Handbook. Wiley, Chichester."]])
    rep = doccheck.check(path, offline=True)
    assert rep["references"] == 2
    assert any("[4]" in i["text"] for i in rep["citation_issues"])


def _mendeley_cite_citation(items, shown):
    import base64 as b64
    payload = {"citationID": "MENDELEY_CITATION_x", "properties": {"noteIndex": 0}, "isEdited": False,
               "citationItems": [{"id": c["id"], "itemData": c, "isTemporary": False} for c in items]}
    tag = "MENDELEY_CITATION_v3_" + b64.b64encode(json.dumps(payload).encode()).decode()
    return parse_xml(f'<w:sdt {nsdecls("w")}><w:sdtPr><w:tag w:val="{tag}"/><w:id w:val="1"/></w:sdtPr>'
                     f'<w:sdtContent><w:r><w:t>{escape(shown)}</w:t></w:r></w:sdtContent></w:sdt>')


def make_mendeley_cite_doc(path):
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    p._p.append(_mendeley_cite_citation([TOPHAM], "(Topham, 2017)"))
    p.add_run(" and reviewed ")
    p._p.append(_mendeley_cite_citation([VETTERS, dict(TOPHAM, id="t2")], "(Vetters, 2024; Topham, 2017)"))
    p.add_run(".")
    d.add_heading("References", level=1)
    bib = parse_xml(f'<w:sdt {nsdecls("w")}><w:sdtPr><w:tag w:val="MENDELEY_BIBLIOGRAPHY"/></w:sdtPr><w:sdtContent>'
                    '<w:p><w:r><w:t>Topham, E. (2017). Sustainable decommissioning of an offshore wind farm.</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>Vetters, J. (2024). Sailing through end-of-life challenges.</w:t></w:r></w:p>'
                    '</w:sdtContent></w:sdt>')
    d.element.body.insert(len(d.element.body) - 1, bib)
    d.save(path)


def test_mendeley_cite_document(tmp_path, monkeypatch):
    import library
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    path, out = str(tmp_path / "mc.docx"), str(tmp_path / "mc_fixed.docx")
    make_mendeley_cite_doc(path)
    rep = doccheck.check(path, offline=True)
    assert rep["source"] == "mendeley_cite" and rep["manager"] == "Mendeley Cite" and rep["references"] == 3
    texts = " ".join(i["text"] for i in rep["citation_issues"])
    assert "listed more than once" in texts
    assert "No reference list" not in texts           # the list inside the content control was found
    res = doccheck.fix(path, rep, out, "apa")
    assert res["merged"] == ["[3] merged into [1]"] and res["entries"] == 2
    d = docx.Document(out)
    xml = d.element.xml
    assert "MENDELEY_CITATION_v3" not in xml and xml.count("ADDIN CSL_CITATION") == 2 and "CSL_BIBLIOGRAPHY" in xml
    text = "\n".join(p.text for p in doccheck.core.body_paragraphs(d))
    assert "(Topham" in text and "Sailing through" in text


def test_reference_list_inside_a_content_control_is_found(tmp_path):
    d = docx.Document()
    d.add_paragraph("Costs are high [1].")
    d.add_heading("References", level=1)
    bib = parse_xml(f'<w:sdt {nsdecls("w")}><w:sdtPr><w:tag w:val="Bibliography"/></w:sdtPr><w:sdtContent>'
                    '<w:p><w:r><w:t>[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore '
                    'wind farm. Renewable Energy, 102, 470-480.</w:t></w:r></w:p></w:sdtContent></w:sdt>')
    d.element.body.insert(len(d.element.body) - 1, bib)
    path = str(tmp_path / "w.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    assert rep["source"] == "typed" and rep["references"] == 1


def test_markers_from_claude(tmp_path, monkeypatch):
    import library
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    with library.editing() as data:
        library.add_csl(data, dict(TOPHAM), key="Topham2017")
    d = docx.Document()
    d.add_paragraph("Decommissioning is costly [@Topham2017] and unknown [@Nobody1999].")
    path, out = str(tmp_path / "claude.docx"), str(tmp_path / "claude_fixed.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    assert rep["source"] == "citrello" and rep["references"] == 1
    assert any("[@Nobody1999]" in i["text"] for i in rep["citation_issues"])
    res = doccheck.fix(path, rep, out, "apa")
    assert res["entries"] == 1 and "ADDIN CSL_CITATION" in docx.Document(out).element.xml


def test_failed_lookups_dont_stop_the_check(tmp_path, monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("NetworkError: Failed to execute 'send' on 'XMLHttpRequest'")
    monkeypatch.setattr(doccheck.core, "crossref_lookup", boom)
    path = str(tmp_path / "paper.pdf")
    make_pdf(path, [["Costs are underestimated [1] and planning matters [4]."],
                    ["References", "[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an",
                     "offshore wind farm. Renewable Energy, 102, pp.470-480.",
                     "[2] Burton, T. and Jenkins, N. (2011). Wind Energy Handbook. Wiley, Chichester."]])
    rep = doccheck.check(path, offline=False)
    assert len(calls) == 2 and rep["references"] == 2
    assert any("[4]" in i["text"] for i in rep["citation_issues"])
