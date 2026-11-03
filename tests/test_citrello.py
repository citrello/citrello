import json
import re
import zipfile

import docx
import pytest

import citeformat
import citrello_core as core
import library
import word_addin

REFS = [
    "Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
    "Renewable Energy, 102, pp.470-480. doi:10.1016/j.renene.2016.10.066.",
    "Vetters, J., Thomassen, G. and Van Passel, S. (2024) 'Sailing through end-of-life challenges: "
    "A comprehensive review for offshore wind', Renewable and Sustainable Energy Reviews, 199, 114486.",
    "Bilgili, M., & Alphan, H. (2022). Global growth in offshore wind turbine technology. "
    "Clean Technologies and Environmental Policy, 24.",
]


def make_doc(path, body, refs, numbered):
    d = docx.Document()
    d.add_paragraph(body)
    d.add_heading("References", level=1)
    for i, r in enumerate(refs, 1):
        d.add_paragraph(f"[{i}] {r}" if numbered else r)
    d.save(path)
    return path


def fields(path):
    x = zipfile.ZipFile(path).read("word/document.xml").decode()
    return x.count("CSL_CITATION"), x.count("CSL_BIBLIOGRAPHY"), (x.count('"begin"'), x.count('"separate"'), x.count('"end"'))


# ---------------- reference parsing ----------------

def test_parse_harvard_reference():
    c = core.parse_reference(REFS[0])
    assert [a["family"] for a in c["author"]] == ["Topham", "McMillan"]
    assert core.year_of(c) == "2017" and c["DOI"] == "10.1016/j.renene.2016.10.066"
    assert c["title"].startswith("Sustainable decommissioning")


def test_parse_ieee_reference():
    c = core.parse_reference('A. Vaswani, N. Shazeer, and N. Parmar, "Attention is all you need," in Proc. '
                             'Advances in Neural Information Processing Systems, vol. 30, pp. 5998-6008, 2017.')
    assert c["title"] == "Attention is all you need" and c["type"] == "paper-conference"
    assert [a["family"] for a in c["author"]] == ["Vaswani", "Shazeer", "Parmar"]


@pytest.mark.parametrize("raw,clean", [
    ("10.1002/PIP.2843;ISSUE:ISSUE:DOI", "10.1002/pip.2843"),
    ("10.1002/we.1801(Accessed:", "10.1002/we.1801"),
    ("https://doi.org/10.1364/OE.510421", "10.1364/oe.510421"),
    ("10.1016/s0140-6736(20)30183-5", "10.1016/s0140-6736(20)30183-5"),   # real DOI with brackets
])
def test_clean_doi(raw, clean):
    assert core.clean_doi(raw) == clean


def test_doi_candidates_trim_trailing_ids():
    assert core.doi_candidates("10.1063/1.4812391/138579") == ["10.1063/1.4812391/138579", "10.1063/1.4812391"]


@pytest.mark.parametrize("url,doi", [
    ("https://onlinelibrary.wiley.com/doi/full/10.1002/adom.202402853", "10.1002/adom.202402853"),
    ("https://www.nature.com/articles/s41565-017-0052-4", "10.1038/s41565-017-0052-4"),
    ("https://arxiv.org/abs/2401.12345v2", "10.48550/arxiv.2401.12345"),
    ("https://orsted.com/en/what-we-do/recycling", None),
])
def test_doi_from_url(url, doi):
    assert library.doi_from_url(url) == doi


# ---------------- converting typed citations ----------------

def records(refs):
    recs = [core.resolve_reference(i, r, offline=True) for i, r in enumerate(refs, 1)]
    core.assess_references(recs)
    return recs


def test_convert_numbered(tmp_path):
    src = make_doc(tmp_path / "num.docx", "Wind is growing [1], [2, 3] and again [1-3]. A year [2020] stays.", REFS, True)
    res = core.convert_document(str(src), records(REFS), out=str(tmp_path / "num_out.docx"))
    assert res["mode"] == "numeric" and res["converted"] == 3
    assert any("2020" in r for r in res["report"])
    n_cite, n_bib, (b, s, e) = fields(tmp_path / "num_out.docx")
    assert n_cite == 3 and n_bib == 1 and b == s == e


def test_convert_author_year(tmp_path):
    body = ("Decommissioning (Topham & McMillan, 2017; Vetters, Thomassen and Van Passel, 2024) matters, "
            "as Bilgili and Alphan (2022) note (e.g. Topham and McMillan, 2017, p. 5).")
    src = make_doc(tmp_path / "ay.docx", body, REFS, False)
    res = core.convert_document(str(src), records(REFS), out=str(tmp_path / "ay_out.docx"))
    assert res["mode"] == "author-year" and res["converted"] == 3 and res["cited"] == [1, 2, 3]


# ---------------- library ----------------

RIS = """TY  - JOUR
AU  - Topham, Eva
AU  - McMillan, David
TI  - Sustainable decommissioning of an offshore wind farm
JO  - Renewable Energy
PY  - 2017
VL  - 102
DO  - 10.1016/j.renene.2016.10.066
ER  - 
"""
BIB = """@article{vetters2024,
  title = {Sailing through end-of-life challenges: {A} comprehensive review for offshore wind},
  author = {Vetters, Jan and Thomassen, Gwenny and Van Passel, Steven},
  journal = {Renewable and Sustainable Energy Reviews}, year = {2024}, volume = {199},
  doi = {10.1016/j.rser.2024.114486}}
"""


def test_import_and_duplicates():
    assert library.import_items(library.parse_any(RIS, "a.ris"), project="Test") == (1, 0)
    assert library.import_items(library.parse_any(BIB, "b.bib")) == (1, 0)
    assert library.import_items(library.parse_any(RIS, "a.ris")) == (0, 1)   # same paper again
    keys = {it["key"] for it in library.load()["items"]}
    assert {"Topham2017", "Vetters2024"} <= keys


def test_export_roundtrip(tmp_path):
    library.import_items(library.parse_any(RIS, "a.ris"))
    data = library.load()
    for fmt in ("ris", "bib", "json"):
        p = library.export(data["items"], fmt, str(tmp_path / f"x.{fmt}"))
        again = library.parse_any(open(p, encoding="utf-8").read(), p)
        assert any(c.get("title", "").startswith("Sustainable decommissioning") for c in again)


# ---------------- live documents ----------------

@pytest.mark.parametrize("style,expect", [("apa", "(Topham & McMillan, 2017"), ("ieee", "[1]"),
                                          ("harvard-cite-them-right", "(Topham and McMillan, 2017")])
def test_create_and_restyle_document(tmp_path, style, expect):
    library.import_items(library.parse_any(RIS + BIB, "x.ris") + library.parse_any(BIB, "x.bib"))
    out = tmp_path / f"draft_{style}.docx"
    res = citeformat.create_document("# Intro\nDecommissioning [@Topham2017; @Vetters2024]. Missing [@Nobody1999].",
                                     str(out), style, title="Draft")
    assert res["citations"] == 1 and res["entries"] == 2
    assert any("Nobody1999" in r for r in res["report"])
    text = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert expect in text


def test_deleted_citation_text_is_dropped(tmp_path):
    library.import_items(library.parse_any(RIS, "x.ris") + library.parse_any(BIB, "x.bib"))
    out = tmp_path / "d.docx"
    citeformat.create_document("A [@Topham2017]. B [@Vetters2024].", str(out), "ieee")
    d = docx.Document(str(out))
    f = [f for f in citeformat.find_fields(d.element.body) if "CSL_CITATION" in f["instr"]][0]
    for r in f["result"]:
        for t in r.findall(docx.oxml.ns.qn("w:t")):
            t.text = ""                     # the user deleted the visible "[1]"
    d.save(str(out))
    res = citeformat.update_document(str(out), str(out), "ieee")
    assert res["citations"] == 1 and res["entries"] == 1


def test_render_codes_for_word():
    library.import_items(library.parse_any(RIS, "x.ris"))
    code = citeformat.code_for_group([{"key": "Topham2017", "locator": "12"}])
    res = citeformat.render_codes([" " + code + " ", " " + code[:255] + " ", " HYPERLINK x "], "apa")
    assert res["citations"][0]["text"] == "(Topham & McMillan, 2017, p. 12)"
    assert res["bad"] == [1] and res["citations"][2] is None
    lines = citeformat.render_lines(res).split("\n")
    assert lines[0].startswith("STYLE\t") and "BAD\t1" in lines


# ---------------- Word macro and Claude extension ----------------

def test_vba_parts_are_well_formed():
    for code in (word_addin.vba_code(), word_addin.FORM_CITE, word_addin.FORM_STYLE,
                 word_addin.EVENTS_CLASS, word_addin.EVENTS_START):
        word_addin._check_vba(code)
        assert all(ord(c) < 127 for c in code), "the Word macro must stay plain ASCII"
        procs = len(re.findall(r"^(?:Public |Private )?(?:Sub|Function) ", code, re.M))
        assert procs == len(re.findall(r"^End (?:Sub|Function)", code, re.M))
    for cb in re.findall(r'onAction="(\w+)"', word_addin.RIBBON):
        assert f"Public Sub {cb}(control As IRibbonControl)" in word_addin.vba_code()


def test_mcpb_bundle(tmp_path):
    import make_mcpb
    exe = tmp_path / "CitrelloMCP.exe"
    exe.write_bytes(b"MZ")
    out = make_mcpb.build(str(exe), str(tmp_path / "Citrello.mcpb"))
    z = zipfile.ZipFile(out)
    m = json.loads(z.read("manifest.json"))
    assert m["name"] == "citrello" and m["server"]["type"] == "binary" and len(m["tools"]) >= 13
    assert {"manifest.json", "icon.png", "server/CitrelloMCP.exe"} <= set(z.namelist())


def test_connector_protocol():
    import mcp_server
    init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "citrello"
    tools = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert {"search_references", "add_reference", "create_word_document", "verify_references"} <= {t["name"] for t in tools}
    library.import_items(library.parse_any(RIS, "x.ris"))
    res = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "search_references", "arguments": {"query": "Topham"}}})
    assert json.loads(res["result"]["content"][0]["text"])["matches"] >= 1
    bad = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "get_reference", "arguments": {"key": "Nope"}}})
    assert bad["result"]["isError"]
