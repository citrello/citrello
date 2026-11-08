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
        page.click("#openImport")
        page.click('[data-guide="imp-file"]')
        page.set_input_files("#importFile", str(bib))
        expect(page.locator("#libRows .ref")).to_have_count(3, timeout=60000)
        page.click("#gNext")
        page.click('[data-guide="imp-papers"]')
        page.click("#ftFree")
        expect(page.locator("#gResult")).to_contain_text("Free full text found for 2 of 3", timeout=240000)
        expect(page.locator("#gResult")).to_contain_text("1 need your access")
        page.click("#gNext")
        expect(page.locator('#libRows .ref[data-sel="vetters2024"] .ftmark')).to_be_visible()
        expect(page.locator('#libRows .ref[data-sel="plos2007"] .ftmark')).to_be_visible()
        expect(page.locator('#libRows .ref[data-sel="lecun2015"] .ftmark')).to_have_count(0)
        assert not errors, errors
        b.close()
