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
            page.click('[data-guide="imp-papers"]')
            expect(page.locator("#guide")).to_contain_text("Papers behind your university login")
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
            page.keyboard.press("Escape")
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
