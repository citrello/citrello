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
# With a screen (e.g. xvfb-run), a real browser window: PDFs then open in Chrome's viewer as they do for people
# (headless Chromium downloads them instead). CI runs these under xvfb-run.
HEADLESS = not os.environ.get("DISPLAY")


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
            ctx = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=HEADLESS,
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
            page.uncheck("#ftVerify")  # these references point at pretend pages on purpose: no online check
            with ctx.expect_page() as fetch_page:
                page.click("#ftExt")
            fp = fetch_page.value
            expect(fp.locator("#list li")).to_have_count(3, timeout=15000)
            fp.click("#start")
            expect(fp.locator("#msg")).to_contain_text("Finished:", timeout=180000)
            expect(fp.locator("#msg")).to_contain_text("Finished: 2 of 3 full texts added", timeout=1000)
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
            blank = ctx.new_page()  # with a screen, closing the last tab closes the window and new tabs fail
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
    T.make_pdf(pdf, [["Paper behind a link, button, viewer and check: only available as a PDF"] +
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
            ctx = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=HEADLESS,
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
            page.uncheck("#ftVerify")  # these references point at pretend pages on purpose: no online check
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


def platform_site(pdf_path, other_pdf=None):
    """Pages like the real platforms: a redirect page (like linkinghub.elsevier.com) to an article built by scripts
    after loading, a security check that clears by itself, a "/doi/" platform page whose PDF is only at the
    platform's usual address (/doi/pdf/<doi>), a web page with readable text, and a web tool with nothing to read."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    paras = "".join(f"<p>Paragraph {i}. Solar trackers raise the yield of concentrating photovoltaics in hot climates.</p>" for i in range(70))
    article = f"<article><h2>1. Introduction</h2>{paras}<h2>2. Results</h2><p>More.</p></article>"
    late = ("<title>Article</title><main id='m'>Loading…</main><script>setTimeout(() => {"
            f"document.getElementById('m').innerHTML = {article!r}; }}, 2000)</script>")
    pages = {
        "/redirect.html": "<title>Redirecting</title><p>Redirecting…</p><script>setTimeout(() => location.replace('/late.html'), 2500)</script>",
        "/late.html": late,
        "/autocheck.html": ("<title>Just a moment...</title><p id='c'>Checking your browser before accessing the site.</p>"
                            f"<script>setTimeout(() => {{ document.title = 'Paper'; document.body.innerHTML = {article!r}; }}, 3000)</script>"),
        "/doi/10.5555/platform": "<title>A platform paper</title><p>Abstract only. Sign in to read more.</p>",
        "/sd/pii/S123": ("<title>Rotating prism array - ScienceDirect</title><h1>Rotating prism array for solar tracking</h1>"
                         "<h2>Abstract</h2><p>Abstract only.</p><a href='/sd/pii/S123/pdfft?md5=abc&pid=1'>View PDF</a>"
                         "<div class='RelatedContent'><h2>Recommended articles</h2><a href='/sd/pii/S999/pdfft'>View PDF</a></div>"),
        "/book.html": "<title>Nonimaging Optics</title><h1>Nonimaging Optics</h1><p>Book description.</p><a href='/ch1.pdf'>Chapter 1 PDF</a>",
        "/tool.html": "<title>Solar path calculator</title><div id='app'>Latitude <input> Longitude <input> <button>Go</button></div>",
        "/webpage.html": "<title>About solar maps</title><main>" + "".join(
            f"<p>Line {i} of an explanatory web page about solar radiation maps and how to read them.</p>" for i in range(40)) + "</main>",
    }

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.split("?")[0] in ("/sd/pii/S123/pdfft", "/sd/pii/S999/pdfft"):
                # like ScienceDirect: the PDF link works only as a page visit (not for a script fetching it), and
                # sends the visit on to a signed address that anyone holding it may fetch
                if self.headers.get("Sec-Fetch-Mode") != "navigate":
                    self.send_response(403)
                    self.end_headers()
                    return
                which = "S123" if "S123" in self.path else "S999"
                self.send_response(302)
                self.send_header("Location", f"/sd/signed/{which}.pdf?X-Amz-Signature=abc")
                self.end_headers()
                return
            if self.path.startswith("/sd/signed/"):
                body = open(pdf_path if "S123" in self.path else other_pdf, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/cfcheck.html":  # like Cloudflare: a check that passes by itself, sets a cookie, reloads
                passed = "cf_clearance=1" in (self.headers.get("Cookie") or "")
                page = (article if passed else "<title>Just a moment...</title><p>Performing security verification</p>"
                        "<script>setTimeout(() => { document.cookie = 'cf_clearance=1; path=/'; location.reload(); }, 4000)</script>")
                body = f"<html><head></head><body>{page}</body></html>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/doi/pdf/10.5555/platform":
                body, ctype = open(pdf_path, "rb").read(), "application/pdf"
            elif self.path in pages:
                body, ctype = f"<html><head></head><body>{pages[self.path]}</body></html>".encode(), "text/html"
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
    return srv.server_address[1], srv


def test_extension_waits_for_pages_knows_platforms_and_runs_in_parallel(tmp_path):
    import json
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    pdf = str(tmp_path / "paper.pdf")
    T.make_pdf(pdf, [["A platform paper: rotating prism array for solar tracking"] + [f"Section {i}: tracking the sun with rotating prisms." for i in range(40)]])
    other = str(tmp_path / "other.pdf")  # another paper's PDF, linked from a "Recommended" box
    T.make_pdf(other, [["Thin-film micro-concentrator solar cells"] + [f"Other paper, section {i}." for i in range(40)]])
    port, srv = platform_site(pdf, other)
    a, b = f"http://127.0.0.1:{port}", f"http://localhost:{port}"  # two "publishers", so two run at once
    refs = [{"id": "redirect", "type": "article-journal", "title": "Behind a redirect", "URL": f"{a}/redirect.html"},
            {"id": "autocheck", "type": "article-journal", "title": "Behind a check that clears", "URL": f"{b}/autocheck.html"},
            {"id": "platform", "type": "article-journal", "title": "On a doi platform", "URL": f"{a}/doi/10.5555/platform"},
            {"id": "webpage", "type": "webpage", "title": "A web page", "URL": f"{b}/webpage.html"},
            {"id": "tool", "type": "webpage", "title": "A web tool", "URL": f"{a}/tool.html"},
            {"id": "cfcheck", "type": "article-journal", "title": "Behind a check that reloads", "URL": f"{b}/cfcheck.html"},
            {"id": "sd", "type": "article-journal", "title": "Rotating prism array for solar tracking", "URL": f"{a}/sd/pii/S123"},
            {"id": "book", "type": "book", "title": "Nonimaging Optics", "URL": f"{b}/book.html"}]
    lib = tmp_path / "refs.json"
    lib.write_text(json.dumps(refs), encoding="utf-8")
    ext = str(tmp_path / "ext")
    subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "build_extension.py"), "--test", ext], check=True)
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(tmp_path / "profile"), channel="chromium", headless=HEADLESS,
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
            page.set_input_files("#importFile", str(lib))
            expect(page.locator("#libRows .ref")).to_have_count(8, timeout=60000)
            page.click("#gNext")
            page.click('.side button.navb[data-tab="papers"]')
            page.uncheck("#ftVerify")  # these references point at pretend pages on purpose: no online check
            with ctx.expect_page() as fetch_page:
                page.click("#ftExt")
            fp = fetch_page.value
            fp.click("#start")
            fp.wait_for_function("document.querySelectorAll('#list li.work').length >= 2", timeout=60000)  # two at once
            expect(fp.locator("#msg")).to_contain_text("Finished: 6 of 8 full texts added", timeout=300000)
            items = fp.locator("#list li")
            expect(items.nth(0)).to_contain_text("Full text ✓")              # waited for the redirect and the script
            expect(items.nth(1)).to_contain_text("Full text ✓")              # the check cleared by itself: no stop
            expect(items.nth(2)).to_contain_text("Full text ✓ (PDF)")        # the platform's own PDF address
            expect(items.nth(3)).to_contain_text("Full text ✓")              # a web page's readable text
            expect(items.nth(4)).to_contain_text("Web page: nothing to read")  # a tool, not "No access"
            expect(items.nth(5)).to_contain_text("Full text ✓")  # the check passed by itself and reloaded: no stop
            # its own PDF (opened in the tab, as the site refuses fetching it), not the "Recommended" paper's
            expect(items.nth(6)).to_contain_text("Full text ✓ (PDF)")
            expect(items.nth(7)).to_contain_text("Book: add the chapters you cite")
            # the report for what didn't work: how the page was built, no text
            expect(fp.locator("#report")).to_be_visible()
            ctx.grant_permissions(["clipboard-read", "clipboard-write"])
            fp.click("#report")
            report = fp.evaluate("navigator.clipboard.readText()")
            assert "A web tool" in report and "Web page: nothing to read" in report and "Solar path calculator" in report
            fp.close()
            page.bring_to_front()
            text = page.evaluate("fetch('/api/fulltext/text?key=' + encodeURIComponent(papersLib.items.find((i) => i.title.startsWith('Rotating')).key)).then((r) => r.text())")
            assert "rotating prism" in text.lower() and "Thin-film" not in text
            assert not errors, errors
            ctx.close()
    finally:
        srv.shutdown()


def test_platform_pdf_addresses():
    """The rule book of where platforms keep a paper's PDF (run with node, the same code the extension runs)."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("needs node")
    js = """
const src = require('fs').readFileSync(process.argv[1], 'utf8');
eval(src.match(/function publisherPdfUrls[\\s\\S]*?\\n  }\\n/)[0]);
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(([u, d]) => publisherPdfUrls(u, d))));"""
    cases = [["https://ieeexplore.ieee.org/document/7077026", ""],
             ["https://www.sciencedirect.com/science/article/pii/S1876610214013988?via%3Dihub", "10.1016/j.egypro.2014.10.031"],
             ["https://onlinelibrary.wiley.com/doi/full/10.1002/pip.2843", ""],
             ["https://link.springer.com/article/10.1007/abc", "10.1007/abc"],
             ["https://www.nature.com/articles/s41565-017-0052-4", ""],
             ["https://www.tandfonline.com/doi/abs/10.1080/1234.5678", ""],
             ["https://pubs.acs.org/doi/10.1021/nl302516v", ""],
             ["https://arxiv.org/abs/1706.03762", ""]]
    import json
    out = json.loads(subprocess.run(["node", "-e", js, os.path.join(ROOT, "extension", "grab.js"), json.dumps(cases)],
                                    capture_output=True, text=True, check=True).stdout)
    assert out[0][0] == "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=7077026"
    assert out[1][0].startswith("https://www.sciencedirect.com/science/article/pii/S1876610214013988/pdfft")
    assert out[2][0] == "https://onlinelibrary.wiley.com/doi/pdfdirect/10.1002/pip.2843?download=true"
    assert out[3][0] == "https://link.springer.com/content/pdf/10.1007/abc.pdf"
    assert out[4][0] == "https://www.nature.com/articles/s41565-017-0052-4.pdf"
    assert out[5][0] == "https://www.tandfonline.com/doi/pdf/10.1080/1234.5678"
    assert out[6][0] == "https://pubs.acs.org/doi/pdf/10.1021/nl302516v"
    assert out[7][0] == "https://arxiv.org/pdf/1706.03762"


def test_doi_candidates_without_glued_on_endings():
    """The extension tries a DOI again without a site's own number or ending when doi.org doesn't know it."""
    import json
    import shutil
    if not shutil.which("node"):
        pytest.skip("needs node")
    js = """
const src = require('fs').readFileSync(process.argv[1], 'utf8');
eval(src.match(/function doiCandidates[\\s\\S]*?\\n}\\n/)[0]);
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(doiCandidates)));"""
    cases = ["10.1063/1.4812391/138579", "10.1016/j.renene.2016.10.066", "10.1002/pip.2843/full", "10.1234/abc.pdf"]
    out = json.loads(subprocess.run(["node", "-e", js, os.path.join(ROOT, "extension", "fetch.js"), json.dumps(cases)],
                                    capture_output=True, text=True, check=True).stdout)
    assert out[0] == ["10.1063/1.4812391/138579", "10.1063/1.4812391"]
    assert out[1] == ["10.1016/j.renene.2016.10.066"]  # a right DOI stays as it is
    assert out[2][-1] == "10.1002/pip.2843" and out[3][-1] == "10.1234/abc"


def test_doi_addresses_and_dois_with_odd_characters():
    """Old Wiley DOIs contain # < > ; which must be encoded for doi.org; some PDFs are known from the DOI alone."""
    import json
    import shutil
    if not shutil.which("node"):
        pytest.skip("needs node")
    js = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
eval(src.match(/const doiUrl = [^\n]*\n/)[0].replace('const ', 'var ')); eval(src.match(/function doiPdfUrls[\s\S]*?\n}\n/)[0]);
console.log(JSON.stringify([doiUrl(process.argv[2]), doiPdfUrls('10.21236/ada213404'), doiPdfUrls('10.48550/arXiv.1706.03762')]));"""
    out = json.loads(subprocess.run(["node", "-e", js, os.path.join(ROOT, "extension", "fetch.js"),
                                     "10.1002/(sici)1099-159x(199603/04)4:2<155::aid-pip128>3.0.co;2-#"],
                                    capture_output=True, text=True, check=True).stdout)
    assert out[0] == "https://doi.org/10.1002/(sici)1099-159x(199603/04)4%3A2%3C155%3A%3Aaid-pip128%3E3.0.co%3B2-%23"
    assert out[1] == ["https://apps.dtic.mil/sti/pdfs/ADA213404.pdf"] and out[2] == ["https://arxiv.org/pdf/1706.03762"]
