"""Check my sources: each cited sentence next to the passages of the paper's full text that match it (no AI)."""
import docx

import fulltext
import library
import sources
from test_doccheck import TOPHAM, VETTERS, add_field, csl_field

PAPER = ("Abstract\n\nOffshore wind farms built in the 1990s are now reaching the end of their design life.\n\n"
         "Methods\n\nWe modelled the decommissioning of a 30-turbine farm, including foundations and cables.\n\n"
         "Results\n\nRemoving the subsea export cables accounted for 42% of the total decommissioning cost, more than "
         "the turbines themselves. Leaving the cables buried in place halved that cost. Vessel day rates were the "
         "largest uncertainty.\n\n" + "Discussion of further unrelated matters about policy and ports. " * 40)


def _library(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    with library.editing() as data:
        library.add_csl(data, dict(TOPHAM), key="Topham2017")
        library.add_csl(data, dict(VETTERS), key="Vetters2024")
    fulltext.attach("Topham2017", PAPER, "pdf")


def test_markers_find_the_matching_passage(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    d = docx.Document()
    d.add_paragraph("Offshore wind is growing. Removing the export cables is the largest part of the decommissioning "
                    "cost [@Topham2017]. Reviews compare end-of-life options [@Vetters2024]. Smith et al. found the "
                    "same for onshore wind [@Nobody2020].")
    path = str(tmp_path / "m.docx")
    d.save(path)
    r = sources.check(path)
    assert r["counts"]["sentences"] == 3  # the uncited first sentence isn't listed; "et al." doesn't split
    first = r["sentences"][0]
    assert first["sentence"].startswith("Removing the export cables")
    p = first["papers"][0]
    assert p["status"] == "found" and p["key"] == "Topham2017"
    assert "42% of the total decommissioning cost" in p["passages"][0]["text"]
    assert r["sentences"][1]["papers"][0]["status"] == "no_text"          # in the library, no text yet
    assert r["sentences"][2]["sentence"].startswith("Smith et al. found")
    assert r["sentences"][2]["papers"][0]["status"] == "not_in_library"
    assert r["counts"] == dict(r["counts"], with_text=1, no_text=1, not_in_library=1)


def test_zotero_fields_and_typed_numbers(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    d = docx.Document()
    p = d.add_paragraph("Cable removal dominated the decommissioning cost ")
    add_field(p, csl_field([dict(TOPHAM, id="z1")], zotero=True), "(Topham and McMillan, 2017)")
    p.add_run(". Vessel day rates were uncertain.")
    path = str(tmp_path / "z.docx")
    d.save(path)
    r = sources.check(path)
    assert len(r["sentences"]) == 1
    s = r["sentences"][0]
    assert "(Topham and McMillan, 2017)" in s["sentence"]  # shown as it looks in the document
    assert s["papers"][0]["key"] == "Topham2017" and s["papers"][0]["status"] == "found"

    d = docx.Document()
    d.add_paragraph("Leaving the cables buried in place halved the cost [1]. Reviews disagree [2].")
    d.add_heading("References", level=1)
    d.add_paragraph("1. Topham, E. and McMillan, D. (2017) Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, pp. 470-480. doi:10.1016/j.renene.2016.10.066")
    d.add_paragraph("2. Someone, A. (2020) A paper nobody has. Journal of Things, 1, pp. 1-2.")
    path = str(tmp_path / "t.docx")
    d.save(path)
    r = sources.check(path)
    assert [s["papers"][0]["status"] for s in r["sentences"]] == ["found", "not_in_library"]
    assert "halved that cost" in r["sentences"][0]["papers"][0]["passages"][0]["text"]


def test_only_word_documents(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        sources.check(str(tmp_path / "a.pdf"))


def test_the_papers_own_reference_list_is_left_out():
    text = "Intro.\n\n" + "Body sentence about cables. " * 60 + "\n\nReferences\n\n1. Someone (2001) A cited paper about cables."
    assert "A cited paper" not in sources.body_text(text) and "Body sentence" in sources.body_text(text)


def test_what_claude_reads(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    d = docx.Document()
    d.add_paragraph("Taking the power lines out was the costliest operation [@Topham2017]. Reviews compare options [@Vetters2024].")
    path = str(tmp_path / "c.docx")
    d.save(path)
    items = sources.ai_items(sources.check(path))
    assert len(items) == 1  # Vetters has no text: nothing for Claude to read
    it = items[0]
    assert it["claim"] == "Taking the power lines out was the costliest operation."  # the citation taken out
    assert it["papers"][0]["key"] == "Topham2017"
    assert any("42% of the total decommissioning cost" in p for p in it["papers"][0]["passages"])
    assert it["papers"][0]["passages"][0].startswith("Abstract")  # the opening first


def test_library_candidates(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    got = sources.library_candidates("Removing subsea cables is the largest decommissioning cost.")
    assert [p["key"] for p in got] == ["Topham2017"] and got[0]["full_text"]
    assert sources.library_candidates("Removing subsea cables is the largest decommissioning cost.", exclude=["Topham2017"]) == []


def test_each_citation_shows_as_in_the_paper(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    d = docx.Document()
    d.add_paragraph("Leaving the cables buried halved the cost [1]. Reviews disagree [2].")
    d.add_heading("References", level=1)
    d.add_paragraph("1. Topham, E. and McMillan, D. (2017) Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, pp. 470-480. doi:10.1016/j.renene.2016.10.066")
    d.add_paragraph("2. Someone, A. (2020) A paper nobody has. Journal of Things, 1, pp. 1-2.")
    path = str(tmp_path / "t.docx")
    d.save(path)
    assert [s["papers"][0]["cite"] for s in sources.check(path)["sentences"]] == ["[1]", "[2]"]
    d = docx.Document()
    d.add_paragraph("Removing cables is costly [@Topham2017].")
    d.save(path)
    assert sources.check(path)["sentences"][0]["papers"][0]["cite"] == "[@Topham2017]"


def test_word_addin_checks_the_citation_clicked(tmp_path, monkeypatch):
    """Citrello for Word: the sentence around the citation, the paper's text (found online when missing, the paper
    added to the library if needed), and what Claude reads."""
    _library(tmp_path, monkeypatch)
    para = ("Offshore wind is growing. Removing the export cables is the largest part of the decommissioning cost "
            "(Topham et al., 2017; Vetters, 2024). Smith et al. found the same.")
    at = para.index("(Topham")
    assert sources.sentence_at(para, at).startswith("Removing the export cables")
    assert sources.clean_claim(sources.sentence_at(para, at)) == \
        "Removing the export cables is the largest part of the decommissioning cost."

    assert sources.word_paper_text("Topham2017", TOPHAM)["status"] == "text"
    calls = []

    def find_free(keys=None, progress=None):  # a free copy found online for Vetters
        calls.append(keys)
        fulltext.attach(keys[0], PAPER.replace("42%", "40%"), "open access")
        return {"found": 1}
    monkeypatch.setattr(fulltext, "find_free", find_free)
    got = sources.word_paper_text("Vetters2024", VETTERS)
    assert got == {"key": "Vetters2024", "status": "text", "changed": True, "doi": VETTERS["DOI"], "words": got["words"]}
    assert calls == [["Vetters2024"]]
    # not in the library: added (under the citation's key) before its text is looked for
    new = {"type": "article-journal", "title": "Cable recovery costs", "DOI": "10.1/cables", "issued": {"date-parts": [[2021]]}}
    got = sources.word_paper_text("Lee2021", new)
    assert got["key"] == "Lee2021" and got["changed"] and library.get(library.load(), "Lee2021")
    monkeypatch.setattr(fulltext, "find_free", lambda keys=None, progress=None: {"found": 0})
    assert sources.word_paper_text("Nope", {"title": "No DOI, no text"})["status"] == "none"

    out = sources.word_claim_item(para, at, ["Topham2017", "Vetters2024", "Nope"])
    item = out["item"]
    assert item["claim"] == "Removing the export cables is the largest part of the decommissioning cost."
    assert [p["ref"] for p in item["papers"]] == ["Topham2017", "Vetters2024"]          # "Nope" has no text
    assert any("42%" in x for x in item["papers"][0]["passages"])
    assert sources.word_claim_item(para, at, ["Nope"])["item"] is None
