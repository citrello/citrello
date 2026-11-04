"""
End-to-end tests: start the real Citrello app with an empty library and use it like a person would, in a
browser. Every step saves a screenshot, so a failure can be seen, not just read.

  pip install pytest playwright python-docx && python -m playwright install chromium
  pytest tests/e2e                                      # runs `python app.py`
  CITRELLO_APP_CMD=dist/Citrello.exe pytest tests/e2e   # the built app
  CITRELLO_E2E_ONLINE=1 ...                             # also the scenarios that look references up online

Screenshots go to $CITRELLO_E2E_SHOTS (default tests/e2e/screenshots).
"""
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = "http://localhost:8585/"
ONLINE = os.environ.get("CITRELLO_E2E_ONLINE") == "1"
SHOTS = os.environ.get("CITRELLO_E2E_SHOTS") or os.path.join(ROOT, "tests", "e2e", "screenshots")
online = pytest.mark.skipif(not ONLINE, reason="needs internet (set CITRELLO_E2E_ONLINE=1)")

BIB = r"""@article{topham2017,
  title = {Sustainable decommissioning of an offshore wind farm},
  author = {Topham, Eva and McMillan, David},
  journal = {Renewable Energy}, year = {2017}, volume = {102}, pages = {470--480},
  doi = {10.1016/j.renene.2016.10.066}}
@article{vetters2024,
  title = {Sailing through end-of-life challenges: A comprehensive review for offshore wind},
  author = {Vetters, Jan and Thomassen, Gwenny and Van Passel, Steven},
  journal = {Renewable and Sustainable Energy Reviews}, year = {2024}, volume = {199},
  doi = {10.1016/j.rser.2024.114486}}
@book{burton2011, title = {Wind Energy Handbook}, author = {Burton, Tony and Jenkins, Nick},
  publisher = {Wiley}, year = {2011}}
"""


def _cmd(var, script):
    v = os.environ.get(var)
    if not v:
        return [sys.executable, os.path.join(ROOT, script)]
    cmd = shlex.split(v, posix=not sys.platform.startswith("win"))
    cmd[0] = os.path.abspath(cmd[0]) if os.path.exists(cmd[0]) else cmd[0]  # the app runs in a temporary folder
    return cmd


@pytest.fixture(scope="module")
def env():
    tmp = tempfile.mkdtemp(prefix="citrello_e2e_")
    e = dict(os.environ, CITRELLO_HOME=os.path.join(tmp, "Citrello"), CITRELLO_ACCOUNT_FILE=os.path.join(tmp, "account.json"),
             CITRELLO_BIB_EXPORTS=os.path.join(tmp, "exports.json"), CODEX_HOME=os.path.join(tmp, "codex"),
             CITRELLO_LEGACY_HOME=os.path.join(tmp, "none"), CITRELLO_BRIDGE="http://127.0.0.1:9",
             CITRELLO_APP_CONFIG=os.path.join(tmp, "app.json"), CITRELLO_INSTALL_FILE=os.path.join(tmp, "install.json"))
    if ONLINE:  # the unit tests' conftest points lookups at a dead address; use the real services here
        e.pop("CITRELLO_CROSSREF", None)
        e.pop("CITRELLO_DOIORG", None)
    else:
        e.update(CITRELLO_CROSSREF="http://127.0.0.1:9", CITRELLO_DOIORG="http://127.0.0.1:9")
    e["_TMP"] = tmp
    return e


@pytest.fixture(scope="module")
def app(env):
    log = open(os.path.join(env["_TMP"], "app.log"), "w")
    proc = subprocess.Popen(_cmd("CITRELLO_APP_CMD", "app.py") + ["--no-browser"], env=env, stdout=log, stderr=log, cwd=env["_TMP"])
    for _ in range(80):
        try:
            urllib.request.urlopen(URL + "api/ping", timeout=2)
            break
        except OSError:
            time.sleep(0.5)
    else:
        proc.kill()
        log.close()
        raise RuntimeError("Citrello didn't start:\n" + open(log.name).read())
    yield proc
    try:
        req = urllib.request.Request(URL + "api/quit", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=5)
    except OSError:
        pass
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    print("\n--- app log ---\n" + open(log.name).read()[-4000:])


@pytest.fixture(scope="module")
def page(app):
    os.makedirs(SHOTS, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 860}, accept_downloads=True)
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=URL.rstrip("/"))
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.goto(URL)
        yield pg
        browser.close()


_n = [0]


def shot(pg, name):
    _n[0] += 1
    pg.screenshot(path=os.path.join(SHOTS, f"{_n[0]:02d}-{name}.png"), full_page=True)


def tab(pg, name):
    pg.click(f'.side button.navb[data-tab="{name}"]')


def write(env, name, data):
    p = os.path.join(env["_TMP"], name)
    with open(p, "wb" if isinstance(data, bytes) else "w", encoding=None if isinstance(data, bytes) else "utf-8") as f:
        f.write(data)
    return p


# ---------------------------------------------------------------------------

def test_00_welcome_on_first_launch(page):
    expect(page.locator("#welcome")).to_be_visible()
    expect(page.locator("#wTitle")).to_have_text("Welcome to Citrello")
    expect(page.locator("#wNews")).not_to_be_checked()   # feature emails are opt-in
    shot(page, "welcome")
    page.click("#wSkip")
    expect(page.locator("#welcome")).to_be_hidden()
    page.reload()
    page.wait_for_timeout(1000)
    expect(page.locator("#welcome")).to_be_hidden()     # only once


def test_01_starts_with_an_empty_library(page):
    expect(page.locator("#libEmpty")).to_be_visible()
    expect(page.locator("#ver")).to_contain_text("v")
    shot(page, "empty-library")
    assert not page.errors


def test_02_import_bibtex_keeps_keys(page, env):
    page.click("#openImport")
    page.fill("#importProject", "Thesis")
    page.set_input_files("#importFile", write(env, "refs.bib", BIB))
    expect(page.locator("#importMsg")).to_contain_text("3 added", timeout=20000)
    expect(page.locator("#libRows .ref")).to_have_count(3)
    for key in ("topham2017", "vetters2024", "burton2011"):
        expect(page.locator(f'#libRows .ref[data-sel="{key}"]')).to_be_visible()
    shot(page, "imported-bib")


def test_03_detail_panel_and_copy(page):
    page.click('#libRows .ref[data-sel="topham2017"]')
    expect(page.locator("#detail h2")).to_have_text("Sustainable decommissioning of an offshore wind farm")
    expect(page.locator("#detail")).to_contain_text("[@topham2017]")
    expect(page.locator("#detail")).to_contain_text("\\cite{topham2017}")
    expect(page.locator("#detail .formatted")).to_contain_text("Topham")
    page.click('#detail button[data-copy="[@topham2017]"]')
    expect(page.locator("#toast")).to_contain_text("Copied")
    shot(page, "detail-panel")


def test_04_projects_and_search(page):
    expect(page.locator('#projNav [data-proj-nav="Thesis"]')).to_be_visible()
    page.click('#projNav [data-proj-nav="Thesis"]')
    expect(page.locator("#libTitle")).to_have_text("Thesis")
    page.fill("#q", "handbook")
    expect(page.locator("#libRows .ref")).to_have_count(1)
    shot(page, "search")
    page.fill("#q", "")
    page.click('#projNav [data-proj-nav="*"]')
    expect(page.locator("#libRows .ref")).to_have_count(3)


def test_05_search_box_offers_to_add_a_doi(page):
    page.fill("#q", "10.1038/s41565-017-0052-4")
    expect(page.locator("#qAdd")).to_be_visible()
    expect(page.locator("#qAddText")).to_contain_text("this DOI")
    shot(page, "search-offers-add")
    page.fill("#q", "")
    expect(page.locator("#qAdd")).to_be_hidden()


def test_06_export_bibtex(page):
    page.click("#openExport")
    with page.expect_download() as d:
        page.click("#expBib")
    text = open(d.value.path(), encoding="utf-8").read()
    assert "@article{topham2017," in text and "@book{burton2011," in text
    assert "pages = {470--480}" in text


def test_07_update_word_document_with_markers(page, env):
    import docx
    d = docx.Document()
    d.add_paragraph("Decommissioning is costly [@topham2017] and reviewed elsewhere [@vetters2024; @burton2011].")
    path = os.path.join(env["_TMP"], "draft.docx")
    d.save(path)
    tab(page, "documents")
    page.select_option("#updStyle", "ieee")
    page.set_input_files("#updFile", path)
    link = page.locator("#updResult a.btn")
    expect(link).to_be_visible(timeout=60000)
    expect(page.locator("#updResult")).to_contain_text("2 citations")
    shot(page, "word-updated")
    with page.expect_download() as dl:
        link.click()
    with zipfile.ZipFile(dl.value.path()) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "ADDIN CSL_CITATION" in xml and "CSL_BIBLIOGRAPHY" in xml
    assert "[@topham2017]" not in xml


def _latex_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("main.tex", "\\documentclass{article}\\begin{document}\n"
                               "Costs \\cite{topham2017,missing2020}.\n\\bibliography{refs}\\end{document}")
        z.writestr("refs.bib", BIB.replace("@book{burton2011", "@book{burtonDup")
                   + "@article{dupe, title={Sustainable decommissioning of an offshore wind farm}, year={2017}, "
                     "doi={10.1016/j.renene.2016.10.066}}\n")
    return buf.getvalue()


def test_08_latex_check(page, env):
    tab(page, "documents")
    page.set_input_files("#texFile", write(env, "paper.zip", _latex_zip()))
    res = page.locator("#texResult")
    expect(res).to_contain_text("Cited but not in the .bib", timeout=20000)
    expect(res).to_contain_text("missing2020")
    expect(res).to_contain_text("The same reference under different keys")
    shot(page, "latex-check")


def test_09_keep_bib_updated(page, env):
    folder = os.path.join(env["_TMP"], "overleaf")
    os.makedirs(folder)
    page.fill("#bxProject", "Thesis")
    page.fill("#bxPath", folder)
    page.click("#bxAdd")
    expect(page.locator("#bxList")).to_contain_text("references.bib")
    text = open(os.path.join(folder, "references.bib"), encoding="utf-8").read()
    assert "@article{topham2017," in text


def test_10_browser_button_popup(page, app):
    data = {"url": "https://example.org/paper", "title": "Blade recycling routes for wind turbines",
            "meta": {"citation_title": ["Blade recycling routes for wind turbines"], "citation_author": ["Lee, Kim"],
                     "citation_journal_title": ["Waste Management"], "citation_publication_date": ["2020"]}, "sel": ""}
    pop = page.context.new_page()
    pop.goto(URL + "clip#" + urllib.request.quote(json.dumps(data)))
    expect(pop.locator("#title")).to_have_text("Blade recycling routes for wind turbines", timeout=30000)
    pop.fill("#project", "Thesis")
    pop.screenshot(path=os.path.join(SHOTS, "10-clip-popup.png"))
    pop.click("#add")
    expect(pop.locator("body")).to_contain_text("Lee2020", timeout=20000)
    pop.close()
    tab(page, "library")
    expect(page.locator('#libRows .ref[data-sel="Lee2020"]')).to_be_visible()


def test_11_settings_codex_and_claude(page, env):
    tab(page, "settings")
    expect(page.locator("#claudeStatus")).not_to_be_empty()
    page.click("#codexConnect")
    expect(page.locator("#codexStatus")).to_contain_text("Connected", timeout=20000)
    cfg = open(os.path.join(env["CODEX_HOME"], "config.toml"), encoding="utf-8").read()
    assert "[mcp_servers.citrello]" in cfg
    shot(page, "settings")


def test_12_connector_answers(env, app):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
             "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_references",
             "arguments": {"query": "decommissioning"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "create_word_document",
             "arguments": {"text": "# Intro\nWind [@topham2017].", "title": "E2E intro", "style": "apa"}}}]
    out = subprocess.run(_cmd("CITRELLO_MCP_CMD", "mcp_server.py"), input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, env=env, timeout=120).stdout
    replies = {r["id"]: r for r in map(json.loads, filter(None, out.splitlines()))}
    assert replies[1]["result"]["serverInfo"]["name"] == "citrello"
    assert "topham2017" in replies[2]["result"]["content"][0]["text"]
    made = json.loads(replies[3]["result"]["content"][0]["text"])
    assert os.path.exists(made["saved_to"]) and made["citations"] == 1


@online
def test_20_add_by_doi_from_search_box(page):
    tab(page, "library")
    page.fill("#q", "10.1016/j.enpol.2015.07.022")
    page.press("#q", "Enter")
    expect(page.locator("#qAddResult .notice")).to_be_visible(timeout=60000)
    expect(page.locator("#qAddResult")).to_contain_text("Added")
    shot(page, "added-by-doi")


@online
def test_21_add_typed_reference(page):
    page.click("#openAdd")
    page.fill("#addText", "Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                          "Renewable Energy, 102, pp.470-480.")
    page.click("#addGo")
    expect(page.locator("#addResults")).to_contain_text("Already in your library", timeout=60000)
    shot(page, "typed-reference-duplicate")


@online
def test_22_verify_online(page):
    page.click("#verifyBtn")
    expect(page.locator("#verifyMsg")).to_contain_text("checked", timeout=180000)
    expect(page.locator('#libRows .ref.verified[data-sel="topham2017"]')).to_be_visible()
    shot(page, "verified")


@online
def test_23_convert_typed_citations(page, env):
    import docx
    d = docx.Document()
    d.add_paragraph("Offshore wind decommissioning is costly [1] and needs planning [2].")
    d.add_heading("References", level=1)
    d.add_paragraph("[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an offshore wind farm. "
                    "Renewable Energy, 102, pp.470-480. doi:10.1016/j.renene.2016.10.066")
    d.add_paragraph("[2] Vetters, J., Thomassen, G. and Van Passel, S. (2024) 'Sailing through end-of-life challenges: "
                    "A comprehensive review for offshore wind', Renewable and Sustainable Energy Reviews, 199, 114486.")
    path = os.path.join(env["_TMP"], "typed.docx")
    d.save(path)
    tab(page, "documents")
    page.set_input_files("#file", path)
    expect(page.locator("#go")).to_be_visible(timeout=120000)
    shot(page, "convert-review")
    page.click("#go")
    expect(page.locator("#doneMsg")).to_be_visible(timeout=120000)
    shot(page, "convert-done")
    links = page.locator("#downloads a")
    assert links.count() >= 1


def test_99_no_page_errors(page):
    assert not page.errors, page.errors
