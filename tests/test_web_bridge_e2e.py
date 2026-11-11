"""End to end: the web app (in Chromium and WebKit) signs in to a local bridge and answers ChatGPT/Claude from the
browser tab. An OAuth client (standing in for ChatGPT) searches the library, adds a reference and makes a Word file.

Run the bridge (see bridge/README.md) and serve the site, then:
  CITRELLO_BRIDGE_E2E=1 CITRELLO_BRIDGE=http://localhost:8787 CITRELLO_WEB_CHECK=http://localhost:8099 \
    pytest tests/test_web_bridge_e2e.py
"""
import json
import os
import re
import time
import urllib.parse
import zipfile

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("CITRELLO_BRIDGE_E2E") != "1" or not os.environ.get("CITRELLO_WEB_CHECK"),
                                reason="needs a running bridge and the site served locally")

import test_bridge_e2e as B  # noqa: E402  (its OAuth helpers)
from test_web_app import BROWSERS, LOAD, LATEX_BIB  # noqa: E402

BASE = os.environ.get("CITRELLO_WEB_CHECK", "").rstrip("/")


def access_token(email):
    """Sign in like ChatGPT does (OAuth with an emailed code) and return an access token."""
    B.EMAIL = email
    client, p, verifier = B.oauth_sign_in()
    _, _, html = B.call("POST", "/authorize", {**p, "step": "send", "email": email}, form=True)
    code = re.search(r"your code is (\d{6})", html).group(1)
    _, h, _ = B.call("POST", "/authorize", {**p, "step": "verify", "email": email, "code": code}, form=True)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h["Location"]).query))
    st, _, raw = B.call("POST", "/token", {"grant_type": "authorization_code", "code": q["code"],
                                           "redirect_uri": p["redirect_uri"], "client_id": client["client_id"],
                                           "code_verifier": verifier}, form=True)
    assert st == 200
    return json.loads(raw)["access_token"]


def tool(token, name, args):
    st, _, raw = B.rpc(token, "tools/call", {"name": name, "arguments": args})
    assert st == 200, raw
    return json.loads(raw)["result"]


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_browser_tab_is_the_device(browser_name, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    email = f"web-{browser_name}-{int(time.time())}@example.com"
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    bib = tmp_path / "refs.bib"
    bib.write_text(LATEX_BIB, encoding="utf-8")
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: d.accept())
        page.goto(f"{BASE}/app/?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        expect(page.locator("#welcome")).to_contain_text("ChatGPT and Claude")
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        expect(page.locator("#connText")).to_have_text("Web connectors: not signed in")
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#libRows .ref")).to_have_count(2, timeout=60000)  # the third entry is a duplicate
        page.click("#gNext")

        # sign in with an emailed code (the local bridge hands the latest code to the test)
        page.click('.side button.navb[data-tab="settings"]')
        page.fill("#acctEmail", email)
        page.click("#acctSend")
        expect(page.locator("#acctCodeRow")).to_be_visible(timeout=30000)
        code = B.bc.start_sign_in(email)["dev_code"]
        page.fill("#acctCode", code)
        page.click("#acctVerify")
        expect(page.locator("#acctConn")).to_contain_text("while this tab is open", timeout=30000)
        expect(page.locator("#connText")).to_have_text("ChatGPT and web: connected", timeout=30000)
        page.locator("#acctIn details").first.click()
        expect(page.locator("#acctDevices")).to_contain_text("(web app)", timeout=30000)
        page.screenshot(path=os.path.join(shots, f"web-connector-{browser_name}.png"), full_page=True)

        # ChatGPT (an OAuth client) uses the library in this browser tab
        token = access_token(email)
        tools = json.loads(B.rpc(token, "tools/list")[2])["result"]["tools"]
        assert "search_references" in {t["name"] for t in tools}
        res = tool(token, "search_references", {"query": "offshore"})
        assert not res["isError"] and "topham2017" in res["content"][0]["text"]
        res = tool(token, "add_reference", {"reference": "Burton, T. and Jenkins, N. (2011). Wind Energy Handbook. "
                                                         "Wiley, Chichester.", "project": "From ChatGPT"})
        assert not res["isError"], res
        expect(page.locator("#projNav")).to_contain_text("From ChatGPT", timeout=30000)  # the page updates itself
        res = tool(token, "create_word_document", {"text": "Costs are high [@topham2017].", "title": "Draft",
                                                   "style": "ieee"})
        assert not res["isError"], res
        res = tool(token, "create_word_document", {"text": "x", "output_path": "/tmp/elsewhere.docx"})
        assert res["isError"]  # the same safeguard as the desktop: only Citrello's Documents folder

        # the Word file ChatGPT made, downloaded from Settings
        page.click('.side button.navb[data-tab="library"]')
        page.click('.side button.navb[data-tab="settings"]')
        link = page.locator("#webDocs a", has_text="Draft.docx")
        expect(link).to_be_visible(timeout=30000)
        with page.expect_download() as dl:
            link.click()
        with zipfile.ZipFile(dl.value.path()) as z:
            assert "ADDIN CSL_CITATION" in z.read("word/document.xml").decode("utf-8")

        # what ChatGPT added is still there after a reload, and the tab reconnects by itself
        page.goto(f"{BASE}/app/?bridge={urllib.parse.quote(B.bc.BRIDGE)}")  # a reload loses the test bridge
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        expect(page.locator("#connText")).to_have_text("ChatGPT and web: connected", timeout=30000)
        assert "Wind Energy Handbook" in tool(token, "search_references", {"query": "handbook"})["content"][0]["text"]

        # with the tab closed, ChatGPT is told to open Citrello
        page.close()
        time.sleep(1.5)
        res = tool(token, "search_references", {"query": "offshore"})
        assert res["isError"] and "citrello.app/app" in res["content"][0]["text"]

        # signing out stops the connection
        page = ctx.new_page()
        page.goto(f"{BASE}/app/?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        page.click('.side button.navb[data-tab="settings"]')
        page.click("#acctSignOut")
        expect(page.locator("#connText")).to_have_text("Web connectors: not signed in", timeout=30000)
        time.sleep(1.5)
        assert tool(token, "search_references", {"query": "offshore"})["isError"]
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_find_free_full_texts_in_the_browser(browser_name, tmp_path):
    """"Find free full texts" in the web app, against the real services: a paper free in Europe PMC (read by the
    browser), one free in a university repository (its PDF through the bridge's /api/oa-pdf), one behind a paywall."""
    from playwright.sync_api import expect, sync_playwright
    bib = tmp_path / "three.bib"
    bib.write_text("""@article{plos2007, title={Organismal complexity and the evolution of gene regulation}, year={2007},
  doi={10.1371/journal.pone.0000217}}
@article{vetters2024, title={Sailing through end-of-life challenges}, year={2024}, doi={10.1016/j.rser.2024.114486}}
@article{lecun2015, title={Deep learning}, year={2015}, doi={10.1038/nature14539}}
""", encoding="utf-8")
    with sync_playwright() as pw:
        b = getattr(pw, browser_name).launch()
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/app/?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
        page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
        page.click("#wWebStart")
        page.click('.side button.navb[data-tab="library"]')  # a new library opens on Home
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#libRows .ref")).to_have_count(3, timeout=60000)
        page.click("#gNext")
        page.click('.side button.navb[data-tab="papers"]')
        page.click("#ftGet")
        expect(page.locator("#papersMsg")).to_contain_text("Free full text found for 2 of 3", timeout=240000)
        expect(page.locator("#papersMsg")).to_contain_text("1 need your access")
        page.click('.side button.navb[data-tab="library"]')
        expect(page.locator('#libRows .ref[data-sel="vetters2024"] .ftmark')).to_be_visible()
        expect(page.locator('#libRows .ref[data-sel="plos2007"] .ftmark')).to_be_visible()
        expect(page.locator('#libRows .ref[data-sel="lecun2015"] .ftmark')).to_have_count(0)
        assert not errors, errors
        b.close()


@pytest.mark.parametrize("browser_name", BROWSERS)
def test_claude_checks_what_my_sources_say(browser_name, tmp_path):
    """Pro: Claude's verdict for each citation (through the bridge, with a pretend Claude API), the exact words in
    "More details", and finding support in the library and on the web (and adding the paper found)."""
    from playwright.sync_api import expect, sync_playwright
    import docx
    import anthropic_mock
    from test_sources import PAPER
    srv = anthropic_mock.start()
    email = f"ai-{browser_name}-{int(time.time())}@example.com"
    bib = tmp_path / "two.bib"
    bib.write_text("@article{topham2017, title={Sustainable decommissioning of an offshore wind farm}, author={Topham, Eva}, year={2017}}\n"
                   "@article{lee2021, title={Export cable recovery costs}, author={Lee, Ann}, year={2021}}\n", encoding="utf-8")
    d = docx.Document()
    d.add_paragraph("Removing the export cables was the largest share of the decommissioning cost [@topham2017]. "
                    "Leaving the cables buried doubled the cost [@topham2017].")
    doc = tmp_path / "claims.docx"
    d.save(str(doc))
    lee = ("Export cable recovery costs.\n\n" + " ".join(
        f"Part {i}. In every farm studied, leaving the export cables buried in place doubled the cost of later works." for i in range(15)))
    try:
        with sync_playwright() as pw:
            b = getattr(pw, browser_name).launch()
            page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{BASE}/app/?bridge={urllib.parse.quote(B.bc.BRIDGE)}")
            page.wait_for_selector("#webCover", state="detached", timeout=LOAD)
            page.click("#wWebStart")
            page.click('.side button.navb[data-tab="library"]')
            page.click("#openImport")
            page.click('[data-guide="imp-file"]')
            page.set_input_files("#importFile", str(bib))
            expect(page.locator("#gResult")).to_contain_text("2 added", timeout=60000)
            page.keyboard.press("Escape")
            for key, text in (("topham2017", PAPER), ("lee2021", lee)):
                assert page.evaluate("""async ([key, text]) => (await fetch('/api/clip/fulltext', {method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-Key': key}, body: JSON.stringify({text})})).status""", [key, text]) == 200
            tab = lambda name: page.click(f'.side button.navb[data-tab="{name}"]')  # noqa: E731
            tab("documents")
            page.set_input_files("#chkFile", str(doc))
            page.click("#chkSources", timeout=120000)
            # not signed in: word matches, and the way to Claude's verdicts
            expect(page.locator("#srcAi")).to_contain_text("sign in to your Citrello account", timeout=30000)
            tab("settings")
            page.fill("#acctEmail", email)
            page.click("#acctSend")
            expect(page.locator("#acctCodeRow")).to_be_visible(timeout=30000)
            page.fill("#acctCode", B.bc.start_sign_in(email)["dev_code"])
            page.click("#acctVerify")
            expect(page.locator("#acctIn")).to_be_visible(timeout=30000)
            tab("documents")
            page.click("#chkSources")
            page.click("#srcAiGo", timeout=30000)  # agrees to send sentences and passages to Claude, once
            src = page.locator("#chkSrc .src")
            expect(src.nth(0)).to_contain_text("Supports this", timeout=60000)
            expect(src.nth(1)).to_contain_text("Seems to contradict this")
            expect(src.nth(0).locator(".cite")).to_have_text("[@topham2017]")  # the citation, in colour
            expect(page.locator("#srcAi")).to_contain_text("This month: 2 of 3 Claude checks, 0 of 3 web checks")
            # More details: Claude's reason and the exact words, highlighted in their passage
            src.nth(1).locator("[data-src-more]").click()
            expect(src.nth(1).locator("mark")).to_contain_text("halved that cost")
            expect(src.nth(1)).to_contain_text("The paper says it halved the cost.")
            # find support in the library: another paper that says it
            src.nth(1).locator('[data-src-find="library"]').click()
            expect(src.nth(1).locator(".src-found")).to_contain_text("Found in your library", timeout=60000)
            expect(src.nth(1).locator(".src-found")).to_contain_text("Export cable recovery costs")
            expect(src.nth(1).locator('.src-found button[data-copy="[@lee2021]"]')).to_be_visible()
            # and on the web: Claude's search, then the paper goes to the library
            src.nth(1).locator('[data-src-find="web"]').click()
            found = src.nth(1).locator(".src-found")
            expect(found).to_contain_text("Found on the web", timeout=60000)
            expect(found).to_contain_text("Cable recovery dominates offshore wind decommissioning costs")
            found.locator("[data-src-addweb]").click()
            expect(found.locator("button[data-copy]")).to_be_visible(timeout=60000)
            expect(page.locator("#srcAi")).to_contain_text("1 of 3 web checks")
            # this test's free accounts get 3 checks: the 4th says so plainly; Pro (switched on by the admin for now) goes on
            src.nth(1).locator('[data-src-find="library"]').click()
            expect(src.nth(1).locator(".src-found")).to_contain_text("Free accounts get 3 Claude checks a month", timeout=60000)
            assert B.call("POST", "/admin/pro", {"email": email, "pro": True}, headers={"Authorization": "Bearer testadmin"})[0] == 200
            src.nth(1).locator('[data-src-find="library"]').click()
            expect(src.nth(1).locator(".src-found")).to_contain_text("Found in your library", timeout=60000)
            page.locator("#chkSrcPanel").scroll_into_view_if_needed()
            page.screenshot(path=os.path.join(os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path), f"web-bridge-{browser_name}-claude.png"))
            # checking again doesn't ask (or pay) Claude twice: the verdicts come from this browser
            n = len(anthropic_mock.SEEN)
            page.click("#chkSources")
            expect(src.nth(1)).to_contain_text("Seems to contradict this", timeout=30000)
            expect(page.locator("#srcAi")).to_contain_text("Verdicts by Claude")
            assert len(anthropic_mock.SEEN) == n
            assert not errors, errors
            b.close()
    finally:
        srv.stop()
