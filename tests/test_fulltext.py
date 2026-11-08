"""Full texts: a paper's text kept next to its reference (never the PDF), matched by DOI or title, and read by the AI."""
import json
import os
import sys

import pytest

import fulltext
import library

sys.path.insert(0, os.path.dirname(__file__))
import test_doccheck as T  # noqa: E402

TOPHAM = {"type": "article-journal", "title": "Sustainable decommissioning of an offshore wind farm",
          "author": [{"family": "Topham", "given": "Eva"}], "issued": {"date-parts": [[2017]]},
          "container-title": "Renewable Energy", "DOI": "10.1016/j.renene.2016.10.066"}
BURTON = {"type": "book", "title": "Wind Energy Handbook", "author": [{"family": "Burton", "given": "Tony"}],
          "issued": {"date-parts": [[2011]]}, "publisher": "Wiley"}

BODY = [  # a paper's pages: lines of text (the PDF helper writes one line per entry)
    ["Renewable Energy 102 (2017) 470-480", "Sustainable decommissioning of an offshore wind farm",
     "Eva Topham, David McMillan", "https://doi.org/10.1016/j.renene.2016.10.066", "",
     "Abstract. Decommissioning offshore wind farms is a growing problem as the first farms reach the end of life."]
    + [f"Section {i}. The cost of removing foundations and cables depends on water depth and vessel day rates; "
       f"the study finds that decommissioning costs are underestimated by {10 + i} percent in planning." for i in range(12)],
    ["Results. Recycling the steel of monopiles recovers part of the cost, while composite blades remain hard to recycle."]
    + [f"Discussion {i}. Leaving scour protection in place can protect marine habitats and reduce cost." for i in range(12)],
]


@pytest.fixture
def lib():
    saved = library.load()
    with library.editing() as data:
        data.update(items=[], projects=[])
        library.add_csl(data, dict(TOPHAM), source="test")
        library.add_csl(data, dict(BURTON), source="test")
    yield {it["csl"]["title"]: it for it in library.load()["items"]}
    with library.editing() as data:
        data.clear()
        data.update(saved)


def pdf_bytes(tmp_path, pages=BODY, name="paper.pdf"):
    p = tmp_path / name
    T.make_pdf(str(p), pages)
    return p.read_bytes()


def test_clean_joins_lines_and_drops_page_furniture():
    raw = ("Journal of Things\nThe decommis-\nsioning of farms is costly.\n3\nJournal of Things\nMore text here.\n\n"
           "Journal of Things\nEven more.\nJournal of Things\nLast.")
    out = fulltext.clean(raw)
    assert "decommissioning" in out and "Journal of Things" not in out and "\n3\n" not in out


def test_pdf_text_and_match_by_doi_then_title(lib, tmp_path):
    text, _ = fulltext.pdf_text(pdf_bytes(tmp_path))
    assert "monopiles" in text and len(text) > fulltext.MIN_CHARS
    items = library.load()["items"]
    it, how = fulltext.match(text, items)
    assert it["csl"]["title"] == TOPHAM["title"] and how == "doi"
    no_doi = text.replace("10.1016/j.renene.2016.10.066", "")
    it, how = fulltext.match(no_doi, items)
    assert it["csl"]["title"] == TOPHAM["title"] and how == "title"
    assert fulltext.match("Something else entirely. " * 200, items) == (None, None)
    with pytest.raises(ValueError, match="isn't a PDF"):
        fulltext.pdf_text(b"PK\x03\x04")
    with pytest.raises(ValueError, match="scan"):
        fulltext.pdf_text(pdf_bytes(tmp_path, [["Just a cover page."]], "cover.pdf"))


def test_attach_read_remove_and_delete(lib, tmp_path):
    key = lib[TOPHAM["title"]]["key"]
    res = fulltext.attach_pdf(pdf_bytes(tmp_path))  # matched to its reference
    assert res["key"] == key and res["how"] == "doi" and res["words"] > 300
    it = library.get(library.load(), key)
    assert it["fulltext"]["source"] == "pdf" and "monopiles" in fulltext.read(it)
    assert not list((tmp_path).glob("*.txt"))  # nothing kept next to the PDF; the text is in the library folder
    path = os.path.join(fulltext.folder(), it["id"] + ".txt")
    assert os.path.exists(path)
    fulltext.remove(key)
    assert not library.get(library.load(), key).get("fulltext") and not os.path.exists(path)
    fulltext.attach_pdf(pdf_bytes(tmp_path), key)
    library.delete([key])  # deleting the reference deletes its text
    assert not os.path.exists(path)


def test_passages_find_the_part_that_matters(tmp_path):
    text, _ = fulltext.pdf_text(pdf_bytes(tmp_path))
    hits = fulltext.passages(text, "recycling composite blades", n=2)
    assert hits and "composite blades" in hits[0]["text"]
    assert all(0 <= h["at"] <= 100 for h in hits)
    assert fulltext.passages(text, "the and of") == []  # only stop words: nothing to rank


def test_ai_reads_papers(lib, tmp_path):
    import mcp_server
    key = lib[TOPHAM["title"]]["key"]
    burton = lib[BURTON["title"]]["key"]
    out = mcp_server.t_read_paper(burton)
    assert out["full_text"] is False and "browser button" in out["message"]
    fulltext.attach_pdf(pdf_bytes(tmp_path))
    out = mcp_server.t_read_paper(key, query="Is steel from monopiles recycled?")
    assert out["full_text"] and any("monopiles" in p["text"] for p in out["passages"])
    out = mcp_server.t_read_paper(key, max_chars=3000)
    assert out["truncated"] and len(out["text"]) == 3000
    found = mcp_server.t_find_in_papers("scour protection marine habitats")
    assert found["searched"] == 1 and found["without_full_text"] == 1
    assert found["results"][0]["key"] == key and "scour" in found["results"][0]["passage"]
    summary = json.loads(mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "search_references", "arguments": {"query": "decommissioning"}}})["result"]["content"][0]["text"])
    assert summary["references"][0]["full_text"] is True


def test_app_requests(lib, tmp_path, monkeypatch):
    """The same requests in the desktop app and the web app (here through the web app's handler)."""
    import app
    import webapp
    monkeypatch.setattr(app, "USER_CONFIG", str(tmp_path / "app.json"))
    key = lib[TOPHAM["title"]]["key"]
    burton = lib[BURTON["title"]]["key"]

    def call(method, path, body=None, headers=None):
        st, ctype, hdrs, out = webapp.handle(method, path, headers or {}, body)
        return st, (json.loads(out) if ctype == "application/json" and out else out)

    st, out = call("POST", "/api/fulltext/attach", pdf_bytes(tmp_path), {"X-Filename": "paper.pdf"})
    assert st == 200 and out["key"] == key and out["how"] == "doi"
    items = call("GET", "/api/library")[1]["items"]
    assert {i["key"]: i["fulltext"] > 0 for i in items} == {key: True, burton: False}
    assert call("GET", f"/api/library/item?key={key}")[1]["fulltext_info"]["source"] == "pdf"
    st, text = call("GET", f"/api/fulltext/text?key={key}")
    assert st == 200 and b"monopiles" in text
    # a PDF chosen for one reference
    st, out = call("POST", "/api/fulltext/attach", pdf_bytes(tmp_path), {"X-Key": burton})
    assert st == 200 and out["key"] == burton and out["how"] == "chosen"
    # a PDF that fits no reference (and has no DOI): said so
    other = [["An unrelated paper about bees and flowers in spring meadows."] * 40]
    st, out = call("POST", "/api/fulltext/attach", pdf_bytes(tmp_path, other, "bees.pdf"), {})
    assert st == 200 and out["unmatched"] and not out["doi"]
    st, out = call("POST", "/api/fulltext/attach", b"not a pdf", {})
    assert st == 400 and "isn't a PDF" in out["error"]
    assert call("POST", "/api/fulltext/remove", json.dumps({"key": key}).encode())[0] == 200
    assert call("GET", f"/api/fulltext/text?key={key}")[0] == 404


def test_text_from_the_papers_page(lib, tmp_path, monkeypatch):
    """The browser button sends the article as the page shows it (or its PDF); the menus and links are dropped."""
    import app
    import webapp
    monkeypatch.setattr(app, "USER_CONFIG", str(tmp_path / "app.json"))
    page = "1. Introduction\nDownload PDF\n" + "\n".join(
        f"Paragraph {i}: removing monopile foundations depends on water depth and vessel day rates." for i in range(40))
    assert "Download PDF" not in fulltext.clean_page(page) and "\n\nParagraph 1:" in fulltext.clean_page(page)
    key = lib[TOPHAM["title"]]["key"]
    st, _, _, out = webapp.handle("POST", "/api/clip/fulltext", {"X-Key": key}, json.dumps({"text": page}).encode())
    assert st == 200 and json.loads(out)["how"] == "page"
    assert library.get(library.load(), key)["fulltext"]["source"] == "page"
    st, _, _, out = webapp.handle("POST", "/api/clip/fulltext", {"X-Key": key}, pdf_bytes(tmp_path))
    assert st == 200 and json.loads(out)["how"] == "pdf"
    st, _, _, out = webapp.handle("POST", "/api/clip/fulltext", {"X-Key": key}, json.dumps({"text": "Too short."}).encode())
    assert st == 400
