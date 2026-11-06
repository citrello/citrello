"""Finding and adding citation styles: the offline catalogue of every CSL style, journal aliases, refusals."""
import os

import pytest

import citeformat
import mcp_server

DEPENDENT = """<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0" default-locale="en-US">
  <info><title>Journal of Testing Things</title><id>http://www.zotero.org/styles/{id}</id>
    <link href="http://www.zotero.org/styles/{id}" rel="self"/>
    <link href="http://www.zotero.org/styles/{parent}" rel="independent-parent"/>
    <category citation-format="{fmt}"/></info>
</style>"""


def _fake_download(sid, parent="nature", fmt="numeric"):
    d = citeformat._user_dir()
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, sid + ".csl"), "w", encoding="utf-8") as f:
        f.write(DEPENDENT.format(id=sid, parent=parent, fmt=fmt))


def test_catalogue_has_every_style():
    cat = citeformat.catalog()
    assert len(cat) > 10000
    assert sum(1 for s in cat if s["format"] != "note") > 10000


@pytest.mark.parametrize("query, expect", [
    ("journal of cleaner production", "journal-of-cleaner-production"),
    ("plos one", "plos-one"),
    ("J Clin Oncol", "journal-of-clinical-oncology"),
    ("Lancet", "the-lancet"),
    ("ieee", "ieee"),           # installed styles come first
])
def test_search_finds_journals(query, expect):
    hits = citeformat.search_styles(query)
    assert hits and hits[0]["id"] == expect, [h["id"] for h in hits[:5]]


def test_search_marks_footnote_styles():
    hits = citeformat.search_styles("oscola")
    assert hits and not any(h["usable"] for h in hits)
    assert citeformat.search_styles("") == [] and citeformat.search_styles("zzqqxx") == []


def test_install_journal_alias_keeps_its_name():
    _fake_download("journal-of-testing-things")
    info = citeformat.install_style("journal-of-testing-things")
    assert info == {"id": "journal-of-testing-things", "title": "Journal of Testing Things", "format": "numeric"}
    assert "journal-of-testing-things" in {s["id"] for s in citeformat.list_styles()}
    r = citeformat.Renderer("journal-of-testing-things", {"k": citeformat._SAMPLE})
    assert r.info["id"] == "nature"  # the rules come from the parent


def test_install_refuses_footnote_styles_and_removes_them():
    _fake_download("journal-of-footnotes", parent="chicago-author-date", fmt="note")
    with pytest.raises(ValueError, match="Footnote"):
        citeformat.install_style("journal-of-footnotes")
    assert not os.path.exists(os.path.join(citeformat._user_dir(), "journal-of-footnotes.csl"))


def test_connector_searches_all_styles():
    res = mcp_server.t_styles("Journal of Cleaner Production")
    assert res["styles"][0]["id"] == "journal-of-cleaner-production"
    assert all("usable" not in s for s in res["styles"])
    assert {s["id"] for s in mcp_server.t_styles()["styles"]} >= {"apa", "ieee"}
