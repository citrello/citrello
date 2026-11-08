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
    assert {"Topham2017", "vetters2024"} <= keys   # BibTeX keys are kept, so \\cite{vetters2024} keeps working


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
    # every tool says what it does to the user's things (ChatGPT's and Claude's directories require it)
    assert set(mcp_server.ANNOTATIONS) == set(mcp_server.TOOLS)
    by_name = {t["name"]: t["annotations"] for t in tools}
    assert by_name["search_references"]["readOnlyHint"] and not by_name["add_reference"]["readOnlyHint"]
    assert by_name["update_word_document"]["destructiveHint"] and by_name["add_reference"]["openWorldHint"]
    library.import_items(library.parse_any(RIS, "x.ris"))
    res = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "search_references", "arguments": {"query": "Topham"}}})
    assert json.loads(res["result"]["content"][0]["text"])["matches"] >= 1
    bad = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                             "params": {"name": "get_reference", "arguments": {"key": "Nope"}}})
    assert bad["result"]["isError"]


# ---------------- Codex ----------------

def test_codex_config_keeps_other_settings(tmp_path, monkeypatch):
    import tomllib
    import codex_config
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '# mine\nmodel = "gpt-5.5"\n\n[mcp_servers.other]\ncommand = "npx"\n\n'
        '[mcp_servers.citrello]\ncommand = "old.exe"\n\n[mcp_servers.citrello.env]\nX = "1"\n\n[profiles.fast]\nmodel = "m"\n')
    entry = {"command": "C:\\Users\\O'Brien\\Citrello\\CitrelloMCP.exe", "args": []}   # a quote in the path
    assert codex_config.connect(entry)["up_to_date"]
    cfg = tomllib.loads((tmp_path / "config.toml").read_text())
    assert cfg["model"] == "gpt-5.5" and cfg["profiles"]["fast"]["model"] == "m"
    assert cfg["mcp_servers"]["other"]["command"] == "npx"
    assert cfg["mcp_servers"]["citrello"] == {"command": entry["command"], "args": [], "startup_timeout_sec": 30}


def test_keys_from_nordic_and_other_letters():
    assert library.make_key({"author": [{"family": "Møller"}], "issued": {"date-parts": [[2019]]}, "title": "t"}, set()) == "Moller2019"
    assert library.make_key({"author": [{"family": "Weiß"}], "issued": {"date-parts": [[2020]]}, "title": "t"}, set()) == "Weiss2020"
    assert library.make_key({"type": "webpage", "title": "x", "container-title": "Ørsted"}, set()).startswith("Orsted")


def test_duplicates_part1_part2_and_different_dois():
    idx = core.LibraryIndex()
    idx.add("a", "Offshore wind farm decommissioning costs: Part 1", "10.1/a", 2020)
    assert idx.find({"title": "Offshore wind farm decommissioning costs: Part 2", "issued": {"date-parts": [[2020]]}})[0] is None
    assert idx.find({"title": "Offshore wind farm decommissioning costs: Part 1", "DOI": "10.1/b"})[0] is None
    assert idx.find({"title": "Offshore wind farm decommissioning costs - Part 1"})[0] == "a"   # same paper, punctuation
    assert idx.find({"title": "Offshore wind farm decomissioning costs: Part 1"})[0] == "a"    # typo still matches
    assert idx.find({"title": "something else", "DOI": "https://doi.org/10.1/A"})[0] == "a"     # same DOI


def test_change_projects_for_many(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    with library.editing() as d:
        for i, t in enumerate(["Alpha study of wind farms", "Beta study of turbines", "Gamma study of blades"]):
            library.add_csl(d, {"type": "article-journal", "title": t, "issued": {"date-parts": [[2020 + i]]},
                                "author": [{"family": f"X{i}"}]}, "Old")
    keys = [it["key"] for it in library.load()["items"]]
    assert library.change_projects(keys[:2], add=["New"]) == 2
    assert library.change_projects(keys[:2], remove=["Old"]) == 2
    assert library.change_projects(keys[2:], replace=["Moved"]) == 1
    by = {it["key"]: it["projects"] for it in library.load()["items"]}
    assert by[keys[0]] == ["New"] and by[keys[2]] == ["Moved"]
    assert {"New", "Moved"} <= set(library.load()["projects"])


ENDNOTE_XML = """<?xml version="1.0" encoding="UTF-8" ?><xml><records>
<record><database name="My EndNote Library.enl">My EndNote Library.enl</database><rec-number>1</rec-number>
<ref-type name="Journal Article">17</ref-type>
<contributors><authors><author><style face="normal" font="default" size="100%">Topham, Eva</style></author>
<author><style face="normal" font="default" size="100%">McMillan, David</style></author></authors></contributors>
<titles><title><style face="normal" font="default" size="100%">Sustainable decommissioning of an offshore wind farm</style></title>
<secondary-title><style face="normal" font="default" size="100%">Renewable Energy</style></secondary-title></titles>
<pages><style face="normal" font="default" size="100%">470-480</style></pages><volume><style>102</style></volume>
<dates><year><style face="normal" font="default" size="100%">2017</style></year></dates>
<electronic-resource-num><style face="normal" font="default" size="100%">https://doi.org/10.1016/j.renene.2016.10.066</style></electronic-resource-num>
</record>
<record><ref-type name="Book">6</ref-type><contributors><authors><author>Burton, Tony</author></authors>
<secondary-authors><author>Jenkins, Nick</author></secondary-authors></contributors>
<titles><title>Wind Energy Handbook</title></titles><publisher>Wiley</publisher><pub-location>Chichester</pub-location>
<isbn>9780470699751</isbn><dates><year>2011</year></dates></record>
<record><ref-type name="Journal Article">17</ref-type><titles><title></title></titles></record>
</records></xml>"""


def test_endnote_xml_import():
    items = library.parse_any(ENDNOTE_XML, "My EndNote Library.xml")
    assert len(items) == 2  # the record without a title is skipped
    a, b = items
    assert a["type"] == "article-journal" and a["DOI"] == "10.1016/j.renene.2016.10.066"
    assert a["author"][0] == {"family": "Topham", "given": "Eva"} and a["container-title"] == "Renewable Energy"
    assert a["issued"] == {"date-parts": [[2017]]} and a["page"] == "470-480" and a["volume"] == "102"
    assert b["type"] == "book" and b["ISBN"] == "9780470699751" and b["editor"][0]["family"] == "Jenkins"
    assert library.parse_any(ENDNOTE_XML.replace('<?xml version="1.0" encoding="UTF-8" ?>', ""), "export.txt")[0]["title"]
    with pytest.raises(ValueError):
        library.parse_any("<xml><records><record><titles>", "broken.xml")


def test_adding_an_existing_reference_to_a_project_lists_the_project():
    """Adding a paper that's already in the library "into a project" puts the project in the sidebar's list too."""
    c = {"type": "article-journal", "title": "A paper added twice for the project list", "DOI": "10.9999/twice.1"}
    with library.editing() as data:
        library.add_csl(data, dict(c))
    with library.editing() as data:
        it, created, _ = library.add_csl(data, dict(c), project="Second look")
    data = library.load()
    assert not created and "Second look" in it["projects"] and "Second look" in data["projects"]


def test_http_json_tries_once_more_after_a_dropped_connection(monkeypatch):
    """A dropped connection (in the web app: a browser NetworkError) is tried once more, then reported as "no
    connection"; a refused connection (offline, or no server) and server answers aren't retried."""
    import urllib.error
    import citrello_core as core
    calls = []
    monkeypatch.setattr(core.time, "sleep", lambda s: None)

    class Resp:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": 1}'

    def flaky(req, timeout=30):
        calls.append(1)
        if len(calls) == 1:
            raise Exception("NetworkError: Failed to execute 'send' on 'XMLHttpRequest'")
        return Resp()
    monkeypatch.setattr(core.urllib.request, "urlopen", flaky)
    assert core.http_json("https://example.org/x")[0] == {"ok": 1} and len(calls) == 2

    def always(req, timeout=30):
        calls.append(1)
        raise Exception("NetworkError: Failed to load")
    calls.clear()
    monkeypatch.setattr(core.urllib.request, "urlopen", always)
    with pytest.raises(urllib.error.URLError):
        core.http_json("https://example.org/x")
    assert len(calls) == 2

    def refused(req, timeout=30):
        calls.append(1)
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    calls.clear()
    monkeypatch.setattr(core.urllib.request, "urlopen", refused)
    with pytest.raises(urllib.error.URLError):
        core.http_json("https://example.org/x")
    assert len(calls) == 1


def test_http_json_turns_an_error_answer_into_an_http_error(monkeypatch):
    """In the web app a 404 comes back as an ordinary answer; it must still be "not found" (an HTTPError), so a wrong
    DOI leads to a search by title instead of a broken lookup."""
    import urllib.error
    import citrello_core as core

    class Resp:
        status = 404
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"Resource not found."
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda req, timeout=30: Resp())
    with pytest.raises(urllib.error.HTTPError) as e:
        core.http_json("https://api.crossref.org/works/10.1/x")
    assert e.value.code == 404


def test_http_json_waits_and_retries_once_when_told_too_many_requests(monkeypatch):
    import urllib.error
    import citrello_core as core
    calls, waits = [], []
    monkeypatch.setattr(core.time, "sleep", lambda s: waits.append(s))

    class Resp:
        def __init__(self, status, body): self.status, self.body, self.headers = status, body, {"Retry-After": "3"}
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body
    def urlopen(req, timeout=30):
        calls.append(1)
        return Resp(429, b"slow down") if len(calls) == 1 else Resp(200, b'{"ok": 1}')
    monkeypatch.setattr(core.urllib.request, "urlopen", urlopen)
    assert core.http_json("https://api.crossref.org/works/x")[0] == {"ok": 1} and waits == [3.0] and len(calls) == 2
