"""The web app's request handler (webapp.py), run here in ordinary Python instead of the browser.

In the browser the same code runs in Pyodide; tests/test_web_app.py drives that end to end in Chromium and WebKit.
"""
import json
import os

import pytest

import library


@pytest.fixture
def web(monkeypatch, tmp_path):
    import app
    import webapp
    monkeypatch.setattr(app, "USER_CONFIG", str(tmp_path / "app.json"))
    monkeypatch.setattr(webapp, "BACKUP_FILE", str(tmp_path / "web.json"))
    saved = library.load()  # the tests share one throw-away library: put it back afterwards
    yield webapp
    with library.editing() as data:
        data.clear()
        data.update(saved)


def call(webapp, method, path, body=None, headers=None):
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    status, ctype, hdrs, out = webapp.handle(method, path, headers or {}, body)
    return status, (json.loads(out) if ctype == "application/json" and out else out), hdrs


BIB = b"""@article{Topham2017,
  author = {Topham, Eva and McMillan, David},
  title = {Sustainable decommissioning of an offshore wind farm},
  journal = {Renewable Energy}, year = {2017}, volume = {102}, pages = {470--480},
  doi = {10.1016/j.renene.2016.10.066}}
"""


def test_status_says_web_and_hides_desktop_parts(web):
    st, out, _ = call(web, "GET", "/api/status")
    assert st == 200 and out["web"] is True and out["usage_stats"] is False
    assert call(web, "GET", "/api/update")[1]["available"] is False
    for path in ("/api/claude/connect", "/api/word/install", "/api/library/import-zotero-local", "/api/quit",
                 "/api/bibexports/add", "/api/update/install"):
        st, out, _ = call(web, "POST", path, {})
        assert st == 400 and "Citrello for desktop" in out["error"], path


def test_import_search_and_export_through_the_handler(web):
    with library.editing() as data:
        data.update(items=[], projects=[])
    st, out, _ = call(web, "POST", "/api/library/import", BIB, {"X-Filename": "refs.bib", "X-Project": "Thesis"})
    assert st == 200 and out["added"] == 1
    st, out, _ = call(web, "GET", "/api/library?q=decommissioning&project=Thesis")
    assert [it["key"] for it in out["items"]] == ["Topham2017"]
    st, out, hdrs = call(web, "GET", "/api/library/export?fmt=bib&project=Thesis")
    assert st == 200 and b"Topham2017" in out and "citrello_Thesis.bib" in hdrs["Content-Disposition"]
    st, out, _ = call(web, "GET", "/api/library/item?key=Topham2017")
    assert "Topham" in out["formatted"]["reference"]


def test_backup_and_restore(web):
    with library.editing() as data:
        data.update(items=[], projects=[])
    call(web, "POST", "/api/library/import", BIB, {"X-Filename": "refs.bib", "X-Project": "Thesis"})
    assert call(web, "GET", "/api/web/state")[1]["last_backup"] is None
    st, backup, hdrs = call(web, "GET", "/api/web/backup")
    assert st == 200 and hdrs["Content-Disposition"].startswith("attachment; filename=citrello-library-")
    assert call(web, "GET", "/api/web/state")[1]["backup_count"] == 1
    call(web, "POST", "/api/library/delete", {"keys": ["Topham2017"]})
    assert library.load()["items"] == []
    st, out, _ = call(web, "POST", "/api/web/restore", backup)
    assert st == 200 and out["items"] == 1
    data = library.load()
    assert data["items"][0]["key"] == "Topham2017" and data["items"][0]["projects"] == ["Thesis"]
    assert "Thesis" in data["projects"]


@pytest.mark.parametrize("bad", [b"not json", b"[]", b'{"items": [{"title": "x"}]}', BIB])
def test_restore_refuses_other_files(web, bad):
    before = library.load()
    st, out, _ = call(web, "POST", "/api/web/restore", bad)
    assert st == 400 and "isn't a Citrello backup" in out["error"]
    assert library.load() == before


def test_an_error_in_the_handler_becomes_an_answer(web, monkeypatch):
    import app
    monkeypatch.setattr(app.library, "search", lambda *a, **k: 1 / 0)
    st, out, _ = call(web, "GET", "/api/library")
    assert st == 500 and "division by zero" in out["error"]


def test_jobs_run_to_the_end_without_threads(web, monkeypatch):
    """In the browser, starting a job finishes it before the request returns (no threads in Pyodide)."""
    import threading
    monkeypatch.setattr(threading, "Thread", web._InlineThread)
    with library.editing() as data:
        data.update(items=[], projects=[])
    call(web, "POST", "/api/library/import", BIB, {"X-Filename": "refs.bib"})
    st, out, _ = call(web, "POST", "/api/library/verify", {"q": "", "project": "*"})
    assert st == 200
    st, state, _ = call(web, "GET", f"/api/verify/{out['id']}")
    assert state["done"] is True  # the lookup itself fails offline; what matters is that it already ran


def test_built_page_is_the_desktop_page_plus_web_js(tmp_path):
    """The build makes /app from ui.html without changing it, apart from loading web.js."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui = open(os.path.join(root, "ui.html"), encoding="utf-8").read()
    assert 'class="card desktop-only" id="latexCard"' in ui and 'id="webBackup"' in ui
    assert "html.web .desktop-only" in ui
    web_js = open(os.path.join(root, "site", "public", "app", "web.js"), encoding="utf-8").read()
    assert 'classList.add("web")' in web_js


def test_jobs_report_progress_while_they_run(web, monkeypatch, tmp_path):
    """In the browser a job runs after its request has been answered; meanwhile its progress goes to the page,
    which answers its own progress polls with it, until the job says it's finished ("")."""
    import sys
    import threading
    import doccheck
    sys.path.insert(0, os.path.dirname(__file__))
    import test_doccheck as T
    import docx
    monkeypatch.setattr(threading, "Thread", web._LaterThread)
    monkeypatch.setattr(doccheck, "check", web._with_progress(doccheck.check))
    sent = []
    monkeypatch.setattr(web, "PUBLISH", lambda path, body: sent.append((path, json.loads(body) if body else None)))
    monkeypatch.setattr(web, "_last_publish", 0.0)
    monkeypatch.setattr(web, "PROGRESS_EVERY", 0)
    d = docx.Document()
    T.add_field(d.add_paragraph("Costly "), T.csl_field([T.TOPHAM, T.VETTERS], zotero=True))
    path = tmp_path / "paper.docx"
    d.save(path)
    st, out, _ = call(web, "POST", "/api/doccheck", path.read_bytes(), {"X-Filename": "paper.docx"})
    assert st == 200 and web.has_pending()
    poll = "/api/doccheck/" + out["id"]
    assert sent and sent[0][0] == poll  # the first progress went out with the answer
    assert call(web, "GET", poll)[1]["state"] == "checking"  # not run yet: the answer went back first
    web.run_pending()
    assert not web.has_pending()
    steps = [body for p, body in sent if p == poll]
    assert steps[0]["state"] == "checking" and len(steps) >= 3  # the start, and steps while it ran
    assert steps[-1] is None  # finished: the page asks Python again
    final = call(web, "GET", poll)[1]
    assert final["state"] == "done" and final["report"]["references"] == 2
