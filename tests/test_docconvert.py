"""Turning a document's citations (Zotero, Mendeley, EndNote, typed...) into Citrello for Word's citations."""
import base64
import json

import docx
import pytest
from docx.oxml.ns import qn
from lxml import etree

import doccheck
import docconvert
import library
from test_doccheck import TOPHAM, VETTERS, add_field, csl_field, make_mendeley_cite_doc

PKG = ('<pkg:package xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage"><pkg:part pkg:name="/word/document.xml" '
       'pkg:contentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"><pkg:xmlData>'
       '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{}</w:body></w:document>'
       '</pkg:xmlData></pkg:part></pkg:package>')


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))


def word_ooxml(path, target):
    """What Word's paragraph.getOoxml() gives for the plan's target paragraph."""
    paras = docconvert.body_paras(docx.Document(path).element.body)
    same = [p for p in paras if docconvert.sig(docconvert.para_text(p)) == target["sig"]]
    return PKG.format(etree.tostring(same[target["nth"]], encoding="unicode"))


def citations(ooxml):
    """[(shown text, [keys], [items])] of the Citrello citations in converted XML."""
    root = etree.fromstring(ooxml.encode())
    out = []
    for sdt in root.iter(qn("w:sdt")):
        tag = sdt.find(qn("w:sdtPr") + "/" + qn("w:tag")).get(qn("w:val"))
        assert tag.startswith("CITRELLO_CITATION_v1_")
        payload = json.loads(base64.b64decode(tag[len("CITRELLO_CITATION_v1_"):]))
        text = "".join(t.text or "" for t in sdt.iter(qn("w:t")))
        out.append((text, [ci["id"] for ci in payload["citationItems"]], payload["citationItems"]))
    return out


def text_of(ooxml):
    return "".join(t.text or "" for t in etree.fromstring(ooxml.encode()).iter(qn("w:t")))


def test_zotero_fields_become_citrello_citations(tmp_path, lib):
    dup = dict(TOPHAM, id="t2", title=TOPHAM["title"] + ".")  # the same paper saved twice in Zotero
    with library.editing() as data:  # Topham is in the library: its citation uses the library's key
        library.add_csl(data, dict(TOPHAM), key="topham-lib")
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    add_field(p, csl_field([TOPHAM], zotero=True), "(Topham, 2017)")
    p.add_run(" today.")
    p = d.add_paragraph("And reviewed ")
    add_field(p, csl_field([VETTERS, dup], zotero=True), "(Vetters, 2024; Topham, 2017)")
    d.add_heading("References", level=1)
    d.add_paragraph("Topham, E. (2017). Sustainable decommissioning of an offshore wind farm.")
    d.add_paragraph("Vetters, J. (2024). Sailing through end-of-life challenges.")
    path = str(tmp_path / "zotero.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    plan, summary = docconvert.make_plan(path, rep)
    assert summary["citations"] == 2 and summary["by"] == {"Zotero": 2} and summary["already"] == 0
    assert [t["count"] for t in summary["paragraphs"]] == [1, 1]
    bib = summary["bibliography"]
    assert bib["kind"] == "paragraphs" and bib["count"] == 2 and bib["first"]["sig"].startswith("topham")

    out, n = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][0]))
    assert n == 1 and text_of(out) == "Decommissioning is costly (Topham, 2017) today."   # the text stays
    assert "instrText" not in out and "fldChar" not in out                              # the field is gone
    [(shown, keys, items)] = citations(out)
    assert shown == "(Topham, 2017)" and keys == ["topham-lib"]
    out, n = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][1]))
    [(shown, keys, items)] = citations(out)
    assert keys == ["Vetters2024", "topham-lib"]                                       # the duplicate is merged
    assert items[0]["itemData"]["title"].startswith("Sailing through") and items[0]["itemData"]["id"] == "Vetters2024"


def test_typed_numeric_citations(tmp_path, lib):
    d = docx.Document()
    d.add_paragraph("Costs are high [1], recycling is hard [2] and see also [5].")
    d.add_heading("References", level=1)
    d.add_paragraph("[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, pp.470-480.")
    d.add_paragraph("[2] Vetters, J., Thomassen, G. and Van Passel, S. (2024) 'Sailing through end-of-life challenges: "
                    "A comprehensive review for offshore wind', Renewable and Sustainable Energy Reviews, 199, 114486.")
    path = str(tmp_path / "typed.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    plan, summary = docconvert.make_plan(path, rep)
    assert summary["citations"] == 2 and summary["by"] == {"typed by hand": 2}
    assert any("[5]" in n for n in summary["notes"])                       # not in the list: left as typed
    assert summary["bibliography"]["count"] == 2
    out, n = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][0]))
    assert n == 2 and text_of(out) == "Costs are high [1], recycling is hard [2] and see also [5]."
    cits = citations(out)
    assert [c[0] for c in cits] == ["[1]", "[2]"] and cits[0][1] == ["Topham2017"] and cits[1][1] == ["Vetters2024"]


def test_author_year_typed_citations_keep_the_name_in_the_sentence(tmp_path, lib):
    d = docx.Document()
    d.add_paragraph("Topham and McMillan (2017) show costs are high (Vetters et al., 2024, p. 5).")
    d.add_heading("References", level=1)
    d.add_paragraph("Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, 470-480.")
    d.add_paragraph("Vetters, J., Thomassen, G. and Van Passel, S. (2024). Sailing through end-of-life challenges. "
                    "Renewable and Sustainable Energy Reviews, 199, 114486.")
    path = str(tmp_path / "ay.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    plan, summary = docconvert.make_plan(path, rep)
    assert summary["citations"] == 2
    out, _ = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][0]))
    cits = citations(out)
    assert cits[0][0] == "(2017)" and cits[0][2][0]["suppress-author"] is True          # "Topham and McMillan (2017)"
    assert cits[1][0] == "(Vetters et al., 2024, p. 5)" and cits[1][2][0]["locator"] == "5"


def test_mendeley_cite_endnote_and_markers(tmp_path, lib):
    path = str(tmp_path / "mc.docx")
    make_mendeley_cite_doc(path)
    rep = doccheck.check(path, offline=True)
    plan, summary = docconvert.make_plan(path, rep)
    assert summary["by"] == {"Mendeley Cite": 2} and summary["bibliography"] == {"kind": "cc", "tag": "MENDELEY_BIBLIOGRAPHY"}
    out, n = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][0]))
    assert n == 2 and "MENDELEY_CITATION" not in out and len(citations(out)) == 2
    assert "Citrello citation" in out

    with library.editing() as data:
        library.add_csl(data, dict(VETTERS), key="Vetters2024")
    d = docx.Document()
    p = d.add_paragraph("EndNote cites ")
    add_field(p, "ADDIN EN.CITE <EndNote><Cite><RecNum>12</RecNum><record><rec-number>12</rec-number><titles><title>"
                 "Sustainable decommissioning of an offshore wind farm</title></titles><contributors><authors><author>"
                 "Topham, Eva</author></authors></contributors><dates><year>2017</year></dates></record></Cite></EndNote>",
              "(Topham, 2017)")
    d.add_paragraph("Claude wrote this [@Vetters2024, p. 3].")
    path = str(tmp_path / "en.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    plan, summary = docconvert.make_plan(path, rep)
    assert summary["by"] == {"EndNote": 1, "[@Key] markers": 1}
    out, _ = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][0]))
    assert citations(out)[0][0] == "(Topham, 2017)"
    out, _ = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][1]))
    [(shown, keys, items)] = citations(out)
    assert shown == "[@Vetters2024, p. 3]" and keys == ["Vetters2024"] and items[0]["locator"] == "3"


def test_citrello_citations_are_left_alone(tmp_path, lib):
    d = docx.Document()
    p = d.add_paragraph("Already ")
    add_field(p, csl_field([TOPHAM]), "(Topham, 2017)")  # the web app's own field (Mendeley-compatible)
    path = str(tmp_path / "web.docx")
    d.save(path)
    rep = doccheck.check(path, offline=True)
    plan, summary = docconvert.make_plan(path, rep)
    out, n = docconvert.convert_ooxml(plan, word_ooxml(path, summary["paragraphs"][0]))
    converted = docx.Document(path)
    pkg = PKG.format(etree.tostring(converted.element.body.find(qn("w:p")), encoding="unicode"))
    once, _ = docconvert.convert_ooxml(plan, pkg)
    again, n2 = docconvert.convert_ooxml(plan, PKG.format(etree.tostring(
        etree.fromstring(once.encode()).find(".//" + qn("w:p")), encoding="unicode")))
    assert n2 == 0 and len(citations(again)) == 1                                   # converting twice changes nothing
    assert summary["by"] == {"Mendeley Desktop or Citrello": 1}
    with pytest.raises(ValueError):
        docconvert.convert_ooxml(plan, "<not xml")
