"""The Citrello browser extension (extension/), loaded into a real Chromium with the web app.

- Bulk: "Fetch the missing ones with my access" opens the extension's fetch page, which visits each paper (pretend
  publisher pages here) in a background tab and hands the text to Citrello; a page without the text is reported.
- The toolbar panel on a paper's page: looks the paper up in Citrello (the open tab, or one opened in the background)
  and adds it with its text.

  python packaging/build_web_check.py
  (cd site/public && python -m http.server 8099) &
  CITRELLO_WEB_CHECK=http://localhost:8099 pytest tests/test_web_extension.py

Chromium only: Playwright can load extensions only there (the Firefox package is built but not tested here).
"""
import os
import subprocess
import sys
import urllib.parse

import pytest

BASE = os.environ.get("CITRELLO_WEB_CHECK", "").rstrip("/")
pytestmark = pytest.mark.skipif(not BASE, reason="needs the site served locally and internet (set CITRELLO_WEB_CHECK)")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOAD = 180000


def test_extension_fetches_full_texts_and_adds_from_the_toolbar(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    from test_web_app import publisher_site
    pdf = str(tmp_path / "vetters.pdf")
    T.make_pdf(pdf, [["Sailing through end-of-life challenges: A comprehensive review for offshore wind"] +
                     [f"Review section {i}: end-of-life options for offshore wind turbines and their costs." for i in range(40)]])
    site, srv = publisher_site(pdf)
    ext = str(tmp_path / "ext")
    subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "build_extension.py"), "--test", ext], check=True)
    # references whose links go to the pretend publisher (real ones go through doi.org to the real publisher)
    bib = tmp_path / "refs.bib"
    bib.write_text(f"""@article{{topham2017, title={{Sustainable decommissioning of an offshore wind farm}}, year={{2017}},
  url={{{site}/article.html}}}}
@article{{vetters2024, title={{Sailing through end-of-life challenges}}, year={{2024}}, url={{{site}/pdfonly.html}}}}
@article{{lecun2015, title={{Deep learning}}, year={{2015}}, url={{{site}/nothing.html}}}}
""", encoding="utf-8")
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=True,
                                                        viewport={"width": 1280, "height": 800},  # the stores' screenshot size
                                                        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/app/")
            page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
            page.click("#wWebStart")
            page.wait_for_function("typeof extVersion !== 'undefined' && extVersion", timeout=15000)  # the extension said hello
            page.evaluate("window.CITRELLO_TEST_FAST = true")  # no 10-20 s pause between papers (local copies only)
            page.click("#openImport")
            page.click('[data-guide="imp-file"]')
            page.set_input_files("#importFile", str(bib))
            expect(page.locator("#libRows .ref")).to_have_count(3, timeout=60000)
            page.click("#gNext")

            # bulk: the extension's fetch page visits each paper and hands the text to Citrello
            page.click('.side button.navb[data-tab="papers"]')
            expect(page.locator("#ftWays")).to_contain_text("With your university access")
            with ctx.expect_page() as fetch_page:
                page.click("#ftExt")
            fp = fetch_page.value
            expect(fp.locator("#list li")).to_have_count(3, timeout=15000)
            fp.click("#start")
            expect(fp.locator("#msg")).to_contain_text("Finished: 2 of 3 full texts added", timeout=180000)
            expect(fp.locator("#list")).to_contain_text("No access")
            fp.screenshot(path=os.path.join(shots, "extension-fetch.png"))
            fp.close()
            page.bring_to_front()
            page.click('.side button.navb[data-tab="library"]')
            expect(page.locator("#libChips")).to_contain_text("2 with full text", timeout=30000)
            expect(page.locator('#libRows .ref[data-sel="topham2017"] .ftmark')).to_be_visible()
            expect(page.locator('#libRows .ref[data-sel="vetters2024"] .ftmark')).to_be_visible()
            expect(page.locator('#libRows .ref[data-sel="lecun2015"] .ftmark')).to_have_count(0)
            page.click('#libRows .ref[data-sel="topham2017"]')
            expect(page.locator("#detail")).to_contain_text("ChatGPT and Claude can read this paper")
            page.screenshot(path=os.path.join(shots, "extension-library.png"))

            # the toolbar panel on a paper's page (a panel under the button, not a new window): it reads the page,
            # Citrello (the open tab) looks the paper up, and Add keeps the paper with its text
            ext_id = sw.url.split("/")[2]

            def panel_for(path):
                paper = ctx.new_page()
                paper.goto(site + path)
                tab_id = sw.evaluate("async (url) => (await chrome.tabs.query({url}))[0].id", site + path)
                pop = ctx.new_page()  # tests can't click the toolbar: the panel's page, pointed at that tab
                pop.on("pageerror", lambda e: errors.append("panel: " + str(e)))
                pop.goto(f"chrome-extension://{ext_id}/popup.html?tab={tab_id}")
                return paper, pop

            sw.evaluate("base => chrome.storage.local.set({testAppBase: base})", BASE)
            paper, pop = panel_for("/article.html")
            expect(pop.locator("#title")).to_contain_text("Sustainable decommissioning", timeout=90000)
            expect(pop.locator("#existing")).to_contain_text("[@topham2017]")  # imported above
            expect(pop.locator("#ft")).to_contain_text("Full text found", timeout=30000)
            pop.fill("#project", "From the toolbar")
            pop.screenshot(path=os.path.join(shots, "extension-toolbar.png"))
            pop.click("#add")
            expect(pop.locator("#doneMsg")).to_contain_text("[@topham2017]", timeout=60000)
            expect(page.locator("#projNav")).to_contain_text("From the toolbar", timeout=30000)  # the open tab updates
            pop.close()
            paper.close()

            # the panel again, twice: it uses the open Citrello tab and never opens another one (which would say
            # "already open in another tab"); also after the extension lost track of the tab (e.g. a tab opened
            # before the extension was installed): it finds the tab and adds its page script
            app_tabs = lambda: sw.evaluate("""async (base) => (await chrome.tabs.query({url: base + '/app/*'}))
                .filter((t) => !t.url.includes('clip')).length""", BASE)  # noqa: E731
            for forget in (False, True):
                if forget:
                    sw.evaluate("chrome.storage.session.clear()")
                paper, pop = panel_for("/article.html")
                expect(pop.locator("#existing")).to_contain_text("[@topham2017]", timeout=60000)
                pop.close()
                paper.close()
                assert app_tabs() == 1
            # a second Citrello tab ("already open in another tab") doesn't confuse it
            second = ctx.new_page()
            second.goto(BASE + "/app/")
            expect(second.locator("#webCover")).to_contain_text("already open in another tab", timeout=30000)
            sw.evaluate("chrome.storage.session.clear()")
            paper, pop = panel_for("/article.html")
            expect(pop.locator("#existing")).to_contain_text("[@topham2017]", timeout=60000)
            pop.close()
            paper.close()
            second.close()
            assert app_tabs() == 1

            # with no Citrello tab open: the extension opens Citrello in a background tab and uses it
            page.close()
            paper, pop = panel_for("/pdfonly.html")
            expect(pop.locator("#title")).to_contain_text("Sailing through end-of-life", timeout=LOAD)
            expect(pop.locator("#ft")).to_contain_text("Full text found", timeout=30000)
            pop.click("#add")
            expect(pop.locator("#doneMsg")).to_contain_text("With its full text", timeout=60000)
            app_tabs = sw.evaluate("async (base) => (await chrome.tabs.query({})).filter((t) => (t.url || '').startsWith(base + '/app/') && !t.url.includes('clip')).length", BASE)
            assert app_tabs == 1  # opened once, and left open for next time
            assert not errors, errors
            ctx.close()
    finally:
        srv.shutdown()


def hard_publisher(pdf_path):
    """Pretend publisher pages where the text isn't on the page: a PDF link to another site (only the extension may
    fetch from there), a script button that opens the PDF in a new tab, a link to a viewer page with the PDF inside,
    and an "are you human?" check that turns into the article once answered (state["solved"])."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    state = {"solved": False}
    article = "<article><h2>1. Introduction</h2>" + "".join(
        f"<p>Section {i}. Leaving scour protection in place protects marine habitats and lowers the cost of removal.</p>"
        for i in range(60)) + "<h2>Results</h2></article>"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            other = f"http://localhost:{self.server.server_address[1]}"  # another site than 127.0.0.1
            page = {
                "/link.html": f'<title>Link</title><p>Abstract only.</p><a href="{other}/files/paper.pdf">Download PDF</a>',
                "/button.html": "<title>Button</title><p>Abstract only.</p><button onclick=\"window.open('/files/paper.pdf')\">View PDF</button>",
                "/viewer.html": f'<title>Viewer</title><p>Abstract only.</p><a href="{other}/epdf/paper">PDF</a>',
                "/epdf/paper": '<title>Reader</title><iframe src="/files/paper.pdf"></iframe>',
                "/check.html": article if state["solved"] else
                    "<title>Just a moment...</title><p>Verify you are human by completing the action below.</p>",
            }.get(self.path)
            if self.path == "/files/paper.pdf":
                body, ctype = open(pdf_path, "rb").read(), "application/pdf"
            elif page:
                body, ctype = f"<html><head></head><body>{page}</body></html>".encode(), "text/html"
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
    return f"http://127.0.0.1:{srv.server_address[1]}", srv, state


def test_extension_finds_the_pdf_and_waits_for_you_at_a_check(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    pdf = str(tmp_path / "paper.pdf")
    T.make_pdf(pdf, [["A paper only available as a PDF"] +
                     [f"Section {i}: decommissioning offshore wind farms costs more than planned." for i in range(40)]])
    site, srv, state = hard_publisher(pdf)
    ext = str(tmp_path / "ext")
    subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "build_extension.py"), "--test", ext], check=True)
    bib = tmp_path / "refs.bib"
    bib.write_text("".join(f"@article{{{k}, title={{Paper behind a {k}}}, year={{2020}}, url={{{site}/{k}.html}}}}\n"
                           for k in ("link", "button", "viewer", "check")), encoding="utf-8")
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=True,
                                                        viewport={"width": 1280, "height": 800},
                                                        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(BASE + "/app/")
            page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
            page.click("#wWebStart")
            page.wait_for_function("typeof extVersion !== 'undefined' && extVersion", timeout=15000)
            page.evaluate("window.CITRELLO_TEST_FAST = true")
            page.click("#openImport")
            page.click('[data-guide="imp-file"]')
            page.set_input_files("#importFile", str(bib))
            expect(page.locator("#libRows .ref")).to_have_count(4, timeout=60000)
            page.click("#gNext")
            page.click('.side button.navb[data-tab="papers"]')
            with ctx.expect_page() as fetch_page:
                page.click("#ftExt")
            fp = fetch_page.value
            fp.click("#start")
            # the check: the extension stops, shows the page to the person and waits
            expect(fp.locator("#msg")).to_contain_text("wants to check that you're a person", timeout=180000)
            expect(fp.locator("#start")).to_have_text("Continue")
            fp.screenshot(path=os.path.join(shots, "extension-check.png"))
            check = next(p for p in ctx.pages if p.url.endswith("/check.html"))
            state["solved"] = True  # the person answers it; the page then shows the article
            check.reload()
            fp.bring_to_front()
            fp.click("#start")
            expect(fp.locator("#msg")).to_contain_text("Finished: 4 of 4 full texts added", timeout=180000)
            fp.screenshot(path=os.path.join(shots, "extension-fetch-hard.png"))
            fp.close()
            page.bring_to_front()
            expect(page.locator("#ftSumText")).to_contain_text("4 of 4 references have their full text", timeout=30000)
            assert not errors, errors
            ctx.close()
    finally:
        srv.shutdown()
