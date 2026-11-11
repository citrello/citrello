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
             CITRELLO_APP_CONFIG=os.path.join(tmp, "app.json"), CITRELLO_INSTALL_FILE=os.path.join(tmp, "install.json"),
             CITRELLO_ZOTERO_DIR=os.path.join(tmp, "Zotero"))
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
        exe = os.environ.get("CITRELLO_E2E_CHROMIUM")  # a Chromium that isn't Playwright's own
        browser = p.chromium.launch(**({"executable_path": exe, "args": ["--no-sandbox"]} if exe else {}))
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


def test_00b_home_and_a_guided_check_of_the_sample_paper(page):
    """A new library opens on Home: what Citrello can do, each with a guided tour on the real screen."""
    expect(page.locator("#tab-home")).to_be_visible()
    expect(page.locator(".home-tile")).to_have_count(9)
    shot(page, "home")
    page.click('.home-tile[data-tour="check"]')
    tip = page.locator("#tourTip")
    expect(tip.locator("h3")).to_have_text("Choose your paper")
    expect(page.locator("#tab-documents")).to_be_visible()  # the tour went to the right screen
    expect(page.locator("#tourSpot")).to_be_visible()
    shot(page, "tour-choose")
    tip.locator("button", has_text="Use the sample paper").click()
    expect(tip.locator("h3")).to_have_text("The summary", timeout=60000)  # moved on by itself when the check finished
    expect(page.locator("#chkCites")).to_contain_text("Citation [7] points to a reference that isn't in the list")
    expect(page.locator("#chkCites")).to_contain_text("[1] = [5]")
    expect(page.locator("#chkCites")).to_contain_text("never cited: [6]")
    page.click("#ttAll")
    expect(tip.locator("ol.tt-steps li")).to_have_count(5)
    expect(tip.locator("ol.tt-steps li.done")).to_have_count(2)
    shot(page, "tour-summary")
    page.click("#ttNext")
    expect(tip.locator("h3")).to_have_text("What's wrong, reference by reference")
    page.click("#ttNext")
    expect(tip.locator("h3")).to_have_text("Fix it in one click")
    page.click("#ttNext")  # Done
    expect(tip).to_have_count(0)
    tab(page, "home")
    expect(page.locator('.home-tile[data-tour="check"] .ht-go')).to_have_text("Show me again →")  # remembered
    # people who know the way turn the guides off: a tile then goes straight to its screen
    page.uncheck("#guidesOn")
    expect(page.locator('.home-tile[data-tour="cites"] .ht-go')).to_have_text("Go there →")
    page.click('.home-tile[data-tour="sources"]')
    expect(page.locator("#tab-documents")).to_be_visible()
    expect(page.locator("#chkDrop")).to_have_class(re.compile("flash"))
    expect(tip).to_have_count(0)
    page.reload()
    tab(page, "home")
    expect(page.locator("#guidesOn")).not_to_be_checked()  # kept
    page.check("#guidesOn")
    # a tour left halfway is offered again on Home
    tab(page, "home")
    page.click('.home-tile[data-tour="library"]')
    expect(tip.locator("h3")).to_have_text("Bring your library")
    page.reload()
    expect(page.locator("#tourResume")).to_contain_text("Tidy up my library", timeout=15000)
    page.click("#tourResumeGo")
    expect(tip.locator("h3")).to_have_text("Bring your library")
    page.click("#ttClose")
    expect(tip).to_have_count(0)
    tab(page, "home")
    expect(page.locator("#tourResume")).to_be_hidden()
    assert not page.errors, page.errors


def test_01_starts_with_an_empty_library(page):
    tab(page, "library")
    expect(page.locator("#libEmpty")).to_be_visible()
    expect(page.locator("#ver")).to_contain_text("v")
    shot(page, "empty-library")
    assert not page.errors


def test_02_import_bibtex_keeps_keys(page, env):
    page.click("#openImport")
    page.fill("#importProject", "Thesis")
    page.click('[data-guide="imp-file"]')  # the Import tiles open a window
    page.set_input_files("#importFile", write(env, "refs.bib", BIB))
    expect(page.locator("#gResult")).to_contain_text("3 added", timeout=20000)
    expect(page.locator("#importMsg")).to_contain_text("3 added")
    page.click("#gNext")  # Close
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


def test_04b_filters_and_selecting_many(page):
    tab(page, "library")
    page.click('#projNav [data-proj-nav="*"]')
    chips = page.locator("#libChips [data-rel]")
    expect(chips.first).to_contain_text("3 references")
    check = page.locator('#libChips [data-rel="check"]')
    expect(check).to_be_visible()
    check.click()                                   # show only "to check"
    expect(check).to_have_attribute("aria-pressed", "true")
    n_check = page.locator("#libRows .ref").count()
    assert 0 < n_check < 3
    page.locator('#libChips [data-rel="all"]').click()
    expect(page.locator("#libRows .ref")).to_have_count(3)
    # tick two references (the second with Shift: a range), add them to a project
    page.locator('#libRows .ref[data-sel="topham2017"] .pick').click()
    page.locator('#libRows .ref[data-sel="burton2011"] .pick').click(modifiers=["Shift"])
    expect(page.locator("#selCount")).to_have_text("3 selected")   # the range covers the one in between
    page.locator('#libRows .ref[data-sel="vetters2024"] .pick').click()
    expect(page.locator("#selCount")).to_have_text("2 selected")
    page.fill("#selProject", "Review")
    page.click("#selAdd")
    expect(page.locator('#projNav [data-proj-nav="Review"]')).to_be_visible()
    shot(page, "selected-added-to-project")
    page.click('#projNav [data-proj-nav="Review"]')
    expect(page.locator("#libRows .ref")).to_have_count(2)
    # inside a project: move one out of it into another
    page.locator('#libRows .ref[data-sel="burton2011"] .pick').click()
    page.fill("#selProject", "Books")
    page.click("#selMove")
    expect(page.locator("#libRows .ref")).to_have_count(1)
    page.click('#projNav [data-proj-nav="Books"]')
    expect(page.locator('#libRows .ref[data-sel="burton2011"]')).to_be_visible()
    page.click('#projNav [data-proj-nav="*"]')
    expect(page.locator("#libRows .ref")).to_have_count(3)   # wait for the list to switch back
    # select all, then clear
    page.check("#selAll")
    expect(page.locator("#selCount")).to_have_text("3 selected")
    page.click("#selClear")
    expect(page.locator("#selBar")).to_be_hidden()


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


def _fix_and_download(page, style):
    page.select_option("#chkStyle", style)
    page.click("#chkFix")
    link = page.locator("#chkResult a.btn.primary")
    expect(link).to_be_visible(timeout=120000)
    with page.expect_download() as dl:
        link.click()
    with zipfile.ZipFile(dl.value.path()) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_07_markers_from_claude_become_live(page, env):
    import docx
    d = docx.Document()
    d.add_paragraph("Decommissioning is costly [@topham2017] and reviewed elsewhere [@vetters2024; @burton2011].")
    path = os.path.join(env["_TMP"], "draft.docx")
    d.save(path)
    tab(page, "documents")
    page.set_input_files("#chkFile", path)
    expect(page.locator("#chkResult")).to_contain_text("[@Key] markers", timeout=120000)
    xml = _fix_and_download(page, "ieee")
    shot(page, "markers-fixed")
    assert "ADDIN CSL_CITATION" in xml and "CSL_BIBLIOGRAPHY" in xml
    assert "[@topham2017]" not in xml


def test_07b_check_a_document(page, env):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dfix", os.path.join(ROOT, "tests", "test_doccheck.py"))
    dfix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dfix)
    import docx
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    dfix.add_field(p, dfix.csl_field([dfix.TOPHAM], zotero=True))
    p = d.add_paragraph("and reviewed ")
    dfix.add_field(p, dfix.csl_field([dfix.VETTERS, dict(dfix.TOPHAM, id="t2")], zotero=True))
    path = os.path.join(env["_TMP"], "zotero-paper.docx")
    d.save(path)
    tab(page, "documents")
    page.set_input_files("#chkFile", path)
    res = page.locator("#chkResult")
    expect(res).to_contain_text("Zotero", timeout=120000)
    expect(res).to_contain_text("listed more than once")
    shot(page, "document-check")
    with page.expect_download() as dl:
        page.click('#chkResult a.btn')
    assert "Sustainable decommissioning" in open(dl.value.path(), encoding="utf-8-sig").read()
    # fix it: live citations, the duplicate merged, the list rebuilt in IEEE
    page.select_option("#chkStyle", "ieee")
    page.click("#chkFix")
    expect(res).to_contain_text("Duplicates merged", timeout=60000)
    shot(page, "document-fixed")
    with page.expect_download() as dl:
        page.click('#chkResult a.btn.primary')
    with zipfile.ZipFile(dl.value.path()) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "ZOTERO_ITEM" not in xml and "ADDIN CSL_CITATION" in xml and "CSL_BIBLIOGRAPHY" in xml


def test_07b2_what_my_sources_say(page, env):
    """Each cited sentence next to the passages of the paper's full text that match it (no AI, on the computer)."""
    import docx
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from test_sources import PAPER
    r = page.evaluate("""async (text) => (await fetch('/api/clip/fulltext', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Key': 'topham2017'}, body: JSON.stringify({text})})).status""", PAPER)
    assert r == 200
    d = docx.Document()
    d.add_paragraph("Removing the export cables is the largest part of the decommissioning cost [@topham2017]. "
                    "Reviews compare the end-of-life options [@vetters2024].")
    path = os.path.join(env["_TMP"], "claims.docx")
    d.save(path)
    tab(page, "documents")
    page.set_input_files("#chkFile", path)
    page.click("#chkSources", timeout=60000)
    expect(page.locator("#chkSrc")).to_contain_text("42% of the total decommissioning cost", timeout=30000)
    expect(page.locator("#chkSrc mark").first).to_be_visible()  # the shared words are highlighted
    expect(page.locator("#chkSrc")).to_contain_text("No text for this paper yet")  # Vetters has no text
    expect(page.locator("#chkSrcChips")).to_contain_text("2 cited sentences")
    # the paper without text can be fixed right here: its PDF, then the sentence fills in by itself
    expect(page.locator("#srcGet")).to_contain_text("1 cited paper has no text yet")
    page.locator("#chkSrcPanel").scroll_into_view_if_needed()
    shot(page, "sources")
    from test_doccheck import make_pdf
    pdf = os.path.join(env["_TMP"], "vetters.pdf")
    make_pdf(pdf, [["Sailing through end-of-life challenges: A comprehensive review for offshore wind", "Jan Vetters"] +
                   [f"Part {i}. Reviews compare the end-of-life options for offshore wind farms: repowering, life "
                    f"extension and full removal, each with different costs." for i in range(14)]])
    page.set_input_files("#srcPdfs", pdf)
    expect(page.locator("#chkSrc")).to_contain_text("repowering, life extension and full removal", timeout=30000)
    expect(page.locator("#srcGet")).to_have_count(0)  # nothing missing any more
    expect(page.locator("#srcFetchMsg")).to_contain_text("Text added for 1 paper")
    # a long paper isn't one wall of text: filters, and 20 sentences at a time
    d = docx.Document()
    d.add_paragraph(" ".join(f"Point {i}: the export cables dominate the decommissioning cost [@topham2017]." for i in range(22)) +
                    " Something nobody has written about [@burton2011].")
    long_path = os.path.join(env["_TMP"], "long.docx")
    d.save(long_path)
    page.set_input_files("#chkFile", long_path)
    page.click("#chkSources", timeout=60000)
    expect(page.locator("#chkSrc .src")).to_have_count(20, timeout=30000)
    page.click("#srcMore")
    expect(page.locator("#chkSrc .src")).to_have_count(23)
    page.click('[data-src-filter="no_text"]')
    expect(page.locator("#chkSrc .src")).to_have_count(1)
    expect(page.locator("#chkSrc")).to_contain_text("Something nobody has written about")


def test_07b3_a_document_without_references_offers_a_way_forward(page, env):
    import docx
    d = docx.Document()
    d.add_paragraph("Just some notes, no references at all.")
    path = os.path.join(env["_TMP"], "notes.docx")
    d.save(path)
    tab(page, "documents")
    page.set_input_files("#chkFile", path)
    expect(page.locator("#chkResult")).to_contain_text("No references found", timeout=30000)
    expect(page.locator("#chkTrySample")).to_be_visible()


def test_07b4_sources_add_the_references_first(page):
    """References not in the library: one click adds them (in a project named after the document) and shows the
    sentences; papers without text then offer ways to get it."""
    tab(page, "documents")
    page.evaluate("useSample()")
    expect(page.locator('#chkSources[data-add="1"]')).to_contain_text("Add the", timeout=60000)
    page.click("#chkSources")
    expect(page.locator("#chkSrcChips")).to_contain_text("cited sentences", timeout=30000)
    expect(page.locator("#chkResult")).to_contain_text("added to your library")
    expect(page.locator("#srcGet")).to_contain_text("have no text yet")
    page.click('#chkResult [data-open-project]')  # "See them": the document's project in the library
    expect(page.locator("#libTitle")).to_have_text("Citrello sample paper")
    page.click('#projNav [data-proj-nav="*"]')  # back to all references for the next tests


def test_07c_check_a_pdf(page, env):
    import importlib.util
    spec = importlib.util.spec_from_file_location("dfix", os.path.join(ROOT, "tests", "test_doccheck.py"))
    dfix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dfix)
    path = os.path.join(env["_TMP"], "paper.pdf")
    dfix.make_pdf(path, [["Costs are underestimated [1] and planning matters [4]."],
                         ["References", "[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an",
                          "offshore wind farm. Renewable Energy, 102, pp.470-480.",
                          "[2] Burton, T. and Jenkins, N. (2011). Wind Energy Handbook. Wiley, Chichester."]])
    tab(page, "documents")
    page.set_input_files("#chkFile", path)
    res = page.locator("#chkResult")
    expect(res).to_contain_text("2 references · PDF", timeout=120000)
    expect(res).to_contain_text("Cited but not in the reference list: [4]")
    expect(res).to_contain_text("A PDF can't be changed")
    shot(page, "pdf-check")


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
    page.set_input_files("#chkFile", write(env, "paper.zip", _latex_zip()))   # the same drop zone
    res = page.locator("#texResult")
    expect(res).to_contain_text("Cited but not in the .bib", timeout=20000)
    expect(res).to_contain_text("missing2020")
    expect(res).to_contain_text("The same reference under different keys")
    shot(page, "latex-check")


def test_08b_arxiv_ready_zip(page, env):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("paper/main.tex", "\\documentclass{article}\\usepackage{natbib}\n% private note\n\\begin{document}\n"
                                     "Costs \\citep{topham2017}.\n\\bibliographystyle{plainnat}\\bibliography{refs}\\end{document}")
        z.writestr("paper/refs.bib", BIB)
        z.writestr("paper/main.aux", "leftover")
    page.set_input_files("#chkFile", write(env, "arxiv-paper.zip", buf.getvalue()))
    expect(page.locator("#axGo")).to_be_visible(timeout=20000)
    page.click("#axGo")
    res = page.locator("#axResult")
    expect(res).to_contain_text("Ready for arXiv", timeout=20000)
    expect(res).to_contain_text("Added main.bbl")
    shot(page, "arxiv-ready")
    with page.expect_download() as dl:
        page.click("#axResult a.btn")
    with zipfile.ZipFile(dl.value.path()) as z:
        names = set(z.namelist())
        assert names == {"main.tex", "main.bbl"}, names
        assert "private note" not in z.read("main.tex").decode()
        assert "\\bibitem[Topham and McMillan(2017)]{topham2017}" in z.read("main.bbl").decode()


def test_08c_find_a_journal_style(page):
    tab(page, "settings")
    page.fill("#styleFind", "nature")
    first = page.locator("#styleFindList li").first
    expect(first).to_contain_text("Nature", timeout=10000)
    expect(page.locator("#styleFindList")).to_contain_text("Nature Methods")  # from the full catalogue
    shot(page, "style-search")
    first.locator("button").click()
    expect(page.locator("#defStyle")).to_have_value("nature", timeout=10000)
    page.fill("#styleFind", "oscola")
    expect(page.locator("#styleFindList")).to_contain_text("not supported yet", timeout=10000)
    page.fill("#styleFind", "")
    tab(page, "documents")  # where the next scenario continues


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


def test_11b_import_zotero_on_this_computer(page, env):
    import importlib.util
    spec = importlib.util.spec_from_file_location("zfix", os.path.join(ROOT, "tests", "test_zotero_local.py"))
    zfix = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zfix)
    os.makedirs(env["CITRELLO_ZOTERO_DIR"], exist_ok=True)
    zfix.make_db(os.path.join(env["CITRELLO_ZOTERO_DIR"], "zotero.sqlite"))
    tab(page, "library")
    page.click("#openImport")
    page.click('[data-guide="imp-zotero"]')
    page.click("#impZoteroLocal")
    expect(page.locator("#importMsg")).to_contain_text("2 Zotero collections are now in Projects", timeout=20000)
    shot(page, "zotero-window")
    page.click("#gNext")
    expect(page.locator('#projNav [data-proj-nav="Thesis / Chapter 2"]')).to_be_visible()
    expect(page.locator('#libRows .ref[data-sel="Orsted"]').or_(page.locator("#libRows .ref", has_text="Our offshore wind farms"))).to_be_visible()
    shot(page, "zotero-imported")


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
def test_23_fix_typed_citations(page, env):
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
    page.set_input_files("#chkFile", path)
    expect(page.locator("#chkResult")).to_contain_text("typed by hand", timeout=180000)
    shot(page, "typed-checked")
    xml = _fix_and_download(page, "apa")
    shot(page, "typed-fixed")
    assert "ADDIN CSL_CITATION" in xml and "[1]" not in xml


@online
def test_24_add_a_journal_style_online(page):
    tab(page, "settings")
    page.fill("#styleFind", "Journal of Cleaner Production")
    item = page.locator("#styleFindList li").first
    expect(item).to_contain_text("Journal of Cleaner Production", timeout=10000)
    item.locator("button").click()
    expect(page.locator("#styleMsg")).to_contain_text("Using", timeout=30000)
    expect(page.locator("#defStyle")).to_have_value("journal-of-cleaner-production")
    page.select_option("#defStyle", "apa")
    tab(page, "library")


def test_98_delete_selected(page):
    tab(page, "library")
    page.click('#projNav [data-proj-nav="*"]')
    page.fill("#q", "blade recycling")
    expect(page.locator("#libRows .ref")).to_have_count(1)
    page.check("#selAll")
    page.once("dialog", lambda d: d.accept())
    page.click("#selDelete")
    expect(page.locator("#libNoMatch")).to_be_visible()
    page.fill("#q", "")


def test_99_no_page_errors(page):
    assert not page.errors, page.errors
