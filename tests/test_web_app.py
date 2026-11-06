"""The web app (citrello.app/app) end to end, in Chromium and WebKit (Safari's engine). Needs internet for Pyodide
and Crossref.

  python packaging/build_web_check.py
  (cd site/public && python -m http.server 8099) &
  CITRELLO_WEB_CHECK=http://localhost:8099 pytest tests/test_web_app.py

Set CITRELLO_WEB_BROWSERS=chromium to test one browser only.
"""
import json
import os
import re
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
        for h in ("Claude", "Codex", "Word", "Updates", "Mendeley", "Browser button"):
            expect(page.locator("#tab-settings h2", has_text=re.compile(f"^{h}$"))).to_be_hidden()
        expect(page.locator("#tab-settings h2", has_text="ChatGPT and Claude on the web")).to_be_visible()
        expect(page.locator("#tab-settings h2", has_text="Account")).to_be_visible()
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


LATEX_BIB = r"""@article{topham2017,
  title = {Sustainable decommissioning of an offshore wind farm},
  author = {Topham, Eva and McMillan, David},
  journal = {Renewable Energy}, year = {2017}, volume = {102}, pages = {470--480},
  doi = {10.1016/j.renene.2016.10.066}}
@article{vetters2024,
  title = {Sailing through end-of-life challenges: A comprehensive review for offshore wind},
  author = {Vetters, Jan and Thomassen, Gwenny and Van Passel, Steven},
  journal = {Renewable and Sustainable Energy Reviews}, year = {2024}, volume = {199},
  doi = {10.1016/j.rser.2024.114486}}
@article{dupe, title={Sustainable decommissioning of an offshore wind farm}, year={2017},
  doi={10.1016/j.renene.2016.10.066}}
"""


def _zip(files):
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_documents_and_styles(browser_name, tmp_path):
    """Step 2: PDF and LaTeX checks, the arXiv .zip, style search, and progress while a job runs."""
    import re
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    shot = lambda page, name: page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-{name}.png"))  # noqa: E731

    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 860}, accept_downloads=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        open_app(page)
        page.click("#wWebStart")

        # a PDF: read with pypdf in the browser
        pdf = str(tmp_path / "paper.pdf")
        T.make_pdf(pdf, [["Costs are underestimated [1] and planning matters [4]."],
                         ["References", "[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an",
                          "offshore wind farm. Renewable Energy, 102, pp.470-480.",
                          "[2] Burton, T. and Jenkins, N. (2011). Wind Energy Handbook. Wiley, Chichester."]])
        tab(page, "documents")
        page.set_input_files("#chkFile", pdf)
        res = page.locator("#chkResult")
        expect(res).to_contain_text("2 references · PDF", timeout=LOAD)
        expect(res).to_contain_text("Cited but not in the reference list: [4]")
        shot(page, "pdf-check")

        # a LaTeX project (Overleaf .zip): missing keys and duplicates
        latex = tmp_path / "paper.zip"
        latex.write_bytes(_zip({"main.tex": "\\documentclass{article}\\begin{document}\n"
                                            "Costs \\cite{topham2017,missing2020}.\n\\bibliography{refs}\\end{document}",
                                "refs.bib": LATEX_BIB}))
        page.set_input_files("#chkFile", str(latex))
        tex = page.locator("#texResult")
        expect(tex).to_contain_text("Cited but not in the .bib", timeout=LOAD)
        expect(tex).to_contain_text("missing2020")
        expect(tex).to_contain_text("The same reference under different keys")

        # the arXiv-ready .zip: .bbl made with the bundled plainnat.bst
        arx = tmp_path / "arxiv-paper.zip"
        arx.write_bytes(_zip({"paper/main.tex": "\\documentclass{article}\\usepackage{natbib}\n% private note\n"
                                                "\\begin{document}\nCosts \\citep{topham2017}.\n"
                                                "\\bibliographystyle{plainnat}\\bibliography{refs}\\end{document}",
                              "paper/refs.bib": LATEX_BIB, "paper/main.aux": "leftover"}))
        page.set_input_files("#chkFile", str(arx))
        expect(page.locator("#axGo")).to_be_visible(timeout=LOAD)
        page.click("#axGo")
        expect(page.locator("#axResult")).to_contain_text("Ready for arXiv", timeout=LOAD)
        with page.expect_download() as dl:
            page.click("#axResult a.btn")
        with zipfile.ZipFile(dl.value.path()) as z:
            assert set(z.namelist()) == {"main.tex", "main.bbl"}
            assert "private note" not in z.read("main.tex").decode()
            assert "\\bibitem[Topham and McMillan(2017)]{topham2017}" in z.read("main.bbl").decode()
        shot(page, "arxiv")

        # style search over the full catalogue, and a journal style downloaded from the CSL repository
        tab(page, "settings")
        page.fill("#styleFind", "nature")
        expect(page.locator("#styleFindList")).to_contain_text("Nature Methods", timeout=30000)
        page.locator("#styleFindList li").first.locator("button").click()
        expect(page.locator("#defStyle")).to_have_value("nature", timeout=60000)
        page.fill("#styleFind", "oscola")
        expect(page.locator("#styleFindList")).to_contain_text("not supported yet", timeout=30000)
        shot(page, "styles")

        # verify online shows its progress while it runs (the page answers its own polls meanwhile)
        tab(page, "library")
        page.fill("#q", "")
        page.click("#openImport")
        bib = tmp_path / "three.bib"
        bib.write_text(LATEX_BIB.replace("@article{dupe", "@article{other").replace(
            "title={Sustainable decommissioning of an offshore wind farm}, year={2017},\n  doi={10.1016/j.renene.2016.10.066}",
            "title={Wind Energy Handbook}, year={2011}, publisher={Wiley}"), encoding="utf-8")
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#libRows .ref")).to_have_count(3, timeout=60000)
        page.evaluate("""() => { window.seen = []; new MutationObserver(() => seen.push(verifyMsg.textContent))
                                     .observe(verifyMsg, {childList: true, characterData: true, subtree: true}); }""")
        page.click("#verifyBtn")
        # the count can be 2 when Crossref doesn't answer for the book in time: that's Crossref, not Citrello
        expect(page.locator("#verifyMsg")).to_have_text(re.compile(r"^\d checked"), timeout=LOAD)
        seen = page.evaluate("seen")
        assert any(re.match(r"Checking \d of 3", t) for t in seen), seen
        expect(page.locator("#libRows .ref.verified").first).to_be_visible()
        shot(page, "verified")

        # and the nature style is still the default after a reload (downloaded styles are kept in the browser)
        page.reload()
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        tab(page, "settings")
        expect(page.locator("#defStyle")).to_have_value("nature", timeout=30000)
        assert not errors, errors
        b.close()
