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
