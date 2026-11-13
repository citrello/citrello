"""End to end: Citrello for Word's panel (site/public/word) in Chromium, with a stand-in for Word (tests/office_mock.js).

The panel runs Citrello's engine, signs in to a local bridge, brings the library from the cloud copy, cites, suggests
papers when the writer types "Wang et al.", edits a citation, inserts the reference list and changes the style.
Real Word isn't used: the stand-in plays a document with content controls the way Office.js does.

Run the bridge (see bridge/README.md) and serve the site, then:
  CITRELLO_BRIDGE_E2E=1 CITRELLO_BRIDGE=http://localhost:8787 CITRELLO_WEB_CHECK=http://localhost:8099 \
    pytest tests/test_word_addin_e2e.py
"""
import gzip
import json
import os
import re
import time
import urllib.parse
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CITRELLO_BRIDGE_E2E") != "1" or not os.environ.get("CITRELLO_WEB_CHECK"),
                                reason="needs a running bridge and the site served locally")

import test_bridge_e2e as B  # noqa: E402
from test_web_app import LOAD  # noqa: E402

BASE = os.environ.get("CITRELLO_WEB_CHECK", "").rstrip("/")
MOCK = os.path.join(os.path.dirname(__file__), "office_mock.js")


def item(key, title, authors, year, journal):
    return {"key": key, "id": key, "projects": [], "added": f"2026-01-0{len(key) % 9}T00:00:00+00:00",
            "csl": {"id": key, "type": "article-journal", "title": title, "container-title": journal,
                    "author": [{"family": f, "given": g} for f, g in authors], "issued": {"date-parts": [[year]]}}}


LIBRARY = {"projects": [], "items": [
    item("topham2017", "Sustainable decommissioning of an offshore wind farm", [("Topham", "Eva"), ("McMillan", "David")], 2017, "Renewable Energy"),
    item("wang2020", "Blade recycling at scale", [("Wang", "Li"), ("Chen", "Hao"), ("Zhou", "Mei")], 2020, "Wind Energy"),
    item("wang2018", "Composite waste from wind turbines", [("Wang", "Li"), ("Li", "Qiang")], 2018, "Waste Management"),
]}


def save_cloud_copy(email, lib=None, texts=None):
    """Sign in like another device would and save a cloud copy holding LIBRARY (and papers' text)."""
    B.bc.api("POST", "/api/auth/start", {"email": email})
    code = B.bc.start_sign_in(email)["dev_code"]
    res = B.bc.api("POST", "/api/auth/verify", {"email": email, "code": code, "device_name": "Test", "platform": "test",
                                                "app_version": "test"})
    body = gzip.compress(json.dumps({"citrello_cloud": 1, "library": lib or LIBRARY, "fulltext": texts or {}}).encode())
    req = urllib.request.Request(B.bc.BRIDGE + "/api/cloud", data=body, method="PUT", headers={
        "Authorization": "Bearer " + res["token"], "Content-Type": "application/gzip", "X-Items": "3", "X-Texts": str(len(texts or {})),
        "X-Device": "test-device"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 200


def blank_docx(tmp_path):
    import docx
    path = str(tmp_path / "blank.docx")
    docx.Document().save(path)
    return path


def word_document(path):
    """What the stand-in for Word needs: the .docx, and each paragraph's text and XML as Word gives them."""
    import base64
    import docx
    from lxml import etree
    import docconvert
    from test_docconvert import PKG
    paras = docconvert.body_paras(docx.Document(path).element.body)
    return {"file": base64.b64encode(open(path, "rb").read()).decode(),
            "paragraphs": [{"text": docconvert.para_text(p), "xml": PKG.format(etree.tostring(p, encoding="unicode"))}
                           for p in paras]}


def sign_in(page, email):
    from playwright.sync_api import expect
    expect(page.locator("#signin")).to_be_visible(timeout=LOAD)
    page.fill("#siEmail", email)
    page.click("#siSend")
    expect(page.locator("#siStep2")).to_be_visible(timeout=30000)
    page.fill("#siCode", B.bc.start_sign_in(email)["dev_code"])
    page.click("#siVerify")


def test_word_addin_turns_zotero_citations_into_its_own(tmp_path):
    """A paper written with Zotero: the panel counts its citations, lists the problems, and converts them in place
    (text kept, fields gone), then restyles them and the reference list with Track Changes on."""
    import docx
    from playwright.sync_api import expect, sync_playwright
    from test_doccheck import TOPHAM, VETTERS, add_field, csl_field
    email = f"wordconv-{int(time.time())}@example.com"
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    save_cloud_copy(email)
    dup = dict(TOPHAM, id="t2", title=TOPHAM["title"] + ".")  # the same paper saved twice in Zotero
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    add_field(p, csl_field([TOPHAM], zotero=True), "(Topham 2017)")
    p.add_run(" today.")
    p = d.add_paragraph("It was reviewed ")
    add_field(p, csl_field([VETTERS, dup], zotero=True), "(Vetters 2024; Topham 2017)")
    p.add_run(".")
    d.add_heading("References", level=1)
    d.add_paragraph("Topham E (2017) Sustainable decommissioning of an offshore wind farm.")
    d.add_paragraph("Vetters J (2024) Sailing through end-of-life challenges.")
    path = str(tmp_path / "zotero.docx")
    d.save(path)
    errors = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 360, "height": 900})
        page.route("https://appsforoffice.microsoft.com/**", lambda r: r.fulfill(path=MOCK, content_type="text/javascript"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        controls = lambda: page.evaluate("__doc.controls()")  # noqa: E731
        page.goto(f"{BASE}/word/taskpane.html?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
        page.evaluate("d => __doc.load(d)", word_document(path))
        sign_in(page, email)

        card = page.locator("#doc")
        expect(card).to_contain_text("2 citations made with Zotero (2) can become Citrello citations", timeout=180000)
        expect(card).to_contain_text("The reference list becomes Citrello's too")
        expect(card).to_contain_text("listed more than once")                     # the problems found
        page.screenshot(path=os.path.join(shots, "word-document-check.png"))
        page.click("#docConvert")
        expect(card).to_contain_text("Done: 2 citations are now Citrello citations", timeout=60000)
        expect(card).to_contain_text("The changes are tracked")
        expect(card).to_contain_text("Fixed while converting: duplicate references merged")
        expect(card).not_to_contain_text("listed more than once")
        cits = [c for c in controls() if c["tag"].startswith("CITRELLO_CITATION_v1_")]
        bib = [c for c in controls() if c["tag"] == "CITRELLO_BIBLIOGRAPHY"]
        # APA, the duplicate merged; online, the details come from Crossref (all the authors: "Topham & McMillan")
        t1, t2 = [c["text"] for c in cits]
        assert re.fullmatch(r"\(Topham[^;]*, 2017\)", t1) and re.fullmatch(r"\(Topham[^;]*, 2017; Vetters[^;]*, 2024\)", t2)
        assert len(bib) == 1 and bib[0]["text"].startswith("Topham, E.") and "Vetters, J." in bib[0]["text"]
        assert bib[0]["text"].count("\n") == 1                                     # two entries
        text = page.evaluate("__doc.text()")
        assert text.startswith(f"Decommissioning is costly {t1} today.\nIt was reviewed {t2}.")
        assert page.evaluate("__doc.trackLog") == ["Off", "TrackAll", "Off"]       # restyled with Track Changes on
        page.screenshot(path=os.path.join(shots, "word-document-converted.png"))
        # the converted citations are the add-in's own: click one, its papers are listed
        page.evaluate("__doc.clickControl(1)")
        expect(page.locator("#curList li")).to_have_count(2, timeout=10000)
        assert not errors, errors
        b.close()


def test_word_addin_cites_suggests_and_keeps_the_list(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    email = f"word-{int(time.time())}@example.com"
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    save_cloud_copy(email)
    errors = []
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 360, "height": 900})
        page.route("https://appsforoffice.microsoft.com/**", lambda r: r.fulfill(path=MOCK, content_type="text/javascript"))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept("Nature"))
        doc = lambda js: page.evaluate(js)  # noqa: E731
        controls = lambda: doc("__doc.controls()")  # noqa: E731
        page.goto(f"{BASE}/word/taskpane.html?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
        page.evaluate("d => __doc.load(d)", word_document(blank_docx(tmp_path)))  # a new, empty document

        # sign in with an emailed code: the library comes from the cloud copy
        expect(page.locator("#signin")).to_be_visible(timeout=LOAD)
        page.fill("#siEmail", email)
        page.click("#siSend")
        expect(page.locator("#siStep2")).to_be_visible(timeout=30000)
        page.fill("#siCode", B.bc.start_sign_in(email)["dev_code"])
        page.click("#siVerify")
        expect(page.locator("#libInfo")).to_contain_text("3 references", timeout=60000)
        expect(page.locator("#results li")).to_have_count(3)
        expect(page.locator("#doc")).to_contain_text("No citations in this document yet", timeout=60000)

        # search and cite
        doc("__doc.type('Decommissioning is costly ')")
        page.fill("#q", "decommissioning")
        expect(page.locator("#results li")).to_have_count(1)
        expect(page.locator("#results li")).to_contain_text("Sustainable decommissioning")
        page.click("#results button:has-text('Cite')")
        page.wait_for_function("__doc.controls().length === 1 && __doc.controls()[0].text.includes('Topham')", timeout=30000)
        assert controls()[0]["text"] == "(Topham & McMillan, 2017)"
        assert controls()[0]["tag"].startswith("CITRELLO_CITATION_v1_") and controls()[0]["title"] == "Citrello citation"

        # typing "Wang et al." offers Wang's papers; Cite gives "Wang et al. (2020)"
        doc("__doc.type('. Later work by Wang et al.')")
        expect(page.locator("#suggest")).to_be_visible(timeout=10000)
        expect(page.locator("#sugTitle")).to_contain_text("Wang et al.")
        expect(page.locator("#sugList li")).to_have_count(2)
        expect(page.locator("#sugList li").first).to_contain_text("Blade recycling")
        page.screenshot(path=os.path.join(shots, "word-suggest.png"))
        page.locator("#sugList button").first.click()
        page.wait_for_function("__doc.text().includes('Wang et al. (2020)')", timeout=30000)
        expect(page.locator("#suggest")).to_be_hidden(timeout=10000)

        # the reference list, where the cursor is
        doc("__doc.type(' found more. References: ')")
        page.click("#bib")
        page.wait_for_function("__doc.controls().some(c => c.tag === 'CITRELLO_BIBLIOGRAPHY' && c.text.includes('Wang'))", timeout=30000)
        bib = [c for c in controls() if c["tag"] == "CITRELLO_BIBLIOGRAPHY"][0]
        assert "Topham, E., & McMillan, D. (2017)" in bib["text"] and "\n" in bib["text"]
        assert any(r.get("italic") and "Renewable Energy" in r["t"] for r in bib["runs"])  # the journal in italics

        # click in the first citation: its papers, add one from the search, take it out again
        doc("__doc.clickControl(0)")
        expect(page.locator("#current")).to_be_visible(timeout=10000)
        expect(page.locator("#curList li")).to_have_count(1)
        page.fill("#q", "composite")
        expect(page.locator("#results li")).to_have_count(1)
        expect(page.locator("#results li")).to_contain_text("Composite waste")
        page.click("#results button:has-text('Add')")
        page.wait_for_function("__doc.controls()[0].text.includes('Wang')", timeout=30000)
        assert controls()[0]["text"] == "(Topham & McMillan, 2017; Wang & Li, 2018)"
        expect(page.locator("#curList li")).to_have_count(2)
        page.locator("#curList li").nth(1).locator("button[data-rm]").click()
        page.wait_for_function("!__doc.controls()[0].text.includes('Wang')", timeout=30000)
        page.locator("#curList details summary").first.click()
        page.screenshot(path=os.path.join(shots, "word-citation.png"))

        # another style for the whole document (numbers follow the order of citing), kept in the document
        page.select_option("#style", "ieee")
        page.wait_for_function("__doc.controls()[0].text === '[1]'", timeout=30000)
        assert controls()[1]["text"] == "[2]" and doc("__doc.settings.citrelloStyle") == "ieee"
        bib = [c for c in controls() if c["tag"] == "CITRELLO_BIBLIOGRAPHY"][0]
        assert bib["text"].startswith("[1]")
        # a style from the full catalogue
        page.select_option("#style", "__other")
        page.wait_for_function("document.getElementById('style').value !== 'ieee'", timeout=30000)
        page.screenshot(path=os.path.join(shots, "word-panel.png"), full_page=True)
        assert not errors, errors
        b.close()


def test_word_addin_checks_a_claim_and_finds_another(tmp_path):
    """Click a citation: Check the claim (free: the allowance goes down), a paper without text gets its PDF added,
    Find another reference is Pro (the Pro card for free accounts; with Pro, a better paper from the library)."""
    import anthropic_mock
    import fulltext
    from playwright.sync_api import expect, sync_playwright
    from test_doccheck import make_pdf
    from test_sources import PAPER
    srv = anthropic_mock.start()
    anthropic_mock.SEEN.clear()
    email = f"wordclaim-{int(time.time())}@example.com"
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    lib = {"projects": [], "items": [dict(i) for i in LIBRARY["items"]] + [
        item("lee2021", "Cable recovery dominates offshore wind decommissioning costs", [("Lee", "Ann")], 2021, "Energy Policy")]}
    for it in lib["items"]:
        if it["key"] in ("topham2017", "lee2021"):
            it["fulltext"] = {"words": len(PAPER.split()), "source": "pdf", "added": "2026-11-01"}
    save_cloud_copy(email, lib, {"topham2017.txt": PAPER, "lee2021.txt": PAPER.replace("42%", "45%")})
    pdf = tmp_path / "wang.pdf"
    make_pdf(str(pdf), [["Blade recycling at scale"] + [l for l in PAPER.split("\n") if l][:30]])
    errors = []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            page = b.new_page(viewport={"width": 360, "height": 1000})
            page.route("https://appsforoffice.microsoft.com/**", lambda r: r.fulfill(path=MOCK, content_type="text/javascript"))
            page.on("pageerror", lambda e: errors.append(str(e)))
            doc = lambda js: page.evaluate(js)  # noqa: E731
            page.goto(f"{BASE}/word/taskpane.html?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
            page.evaluate("d => __doc.load(d)", word_document(blank_docx(tmp_path)))
            sign_in(page, email)
            expect(page.locator("#libInfo")).to_contain_text("4 references", timeout=60000)

            # two citations: one paper with its text in the library, one without (no free copy: no DOI)
            doc("__doc.type('Removing the subsea export cables accounted for most of the decommissioning cost ')")
            page.fill("#q", "decommissioning of an offshore")
            expect(page.locator("#results li")).to_have_count(1)
            expect(page.locator("#results li")).to_contain_text("Sustainable decommissioning")
            page.click("#results button:has-text('Cite')")
            page.wait_for_function("__doc.controls().length === 1 && __doc.controls()[0].text.includes('Topham')", timeout=30000)
            doc("__doc.type('. Blades are recycled ')")
            page.fill("#q", "blade recycling")
            expect(page.locator("#results li")).to_have_count(1)
            expect(page.locator("#results li")).to_contain_text("Blade recycling")
            page.click("#results button:has-text('Cite')")
            page.wait_for_function("__doc.controls().length === 2", timeout=30000)

            # click the first citation: Check the claim (free: 3 a month on this test copy of the server)
            doc("__doc.clickControl(0)")
            expect(page.locator("#quotaLine")).to_contain_text("3 of 3 free claim checks left", timeout=30000)
            page.click("#claimCheck")
            expect(page.locator("#claimOut .verdict.supports")).to_contain_text("Supports it", timeout=60000)
            expect(page.locator("#quotaLine")).to_contain_text("2 of 3 free claim checks left")
            sent = anthropic_mock.SEEN[-1]["body"]["messages"][0]["content"]
            assert "Removing the subsea export cables accounted for most of the decommissioning cost" in sent and "42%" in sent
            page.locator("#claimOut summary").first.click()
            page.screenshot(path=os.path.join(shots, "word-claim-check.png"))

            # Find another reference: Pro only
            page.click("#findAnother")
            expect(page.locator("#proCard")).to_be_visible()
            expect(page.locator("#proLead")).to_contain_text("part of Citrello Pro")
            page.click("#proLater")

            # the second citation: no text anywhere, so its PDF is added from the computer
            doc("__doc.clickControl(1)")
            expect(page.locator("#claimOut")).to_be_empty()
            page.click("#claimCheck")
            expect(page.locator("#claimOut .verdict.none")).to_contain_text("No full text found", timeout=60000)
            expect(page.locator("#quotaLine")).to_contain_text("2 of 3")             # nothing was sent to Claude
            with page.expect_file_chooser() as fc:
                page.click("#claimOut button[data-pdf]")
            fc.value.set_files(str(pdf))
            expect(page.locator("#claimOut .verdict:not(.none)")).to_have_count(1, timeout=60000)
            expect(page.locator("#quotaLine")).to_contain_text("1 of 3")

            # with Pro: another reference from the library, added to the citation
            assert B.call("POST", "/admin/pro", {"email": email, "pro": True}, headers={"Authorization": "Bearer testadmin"})[0] == 200
            doc("__doc.clickControl(0)")
            expect(page.locator("#claimOut .verdict.supports")).to_be_visible(timeout=10000)  # checked before: shown again
            page.click("#findAnother")
            page.click("#findLib")
            lee = page.locator("#claimOut .verdict", has_text="Lee 2021")
            expect(lee).to_contain_text("Supports it", timeout=60000)
            page.screenshot(path=os.path.join(shots, "word-find-another.png"))
            lee.locator("button[data-add]:has-text('Add to this citation')").click()
            page.wait_for_function("__doc.controls()[0].text.includes('Lee')", timeout=30000)
            expect(page.locator("#curList li")).to_have_count(2)
            expect(page.locator("#quotaLine")).to_contain_text("Citrello Pro")
            assert not errors, errors
            b.close()
    finally:
        srv.shutdown()
        srv.server_close()
