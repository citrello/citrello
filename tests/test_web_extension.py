"""The Citrello browser extension (extension/), loaded into a real Chromium with the web app.

- Bulk: "Fetch the missing ones with my access" opens the extension's fetch page, which visits each paper (pretend
  publisher pages here) in a background tab and hands the text to Citrello; a page without the text is reported.
- The toolbar button on a paper's page: the "Add to Citrello" pop-up gets the paper's text from the extension.

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
                                                        viewport={"width": 1280, "height": 900},
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

            # the toolbar button on a paper's page: the pop-up gets the paper's text from the extension
            sw.evaluate("url => chrome.storage.local.set({testClipUrl: url})", BASE + "/app/clip.html")
            paper = ctx.new_page()
            paper.goto(site + "/article.html")
            paper.bring_to_front()
            with ctx.expect_page() as popup:
                sw.evaluate("""async (url) => { const [tab] = await chrome.tabs.query({url});
                                                 await self.clipTab(tab); }""", site + "/article.html")
            pop = popup.value
            expect(pop.locator("#title")).to_contain_text("Sustainable decommissioning", timeout=90000)
            expect(pop.locator("#ftLine")).to_contain_text("Full text found", timeout=30000)
            pop.screenshot(path=os.path.join(shots, "extension-toolbar.png"))
            assert not errors, errors
            ctx.close()
    finally:
        srv.shutdown()
