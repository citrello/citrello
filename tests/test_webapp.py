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


@pytest.fixture
def web_conn(web, monkeypatch, tmp_path):
    """The web app's stand-in for the desktop's bridge connection (the WebSocket itself is in worker.js)."""
    import bridge_client
    monkeypatch.setattr(bridge_client, "ACCOUNT_FILE", str(tmp_path / "account.json"))
    monkeypatch.setattr(bridge_client, "CONNECTION", bridge_client.CONNECTION)
    monkeypatch.setattr(bridge_client, "platform", bridge_client.platform)
    conn = web.WebConnection()
    monkeypatch.setattr(web, "CONNECTION", conn)
    web.use_web_connection("Safari on Mac (web app)")
    return conn


def test_signing_in_tells_the_worker_to_connect(web, web_conn, monkeypatch):
    import bridge_client
    sent = {}

    def fake_api(method, path, body=None, token=None, timeout=25):
        sent[path] = body
        if path == "/api/auth/verify":
            return {"token": "cdt_secret", "device_id": "d_1", "user": {"id": "u1", "email": "a@b.org", "newsletter": False}}
        if path == "/api/me":
            return {"user": {"email": "a@b.org", "newsletter": False}, "devices": [], "connector_url": "x/mcp"}
        return {"ok": True}
    monkeypatch.setattr(bridge_client, "api", fake_api)
    plan = json.loads(web_conn.plan())
    assert plan["token"] is None and plan["url"].endswith("/device/connect")
    st, out, _ = call(web, "POST", "/api/account/verify", {"email": "a@b.org", "code": "123456"})
    assert st == 200 and out["signed_in"] and out["web"]
    # the server is told this is a browser, not "emscripten"
    assert sent["/api/auth/verify"]["device_name"] == "Safari on Mac (web app)"
    assert sent["/api/auth/verify"]["platform"] == "Web app"
    new = json.loads(web_conn.plan())
    assert new["token"] == "cdt_secret" and new["generation"] > plan["generation"]
    web_conn.set_state("online")
    st, out, _ = call(web, "GET", "/api/account")
    assert out["state"] == "online" and out["email"] == "a@b.org"
    assert call(web, "GET", "/api/status")[1]["signed_in"] is True
    st, out, _ = call(web, "POST", "/api/account/signout", {})
    assert st == 200 and not out["signed_in"]
    assert json.loads(web_conn.plan())["token"] is None


def test_the_tab_answers_the_connector_like_the_desktop(web, web_conn):
    hello = json.loads(web.hello())
    assert hello["type"] == "hello" and any(t["name"] == "search_references" for t in hello["tools"])
    with library.editing() as data:
        data.update(items=[], projects=[])
    call(web, "POST", "/api/library/import", BIB, {"X-Filename": "refs.bib"})
    frame = {"type": "rpc", "id": "r1", "msg": {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                                 "params": {"name": "search_references", "arguments": {"query": "offshore"}}}}
    reply = json.loads(web.answer_rpc(json.dumps(frame)))
    assert reply["type"] == "reply" and reply["id"] == "r1" and reply["msg"]["id"] == 7
    assert "Topham2017" in reply["msg"]["result"]["content"][0]["text"]
    # the same safeguard as on the desktop: a web request can't write outside Citrello's Documents folder
    frame["msg"]["params"] = {"name": "create_word_document", "arguments": {
        "text": "Costs [@Topham2017].", "output_path": "/tmp/elsewhere.docx"}}
    reply = json.loads(web.answer_rpc(json.dumps(frame)))
    assert reply["msg"]["result"]["isError"] and "Documents" in reply["msg"]["result"]["content"][0]["text"]
    frame["msg"]["params"] = {"name": "create_word_document", "arguments": {"text": "Costs [@Topham2017].", "title": "Draft"}}
    reply = json.loads(web.answer_rpc(json.dumps(frame)))
    assert not reply["msg"]["result"].get("isError"), reply
    docs = call(web, "GET", "/api/web/documents")[1]["documents"]
    assert docs[0]["name"] == "Draft.docx"  # newest first (other tests save files there too)
    st, data, hdrs = call(web, "GET", "/api/web/documents/Draft.docx")
    assert st == 200 and data[:2] == b"PK" and "Draft.docx" in hdrs["Content-Disposition"]
    assert call(web, "GET", "/api/web/documents/..%2Flibrary.json")[0] == 404
    assert web.answer_rpc("not json") == ""


def test_import_zotero_file_in_the_browser(web, tmp_path):
    """"From Zotero on this computer" in the web app: the person picks zotero.sqlite."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import test_zotero_local as Z
    with library.editing() as data:
        data.update(items=[], projects=[])
    db = tmp_path / "zotero.sqlite"
    Z.make_db(str(db))
    st, out, _ = call(web, "POST", "/api/web/import-zotero-file", db.read_bytes(), {"X-Project": "Old%20Zotero"})
    assert st == 200 and out["added"] >= 1 and out["collections"] >= 1 and "database" not in out
    data = library.load()
    assert all("Old Zotero" in it["projects"] for it in data["items"])
    st, out, _ = call(web, "POST", "/api/web/import-zotero-file", b"PK\x03\x04 not sqlite", {})
    assert st == 400 and "zotero.sqlite" in out["error"]


def test_import_mendeley_with_a_token_from_the_browser(web, monkeypatch, tmp_path):
    """"Import my Mendeley library" in the web app: the page passes Mendeley's sign-in token; nothing is kept."""
    import sys
    import citrello_core as core
    sys.path.insert(0, os.path.dirname(__file__))
    import mendeley_mock
    base, srv = mendeley_mock.start()
    monkeypatch.setattr(core, "API", base)
    monkeypatch.setattr(core, "TOKEN_FILE", str(tmp_path / "mendeley.json"))  # never a real login file
    try:
        with library.editing() as data:
            data.update(items=[], projects=[])
        st, out, _ = call(web, "POST", "/api/web/import-mendeley", {"token": mendeley_mock.TOKEN, "project": "Mendeley"})
        assert st == 200 and out == {"added": 2, "skipped": 0}  # both pages
        assert all(it["projects"] == ["Mendeley"] for it in library.load()["items"])
        assert not os.path.exists(core.TOKEN_FILE)  # the token isn't kept
        st, out, _ = call(web, "POST", "/api/web/import-mendeley", {"token": "expired"})
        assert st == 400 and "expired" in out["error"] and not os.path.exists(core.TOKEN_FILE)
        assert call(web, "POST", "/api/web/import-mendeley", {})[0] == 400
    finally:
        srv.shutdown()


def test_cloud_copy_bundle_and_restore(web):
    """The cloud copy: the library and the papers' text in one gzip file; restoring it replaces both."""
    import fulltext
    import gzip
    with library.editing() as data:
        data.update(items=[], projects=[])
    call(web, "POST", "/api/library/import", BIB, {"X-Filename": "refs.bib", "X-Project": "Thesis"})
    fulltext.attach("Topham2017", "Cable removal was the largest cost. " * 100, "pdf")
    st1 = call(web, "GET", "/api/web/state")[1]
    assert st1["stamp"] and st1["cloud_device"].startswith("browser-")
    assert call(web, "GET", "/api/web/state")[1]["cloud_device"] == st1["cloud_device"]  # the same browser every time
    st, body, hdrs = call(web, "GET", "/api/web/cloud-bundle")
    assert st == 200 and body[:2] == b"\x1f\x8b" and hdrs["X-Items"] == "1" and int(hdrs["X-Texts"]) >= 1
    bundle = json.loads(gzip.decompress(body))
    assert bundle["library"]["items"][0]["key"] == "Topham2017"
    assert any("largest cost" in t for t in bundle["fulltext"].values())
    assert call(web, "POST", "/api/web/cloud-saved", {"stamp": hdrs["X-Stamp"]})[0] == 200
    assert call(web, "GET", "/api/web/state")[1]["cloud_stamp"] == st1["stamp"]  # nothing changed since
    # another browser: empty library, the cloud copy comes back with the paper's text
    call(web, "POST", "/api/library/delete", {"keys": ["Topham2017"]})
    assert library.load()["items"] == []
    st, out, _ = call(web, "POST", "/api/web/cloud-restore", body)
    assert st == 200 and out["items"] == 1
    it = library.get(library.load(), "Topham2017")
    assert it and "largest cost" in fulltext.read(it)
    assert call(web, "POST", "/api/web/cloud-restore", b"not gzip")[0] == 400
