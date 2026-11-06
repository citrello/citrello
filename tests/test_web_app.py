"""The web app (citrello.app/app) end to end, in Chromium and WebKit (Safari's engine). Needs internet for Pyodide
and Crossref.

  python packaging/build_web_check.py
  (cd site/public && python -m http.server 8099) &
  CITRELLO_WEB_CHECK=http://localhost:8099 pytest tests/test_web_app.py

Set CITRELLO_WEB_BROWSERS=chromium to test one browser only.
"""
import json
import os
import sys
import zipfile

import pytest

BASE = os.environ.get("CITRELLO_WEB_CHECK")
pytestmark = pytest.mark.skipif(not BASE, reason="needs the site served locally and internet (set CITRELLO_WEB_CHECK)")
BROWSERS = os.environ.get("CITRELLO_WEB_BROWSERS", "chromium,webkit").split(",")
LOAD = 180000  # Pyodide and the packages, first time

BIB = """@article{Vetters2024,
  author = {Vetters, Jan and Thomassen, Gwenny and Van Passel, Steven},
  title = {Sailing through end-of-life challenges: A comprehensive review},
  journal = {Renewable and Sustainable Energy Reviews}, year = {2024}, volume = {199}, pages = {114486}}
"""


def open_app(page):
    page.goto(BASE.rstrip("/") + "/app/")
    page.wait_for_selector("#webCover", state="detached", timeout=LOAD)


def tab(page, name):
    page.click(f'.side button.navb[data-tab="{name}"]')


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app(browser_name, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    import docx
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    shot = lambda page, name: page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-{name}.png"))  # noqa: E731
    bib = tmp_path / "refs.bib"
    bib.write_text(BIB, encoding="utf-8")

    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 860}, accept_downloads=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        open_app(page)

        # first visit: the web welcome, no desktop-only parts
        expect(page.locator("#welcome")).to_be_visible()
        expect(page.locator("#welcome")).to_contain_text("stays in this browser")
        shot(page, "welcome")
        page.click("#wWebStart")
        expect(page.locator("#quit")).to_be_hidden()
        tab(page, "settings")
        for h in ("Claude", "Codex", "Word", "Updates", "Mendeley"):
            expect(page.locator("#tab-settings h2", has_text=h).first).to_be_hidden()
        expect(page.locator("#webBackup")).to_be_visible()
        expect(page.locator("#webBackupInfo")).to_contain_text("No backup yet")
        tab(page, "documents")
        expect(page.locator("#latexCard")).to_be_hidden()
        tab(page, "library")

        # import a .bib file into a project
        page.click("#openImport")
        expect(page.locator("#impZoteroLocal")).to_be_hidden()
        page.fill("#importProject", "Review")
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#importMsg")).to_contain_text("1", timeout=60000)
        expect(page.locator("#libRows")).to_contain_text("Sailing through end-of-life")

        # add by DOI: looked up on Crossref from the browser
        page.fill("#q", "10.1016/j.renene.2016.10.066")
        page.click("#qAddBtn")
        expect(page.locator("#libRows")).to_contain_text("Sustainable decommissioning", timeout=90000)
        expect(page.locator("#webBackupNag")).to_be_visible()
        shot(page, "library")

        # export BibTeX: a download made in the browser
        page.fill("#q", "")
        page.click("#openExport")
        with page.expect_download() as dl:
            page.click("#expBib")
        assert dl.value.suggested_filename == "citrello_library.bib"
        text = open(dl.value.path(), encoding="utf-8").read()
        assert "Topham2017" in text and "Vetters2024" in text

        # back up, delete everything, restore
        with page.expect_download() as dl:
            page.click("#webBackupNag a.btn")
        backup = tmp_path / dl.value.suggested_filename
        dl.value.save_as(str(backup))
        assert backup.name.startswith("citrello-library-")
        assert {it["key"] for it in json.loads(backup.read_text(encoding="utf-8"))["items"]} == {"Topham2017", "Vetters2024"}
        expect(page.locator("#webBackupNag")).to_be_hidden()
        page.check("#selAll")
        page.click("#selDelete")
        expect(page.locator("#libEmpty")).to_be_visible()
        tab(page, "settings")
        expect(page.locator("#webBackupInfo")).to_contain_text("Last backup")
        page.set_input_files("#webRestoreFile", str(backup))
        expect(page.locator("#webRestoreMsg")).to_contain_text("Restored: 2 references")
        shot(page, "settings")

        # check and fix a Word document (Zotero citations) with the desktop app's own code
        d = docx.Document()
        p = d.add_paragraph("Decommissioning is costly ")
        T.add_field(p, T.csl_field([T.TOPHAM], zotero=True))
        p = d.add_paragraph("and reviewed ")
        T.add_field(p, T.csl_field([T.VETTERS, dict(T.TOPHAM, id="t2")], zotero=True))
        paper = str(tmp_path / "paper.docx")
        d.save(paper)
        tab(page, "documents")
        page.set_input_files("#chkFile", paper)
        res = page.locator("#chkResult")
        expect(res).to_contain_text("listed more than once", timeout=LOAD)
        expect(res).to_contain_text("Zotero")
        page.select_option("#chkStyle", "ieee")
        page.click("#chkFix")
        expect(res).to_contain_text("Duplicates merged", timeout=LOAD)
        with page.expect_download() as dl:
            page.click("#chkResult a.btn.primary")
        with zipfile.ZipFile(dl.value.path()) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        assert "ZOTERO_ITEM" not in xml and "ADDIN CSL_CITATION" in xml and "CSL_BIBLIOGRAPHY" in xml
        shot(page, "document-fixed")

        # a second tab can't open it at the same time (both would overwrite each other's library)
        other = ctx.new_page()
        other.goto(BASE.rstrip("/") + "/app/")
        expect(other.locator("#webCover")).to_contain_text("already open in another tab", timeout=30000)
        other.close()

        # the library is still there after a reload, and the welcome isn't shown again
        page.reload()
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        expect(page.locator("#libRows")).to_contain_text("Sustainable decommissioning", timeout=30000)
        expect(page.locator("#libRows")).to_contain_text("Sailing through end-of-life")
        expect(page.locator("#welcome")).to_be_hidden()
        page.click("#libRows >> text=Sailing through end-of-life")
        expect(page.locator("#detail")).to_contain_text("Review")
        assert not errors, errors
        b.close()
