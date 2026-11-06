"""The in-browser checker on citrello.app/check, end to end in a real browser (needs internet for Pyodide).

  python packaging/build_web_check.py
  (cd site/public && python -m http.server 8099) &
  CITRELLO_WEB_CHECK=http://localhost:8099 pytest tests/test_web_check.py
"""
import os
import sys
import zipfile

import pytest

BASE = os.environ.get("CITRELLO_WEB_CHECK")
pytestmark = pytest.mark.skipif(not BASE, reason="needs the site served locally and internet (set CITRELLO_WEB_CHECK)")


def test_check_and_fix_in_the_browser(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    import docx
    d = docx.Document()
    p = d.add_paragraph("Decommissioning is costly ")
    T.add_field(p, T.csl_field([T.TOPHAM], zotero=True))
    p = d.add_paragraph("and reviewed ")
    T.add_field(p, T.csl_field([T.VETTERS, dict(T.TOPHAM, id="t2")], zotero=True))
    path = str(tmp_path / "paper.docx")
    d.save(path)
    pdf = str(tmp_path / "paper.pdf")
    T.make_pdf(pdf, [["Costs are underestimated [1] and planning matters [4]."],
                     ["References", "[1] Topham, E. and McMillan, D. (2017). Sustainable decommissioning of an",
                      "offshore wind farm. Renewable Energy, 102, pp.470-480.",
                      "[2] Burton, T. and Jenkins, N. (2011). Wind Energy Handbook. Wiley, Chichester."]])
    shots = os.environ.get("CITRELLO_E2E_SHOTS") or str(tmp_path)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE.rstrip("/") + "/check.html")
        page.set_input_files("#file", path)
        res = page.locator("#result")
        expect(res).to_contain_text("listed more than once", timeout=300000)
        expect(res).to_contain_text("Zotero")
        page.screenshot(path=os.path.join(shots, "web-check-report.png"), full_page=True)
        page.select_option("#style", "ieee")
        with page.expect_download(timeout=180000) as dl:
            page.click("#fix")
        with zipfile.ZipFile(dl.value.path()) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        assert "ZOTERO_ITEM" not in xml and "ADDIN CSL_CITATION" in xml and "CSL_BIBLIOGRAPHY" in xml
        expect(page.locator("#fixResult")).to_contain_text("Duplicates merged")
        page.screenshot(path=os.path.join(shots, "web-check-fixed.png"), full_page=True)
        page.set_input_files("#file", pdf)
        expect(res).to_contain_text("Cited but not in the reference list: [4]", timeout=180000)
        assert not errors, errors
        b.close()
