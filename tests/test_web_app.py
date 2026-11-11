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
import urllib.parse
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
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        expect(page.locator("#quit")).to_be_hidden()
        tab(page, "settings")
        for h in ("Claude", "Codex", "Word", "Updates", "Mendeley"):
            expect(page.locator("#tab-settings h2", has_text=re.compile(f"^{h}$"))).to_be_hidden()
        expect(page.locator("#tab-settings h2", has_text="Use Citrello in ChatGPT and Claude")).to_be_visible()
        expect(page.locator("#tab-settings h2", has_text="Account")).to_be_visible()
        expect(page.locator("#tab-settings h2", has_text="Browser button")).to_be_visible()
        expect(page.locator("#webBackup")).to_be_visible()
        expect(page.locator("#webBackupInfo")).to_contain_text("No backup yet")
        tab(page, "documents")
        expect(page.locator("#latexCard")).to_be_hidden()
        tab(page, "library")

        # import a .bib file into a project
        page.click("#openImport")
        page.fill("#importProject", "Review")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#gResult")).to_contain_text("1 added", timeout=60000)
        page.click("#gNext")
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
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home

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
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#libRows .ref")).to_have_count(3, timeout=60000)
        page.click("#gNext")
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


PAPER_PAGE = """<html><head><title>{title}</title><meta name="citation_title" content="{title}">
<meta name="citation_doi" content="{doi}"></head><body>A paper</body></html>"""


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_browser_button_and_zotero(browser_name, tmp_path):
    """Step 4: the "Add to Citrello" button, with a Citrello tab open (the pop-up uses it) and without one (the
    pop-up opens Citrello itself); and importing Zotero's own library file."""
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_zotero_local as Z
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)

    def click_button(ctx, code, title, doi):
        """A paper's page, and the bookmarklet clicked on it -> the pop-up."""
        paper = ctx.new_page()
        paper.set_content(PAPER_PAGE.format(title=title, doi=doi))
        with ctx.expect_page() as popup:
            paper.evaluate(code)
        pop = popup.value
        pop.on("pageerror", lambda e: errors.append("pop-up: " + str(e)))
        return paper, pop

    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 860})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        tab(page, "settings")
        expect(page.locator("#bookmarklet")).to_be_visible()
        href = page.get_attribute("#bookmarklet", "href")
        code = urllib.parse.unquote(href[len("javascript:"):])
        assert BASE.rstrip("/") + "/app/clip.html" in code

        # with Citrello open: the pop-up asks the open tab, which adds it and shows it at once
        paper, pop = click_button(ctx, code, "Sustainable decommissioning of an offshore wind farm",
                                  "10.1016/j.renene.2016.10.066")
        expect(pop.locator("#title")).to_contain_text("Sustainable decommissioning", timeout=90000)
        pop.fill("#project", "Clipped")
        pop.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-clip.png"))
        pop.click("#add")
        expect(pop.locator("#doneMsg")).to_contain_text("Added", timeout=30000)
        tab(page, "library")
        expect(page.locator("#libRows")).to_contain_text("Sustainable decommissioning", timeout=30000)
        expect(page.locator("#projNav")).to_contain_text("Clipped")
        pop.close()
        paper.close()

        # with no Citrello tab open: the pop-up opens Citrello itself
        page.close()
        paper, pop = click_button(ctx, code, "Sailing through end-of-life challenges", "10.1016/j.rser.2024.114486")
        expect(pop.locator("#title")).to_contain_text("Sailing through end-of-life", timeout=LOAD)
        pop.click("#add")
        expect(pop.locator("#doneMsg")).to_contain_text("Added", timeout=30000)
        pop.close()
        paper.close()

        # both are in the library; then Zotero's own file, with its collections as projects
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        expect(page.locator("#libRows .ref")).to_have_count(2, timeout=30000)
        db = tmp_path / "zotero.sqlite"
        Z.make_db(str(db))
        page.click("#openImport")
        # without Citrello's Mendeley app ID (whatever the deploy has): only the export way
        ctx.route("**/app/config.json", lambda r: r.fulfill(json={"mendeley_client_id": ""}))
        page.reload()
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        page.click("#openImport")
        page.click('[data-guide="imp-mendeley"]')
        expect(page.locator("#guide")).to_contain_text("Mendeley Reference Manager")
        expect(page.locator("#impMendeleyWeb")).to_be_hidden()
        page.click("#gNext")
        page.click('[data-guide="imp-zotero"]')
        expect(page.locator("#impZoteroLocal")).to_have_count(0)  # the desktop's one-click import isn't in the web app
        page.set_input_files("#impZoteroFile", str(db))
        expect(page.locator("#gResult")).to_contain_text("now in Projects", timeout=60000)
        expect(page.locator("#importMsg")).to_contain_text("now in Projects")
        page.click("#gNext")
        expect(page.locator("#projNav")).to_contain_text("Thesis")
        expect(page.locator("#libRows")).to_contain_text("Wind Energy Handbook")
        page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-zotero.png"))
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_mendeley_import(browser_name, tmp_path):
    """"Import my Mendeley library": sign in to Mendeley in a pop-up (a stand-in here), then the library comes
    straight from Mendeley's API into the browser (a stand-in server that, like Mendeley, allows browsers)."""
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import mendeley_mock
    api, srv = mendeley_mock.start()
    seen = {}
    try:
        with sync_playwright() as pw:
            b = getattr(pw, browser_name).launch()
            ctx = b.new_context(viewport={"width": 1280, "height": 860})
            # Citrello's Mendeley app ID, set at deploy; and Mendeley's sign-in page, which sends the person back
            ctx.route("**/app/config.json", lambda r: r.fulfill(json={"mendeley_client_id": "citrello-test"}))

            def mendeley_sign_in(route):
                q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(route.request.url).query))
                seen.update(q)
                back = q["redirect_uri"] + "#" + urllib.parse.urlencode(
                    {"access_token": mendeley_mock.TOKEN, "token_type": "bearer", "state": q["state"]})
                # Mendeley redirects; Playwright can't fake a redirect in WebKit, so the page sends you back itself
                route.fulfill(content_type="text/html", body=f"<script>location.replace({json.dumps(back)})</script>")
            ctx.route("https://mendeley.test/**", mendeley_sign_in)
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE.rstrip("/") + "/app/?" + urllib.parse.urlencode(
                {"mendeley_auth": "https://mendeley.test/oauth/authorize", "mendeley_api": api}))
            page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
            page.click("#wWebStart")
            page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
            page.click("#openImport")
            page.fill("#importProject", "From Mendeley")
            page.click('[data-guide="imp-mendeley"]')
            with ctx.expect_page() as popup:
                page.click("#impMendeleyWeb")
            expect(page.locator("#importMsg")).to_contain_text("2 added from Mendeley", timeout=60000)
            assert seen["client_id"] == "citrello-test" and seen["response_type"] == "token"
            assert seen["redirect_uri"].endswith("/app/mendeley.html")
            popup.value.wait_for_event("close", timeout=10000)  # the sign-in window closes itself
            expect(page.locator("#libRows")).to_contain_text("Wind Energy Handbook")
            expect(page.locator("#projNav")).to_contain_text("From Mendeley")
            assert not errors, errors
            b.close()
    finally:
        srv.shutdown()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_connection_guides(browser_name, tmp_path):
    """Settings: the Claude, ChatGPT, Claude Code and browser-button guides open in a window, one picture per step."""
    from playwright.sync_api import expect, sync_playwright
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        tab(page, "settings")
        expect(page.locator("#webDocsBox")).to_be_hidden()  # nothing to download yet: not shown
        for name, title, steps in (("claude", "Connect Claude", 5), ("chatgpt", "Connect ChatGPT", 5),
                                   ("code", "Connect Claude Code", 2), ("button", "The browser button", 3)):
            page.click(f'[data-guide="{name}"]')
            dialog = page.locator("#guide")
            expect(dialog).to_be_visible()
            expect(page.locator("#gTitle")).to_have_text(title)
            for i in range(steps):
                expect(page.locator("#gCount")).to_have_text(f"Step {i + 1} of {steps}")
                expect(page.locator("#gArt svg")).to_be_visible()
                assert page.locator("#gArt svg").get_attribute("aria-label")  # the picture is described
                if i == steps - 1:
                    expect(page.locator("#gNext")).to_have_text("Done")
                if name == "claude" and i == 2:
                    expect(page.locator("#gText")).to_contain_text("https://mcp.citrello.app/mcp")
                    page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-guide.png"))
                page.click("#gNext")
            expect(dialog).to_be_hidden()  # Done closes it
        page.click('[data-guide="claude"]')
        page.keyboard.press("ArrowRight")
        expect(page.locator("#gCount")).to_have_text("Step 2 of 5")
        page.keyboard.press("Escape")
        expect(page.locator("#guide")).to_be_hidden()
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_import_endnote_xml(browser_name, tmp_path):
    """Import → EndNote: an EndNote XML export, dropped into the window."""
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    from test_citrello import ENDNOTE_XML
    xml = tmp_path / "My EndNote Library.xml"
    xml.write_text(ENDNOTE_XML, encoding="utf-8")
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        page.click("#openImport")
        page.click('[data-guide="imp-endnote"]')
        expect(page.locator("#guide")).to_contain_text("Save as type")
        page.set_input_files("#importFile", str(xml))
        expect(page.locator("#gResult")).to_contain_text("2 added", timeout=60000)
        page.keyboard.press("Escape")
        expect(page.locator("#libRows")).to_contain_text("Wind Energy Handbook")
        tab(page, "settings")
        expect(page.locator("#tab-settings h2", has_text=re.compile("^Zotero$"))).to_have_count(0)  # no Zotero card now
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_home_and_tours(browser_name, tmp_path):
    """Home: a new library opens on what Citrello can do; a tour checks the sample paper (made by Python in the
    browser) on the real screen, and the connector tours give a prompt to copy and links to Claude and ChatGPT."""
    from playwright.sync_api import expect, sync_playwright
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    shot = lambda page, name: page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-{name}.png"))  # noqa: E731
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        page.click("#wWebStart")
        expect(page.locator("#tab-home")).to_be_visible(timeout=30000)
        expect(page.locator(".home-tile")).to_have_count(9)
        page.click('.home-tile[data-tour="cites"]')
        tip = page.locator("#tourTip")
        expect(tip.locator("h3")).to_have_text("Choose your paper")
        tip.locator("button", has_text="Use the sample paper").click()
        expect(tip.locator("h3")).to_have_text("Citations and the reference list", timeout=120000)
        expect(page.locator("#chkCites")).to_contain_text("Citation [7] points to a reference that isn't in the list")
        shot(page, "tour-cites")
        page.click("#ttNext")
        page.click("#ttNext")
        expect(tip).to_have_count(0)
        tab(page, "home")
        page.click('.home-tile[data-tour="ai"]')
        expect(page.locator("#tab-settings")).to_be_visible()
        page.click("#ttNext")
        expect(tip).to_contain_text("Write the introduction of my paper")
        expect(tip.locator("button", has_text="Ask ChatGPT")).to_be_visible()
        # the first time, "Ask Claude" goes through connecting Claude, with the question waiting at the end
        tip.locator("button", has_text="Ask Claude").click()
        expect(page.locator("#guide")).to_be_visible()
        expect(page.locator("#gTitle")).to_have_text("Connect Claude")
        for _ in range(4):
            page.click("#gNext")
        expect(page.locator("#guide .g-ask")).to_contain_text("Write the introduction of my paper")
        page.context.route("https://claude.ai/**", lambda r: r.fulfill(body="claude"))
        with page.context.expect_page() as chat:
            page.click("#gAsk")
        assert chat.value.url.startswith("https://claude.ai/new?q=Write%20the%20introduction")
        chat.value.close()
        expect(page.locator("#guide")).to_be_hidden()
        with page.context.expect_page() as chat:  # connected now: straight to the chat
            tip.locator("button", has_text="Ask Claude").click()
        chat.value.close()
        expect(page.locator("#guide")).to_be_hidden()
        page.keyboard.press("Escape")
        expect(tip).to_have_count(0)

        # what my sources say: a paper with its text, a document that cites it
        bib = tmp_path / "two.bib"
        bib.write_text("@article{topham2017, title={Sustainable decommissioning of an offshore wind farm}, "
                       "author={Topham, Eva and McMillan, David}, journal={Renewable Energy}, year={2017}, "
                       "doi={10.1016/j.renene.2016.10.066}}\n@article{vetters2024, title={Sailing through end-of-life "
                       "challenges}, author={Vetters, Jan}, year={2024}, doi={10.1016/j.rser.2024.114486}}\n", encoding="utf-8")
        tab(page, "library")
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#gResult")).to_contain_text("2 added", timeout=60000)
        page.keyboard.press("Escape")
        sys.path.insert(0, os.path.dirname(__file__))
        from test_sources import PAPER
        import docx
        assert page.evaluate("""async (text) => (await fetch('/api/clip/fulltext', {method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Key': 'topham2017'}, body: JSON.stringify({text})})).status""", PAPER) == 200
        d = docx.Document()
        d.add_paragraph("Removing the export cables is the largest part of the decommissioning cost [@topham2017]. "
                        "Reviews compare the end-of-life options [@vetters2024].")
        doc = tmp_path / "claims.docx"
        d.save(str(doc))
        tab(page, "documents")
        page.set_input_files("#chkFile", str(doc))
        page.click("#chkSources", timeout=120000)
        expect(page.locator("#chkSrc")).to_contain_text("42% of the total decommissioning cost", timeout=60000)
        expect(page.locator("#chkSrc")).to_contain_text("No text for this paper yet")
        expect(page.locator("#srcFetch")).to_have_text("Get the free ones now")  # no extension yet
        shot(page, "sources")
        # with the extension: it's asked for exactly the papers without text, and their sentences fill in
        page.evaluate("""() => { window.sent = null;
            window.addEventListener('message', (e) => { if (e.data && e.data.citrelloExt === 'fetch') window.sent = e.data.papers; });
            window.postMessage({citrelloExt: 'hello', version: 'test'}, location.origin); }""")
        expect(page.locator("#srcFetch")).to_have_text("Get their full texts with my access")
        page.click("#srcFetch")
        page.wait_for_function("window.sent !== null")
        assert [(x["key"], x["doi"]) for x in page.evaluate("window.sent")] == [("vetters2024", "10.1016/j.rser.2024.114486")]
        text = "Sailing through end-of-life challenges.\n\n" + " ".join(
            f"Part {i}. Reviews compare the end-of-life options for offshore wind: repowering, life extension and full removal." for i in range(20))
        page.evaluate("(text) => window.postMessage({citrelloExt: 'fulltext', key: 'vetters2024', text, id: 1}, location.origin)", text)
        expect(page.locator("#chkSrc")).to_contain_text("repowering, life extension and full removal", timeout=60000)
        expect(page.locator("#srcGet")).to_have_count(0)
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_sources_by_meaning(browser_name, tmp_path):
    """What your sources say, by meaning: two small language models in the page find the passage even in other words
    and say whether it supports the sentence, contradicts it, or doesn't say (downloads the models: needs internet)."""
    from playwright.sync_api import expect, sync_playwright
    import docx
    sys.path.insert(0, os.path.dirname(__file__))
    from test_sources import PAPER
    bib = tmp_path / "t.bib"
    bib.write_text("@article{topham2017, title={Sustainable decommissioning of an offshore wind farm}, author={Topham, Eva}, "
                   "year={2017}, doi={10.1016/j.renene.2016.10.066}}\n", encoding="utf-8")
    d = docx.Document()
    d.add_paragraph("Taking the power lines out of the seabed was the costliest operation [@topham2017]. "
                    "Leaving the cables buried doubled the cost [@topham2017]. "
                    "Most turbine blades end up in landfill [@topham2017].")
    doc = tmp_path / "claims.docx"
    d.save(str(doc))
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        page.click("#wWebStart")
        tab(page, "library")
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#gResult")).to_contain_text("1 added", timeout=60000)
        page.keyboard.press("Escape")
        assert page.evaluate("""async (text) => (await fetch('/api/clip/fulltext', {method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-Key': 'topham2017'}, body: JSON.stringify({text})})).status""", PAPER) == 200
        tab(page, "documents")
        page.set_input_files("#chkFile", str(doc))
        page.click("#chkSources", timeout=120000)
        expect(page.locator("#srcMeaning")).to_contain_text("Verdicts by two small language models", timeout=600000)
        src = page.locator("#chkSrc .src")
        expect(src.nth(0)).to_contain_text("Supports this")       # other words, same meaning
        expect(src.nth(0)).to_contain_text("42% of the total decommissioning cost")
        expect(src.nth(1)).to_contain_text("Seems to contradict this")  # the paper says it halved the cost
        expect(src.nth(2)).to_contain_text("Doesn't clearly say this")   # the paper isn't about blades
        expect(page.locator("#chkSrcChips")).to_contain_text("1 supported")
        page.locator("#chkSrcPanel").scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path), f"web-app-{browser_name}-meaning.png"))
        page.click("#chkSources")  # again: from the browser's cache, quickly
        expect(page.locator("#srcMeaning")).to_contain_text("Verdicts by two small language models", timeout=60000)
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_full_texts(browser_name, tmp_path):
    """Papers' full text: PDFs dropped in bulk are matched to their references and only their text is kept; one PDF
    chosen for a reference; the mark and the filter in the library; a paper not in the library yet is added."""
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    from test_fulltext import BODY
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    bib = tmp_path / "two.bib"
    bib.write_text("""@article{topham2017, title={Sustainable decommissioning of an offshore wind farm}, author={Topham, Eva},
  journal={Renewable Energy}, year={2017}, doi={10.1016/j.renene.2016.10.066}}
@book{burton2011, title={Wind Energy Handbook}, author={Burton, Tony}, publisher={Wiley}, year={2011}}
""", encoding="utf-8")
    pdfs = {}
    for name, pages in {"topham.pdf": BODY,
                        "bees.pdf": [["An unrelated paper about bees and flowers in spring meadows."] * 40],
                        "burton.pdf": [["Wind Energy Handbook, second edition. Chapter 1."] +
                                       [f"Turbine aerodynamics part {i}: blade element momentum theory explained." for i in range(40)]],
                        "vetters.pdf": [["Sailing through end-of-life challenges: A comprehensive review for offshore wind",
                                         "https://doi.org/10.1016/j.rser.2024.114486"] +
                                        [f"Review section {i}: end-of-life options for offshore wind turbines." for i in range(40)]]}.items():
        T.make_pdf(str(tmp_path / name), pages)
        pdfs[name] = str(tmp_path / name)
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        open_app(page)
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#gResult")).to_contain_text("2 added", timeout=60000)
        page.click("#gNext")

        # many PDFs at once: matched, or said that they fit nothing
        tab(page, "papers")  # its own tab in the sidebar
        expect(page.locator("#ftSumText")).to_contain_text("None of your 2 references")
        expect(page.locator("#ftMissing .ftrow")).to_have_count(2)
        page.uncheck("#ftAdd")
        page.set_input_files("#ftFiles", [pdfs["topham.pdf"], pdfs["bees.pdf"]])
        expect(page.locator("#papersMsg")).to_contain_text("Full text added for 1 paper", timeout=90000)
        expect(page.locator("#ftLog")).to_contain_text("topham.pdf → topham2017")
        expect(page.locator("#ftLog")).to_contain_text("bees.pdf: no matching reference")
        page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-fulltext-bulk.png"))
        # a paper that isn't in the library yet: added by its DOI (Crossref), with its text
        page.check("#ftAdd")
        page.set_input_files("#ftFiles", pdfs["vetters.pdf"])
        expect(page.locator("#ftLog")).to_contain_text("added to your library", timeout=90000)
        expect(page.locator("#ftSumText")).to_contain_text("2 of 3 references have their full text", timeout=30000)
        expect(page.locator("#navFt")).to_have_text("2/3")
        page.set_input_files('#ftMissing input[data-ft-key="burton2011"]', pdfs["burton.pdf"])  # from the list
        expect(page.locator("#ftMissingCard")).to_be_hidden(timeout=60000)  # none left without
        page.locator("#tab-papers").screenshot(path=os.path.join(shots, f"web-app-{browser_name}-fulltext-tab.png"))
        tab(page, "library")

        expect(page.locator('#libRows .ref[data-sel="topham2017"] .ftmark')).to_be_visible()
        expect(page.locator("#libChips")).to_contain_text("3 with full text")
        expect(page.locator('#libChips [data-ft="no"]')).to_have_count(0)  # none without: no "without" chip
        page.click('#libRows .ref[data-sel="burton2011"]')
        expect(page.locator("#detail h2")).to_have_text("Wind Energy Handbook")  # its panel, not the last one's
        expect(page.locator("#detail")).to_contain_text("ChatGPT and Claude can read this paper", timeout=30000)
        page.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-fulltext-detail.png"))
        with page.expect_download() as dl:
            page.locator("#detail a.btn", has_text="See the text").click()
        assert "blade element momentum" in open(dl.value.path(), encoding="utf-8").read()

        # still there after a reload (kept in the browser), and removable
        page.reload()
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        expect(page.locator("#libChips")).to_contain_text("3 with full text", timeout=30000)
        page.click('#libRows .ref[data-sel="burton2011"]')
        expect(page.locator("#detail h2")).to_have_text("Wind Energy Handbook")  # its panel, not the last one's
        page.locator('#detail [data-ft-remove="burton2011"]').click()
        expect(page.locator("#detail")).to_contain_text("Choose the PDF", timeout=30000)
        assert not errors, errors
        b.close()


def publisher_site(pdf_path):
    """Pretend publisher pages on their own address (another port = another site, like a real publisher)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    article = " ".join(f"<p>Section {i}. Decommissioning offshore wind farms costs more than planned; removing monopile "
                       f"foundations depends on water depth and vessel rates, and recycling steel recovers part of it.</p>"
                       for i in range(40))
    pages = {
        "/article.html": f"""<html><head><title>Topham</title><meta name="citation_title" content="Sustainable decommissioning of an offshore wind farm">
            <meta name="citation_doi" content="10.1016/j.renene.2016.10.066"></head><body><nav>Menu</nav>
            <article><h2>1. Introduction</h2>{article}<h2>4. Results</h2><p>Recycling composite blades remains hard.</p></article></body></html>""",
        "/pdfonly.html": """<html><head><title>Vetters</title><meta name="citation_doi" content="10.1016/j.rser.2024.114486">
            <meta name="citation_pdf_url" content="/vetters.pdf"></head><body><p>Abstract only.</p></body></html>""",
        "/nothing.html": """<html><head><title>LeCun</title><meta name="citation_doi" content="10.1038/nature14539"></head>
            <body><p>Abstract only. Buy this article.</p></body></html>""",
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/vetters.pdf":
                body, ctype = open(pdf_path, "rb").read(), "application/pdf"
            elif self.path in pages:
                body, ctype = pages[self.path].encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_web_app_browser_button_takes_the_full_text(browser_name, tmp_path):
    """The browser button on a paper's page: the article shown on the page, or the PDF it links to, becomes the
    reference's full text (with the person's access; only the text is kept); a page with neither says so."""
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    pdf = str(tmp_path / "vetters.pdf")
    T.make_pdf(pdf, [["Sailing through end-of-life challenges: A comprehensive review for offshore wind"] +
                     [f"Review section {i}: end-of-life options for offshore wind turbines and their costs." for i in range(40)]])
    site, srv = publisher_site(pdf)
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    try:
        with sync_playwright() as pw:
            b = getattr(pw, browser_name).launch()
            ctx = b.new_context(viewport={"width": 1280, "height": 860})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            open_app(page)
            page.click("#wWebStart")
            page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
            tab(page, "settings")
            code = urllib.parse.unquote(page.get_attribute("#bookmarklet", "href")[len("javascript:"):])
            tab(page, "library")

            def click_button_on(path):
                paper = ctx.new_page()
                paper.goto(site + path)
                with ctx.expect_page() as popup:
                    paper.evaluate(code)
                pop = popup.value
                pop.on("pageerror", lambda e: errors.append("pop-up: " + str(e)))
                return paper, pop

            for path, title, how in (("/article.html", "Sustainable decommissioning", "page"),
                                     ("/pdfonly.html", "Sailing through end-of-life", "pdf")):
                paper, pop = click_button_on(path)
                expect(pop.locator("#title")).to_contain_text(title, timeout=90000)
                expect(pop.locator("#ftLine")).to_contain_text("Full text found", timeout=30000)
                if how == "page":
                    pop.screenshot(path=os.path.join(shots, f"web-app-{browser_name}-clip-fulltext.png"))
                pop.click("#add")
                expect(pop.locator("#doneMsg")).to_contain_text("With its full text", timeout=60000)
                pop.close()
                paper.close()
            expect(page.locator("#libChips")).to_contain_text("2 with full text", timeout=30000)
            key = page.locator("#libRows .ref", has_text="Sustainable decommissioning").get_attribute("data-sel")
            page.click(f'#libRows .ref[data-sel="{key}"]')
            expect(page.locator("#detail")).to_contain_text("ChatGPT and Claude can read this paper")

            paper, pop = click_button_on("/nothing.html")  # abstract only: nothing to take
            expect(pop.locator("#ftLine")).to_contain_text("No full text on this page", timeout=30000)
            pop.close()
            paper.close()
            assert not errors, errors
            b.close()
    finally:
        srv.shutdown()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_full_texts_correct_wrong_dois_first(browser_name, tmp_path):
    """Before fetching full texts, the references are checked online so a wrong DOI is corrected (the extension
    would otherwise open the wrong page, or none). A stand-in for the extension records what it's given."""
    import json as _json
    from playwright.sync_api import expect, sync_playwright
    refs = [{"id": "topham2017", "type": "article-journal", "title": "Sustainable decommissioning of an offshore wind farm",
             "author": [{"family": "Topham", "given": "Eva"}, {"family": "McMillan", "given": "David"}],
             "container-title": "Renewable Energy", "volume": "102", "page": "470-480", "issued": {"date-parts": [[2017]]},
             "DOI": "10.1016/j.renene.2016.10.999"}]  # wrong: the real one ends in .066
    lib = tmp_path / "refs.json"
    lib.write_text(_json.dumps(refs), encoding="utf-8")
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        open_app(page)
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(lib))
        expect(page.locator("#gResult")).to_contain_text("1 added", timeout=60000)
        page.click("#gNext")
        # a stand-in for the extension: says hello, and keeps what Citrello asks it to fetch
        page.evaluate("""() => { window.sent = null;
            window.addEventListener('message', (e) => { if (e.data && e.data.citrelloExt === 'fetch') window.sent = e.data.papers; });
            window.postMessage({citrelloExt: 'hello', version: 'test'}, location.origin); }""")
        tab(page, "papers")
        expect(page.locator("#ftVerify")).to_be_checked()
        page.click("#ftGet")
        page.wait_for_function("window.sent !== null", timeout=180000)
        sent = page.evaluate("window.sent")
        assert sent[0]["doi"] == "10.1016/j.renene.2016.10.066", sent  # corrected before fetching
        expect(page.locator("#papersMsg")).to_contain_text("1 DOI was corrected first")
        assert not errors, errors
        b.close()
