"""
Citrello in the browser (citrello.app/app).

The web app shows the desktop app's own interface (ui.html) and runs the desktop app's own JSON API (app.py) inside
the browser with Pyodide. The page's fetch("/api/...") calls are sent to a Web Worker, which calls handle() here
instead of going to a local server. So every feature is the same code as on the desktop, and the library stays in
the browser: $HOME is a folder the worker keeps in the browser's own storage (IndexedDB).

What differs from the desktop is in WebHandler: things that need a program on the computer (Claude Desktop,
Codex, the Word tab, updates, quitting) answer "not in the web app", and backup/restore of the whole library is
added because a browser can lose its storage (clearing site data, Safari's limits).
"""
import http.client
import json
import os
import threading
import time
import urllib.parse


class _InlineThread(threading.Thread):
    """The browser's Python can't start threads. Run background jobs straight away instead: the request that
    starts a job returns once the job is finished, and the page's next poll sees it done."""

    def start(self):
        self.run()


import app  # noqa: E402
import library  # noqa: E402

WEB_ONLY_MISSING = "This needs Citrello for desktop. In the web app, use the connector link in Settings instead."
BACKUP_FILE = os.path.join(os.path.expanduser("~"), ".citrello_web.json")  # when the last backup was made


def _web_state():
    try:
        with open(BACKUP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_web_state(**values):
    st = _web_state()
    st.update(values)
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f)


def restore(raw):
    """Replace the library with a backup (a library.json saved by "Back up the library", or by the desktop app).
    Returns how many references it holds. Refuses anything that isn't a Citrello library."""
    try:
        data = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
    except ValueError:
        raise ValueError("That file isn't a Citrello backup (library.json).")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list) or not all(
            isinstance(it, dict) and isinstance(it.get("csl"), dict) and it.get("key") for it in data["items"]):
        raise ValueError("That file isn't a Citrello backup (library.json). To add references from another "
                         "program, use Import instead.")
    with library.editing() as cur:
        cur.clear()
        cur.update(data)
        cur.setdefault("projects", [])
    return len(data["items"])


class WebHandler(app.Handler):
    """app.Handler without a socket: the request comes from the worker and the answer goes back to it."""

    def __init__(self, method, path, headers, body):  # noqa: super().__init__ would read from a socket
        self.command, self.path = method, path
        self.headers = http.client.HTTPMessage()
        for k, v in (headers or {}).items():
            self.headers[k] = v
        self._body = body or b""
        self.response = None

    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        if self.response is None:  # the first answer counts
            self.response = (code, ctype, dict(headers or {}), body)
        return True

    def body(self):
        return self._body

    def same_origin(self):
        return True  # only the page itself can post messages to its worker

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/status":
            cfg = app.load_config()
            return self.send(200, {"version": app.VERSION, "web": True, "configured": False, "connected": False,
                                   "name": None, "email": cfg.get("email", ""),
                                   "style": cfg.get("style", app.citeformat.DEFAULT_STYLE),
                                   "zotero_user": cfg.get("zotero_user", ""),
                                   "has_zotero_key": bool(cfg.get("zotero_key")),
                                   "welcome_done": cfg.get("welcome_done") == "1", "usage_stats": False,
                                   "signed_in": False})
        if path == "/api/update":
            return self.send(200, {"available": False, "current": app.VERSION, "web": True})
        if path == "/api/account":  # sign-in in the web app comes with the connector (next step)
            return self.send(200, {"signed_in": False, "web": True, "connected": False,
                                   "connector_url": app.bridge_client.BRIDGE + "/mcp"})
        if path in ("/api/claude", "/api/codex", "/api/word", "/api/bibexports"):
            return self.send(200, {"web": True, "exports": []})
        if path == "/api/web/backup":
            data = library.load()
            _save_web_state(last_backup=time.time(), backup_count=len(data["items"]))
            name = "citrello-library-" + time.strftime("%Y-%m-%d") + ".json"
            return self.send(200, json.dumps(data, ensure_ascii=False, indent=1), "application/json",
                             {"Content-Disposition": "attachment; filename=" + name})
        if path == "/api/web/state":
            data = library.load()
            st = _web_state()
            return self.send(200, {"items": len(data["items"]), "last_backup": st.get("last_backup"),
                                   "backup_count": st.get("backup_count", 0)})
        if path in ("/login", "/callback", "/api/word/Citrello.bas"):
            return self.send(404, {"error": WEB_ONLY_MISSING})
        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/web/restore":
            try:
                return self.send(200, {"items": restore(self._body)})
            except ValueError as e:
                return self.send(400, {"error": str(e)})
        if path in ("/api/quit", "/api/update/install", "/api/library/import-zotero-local",
                    "/api/library/import-mendeley", "/api/library/export-mendeley", "/api/doc/update-file") \
                or path.startswith(("/api/claude/", "/api/codex/", "/api/word/install", "/api/word/remove",
                                    "/api/bibexports/", "/api/account/")):
            return self.send(400, {"error": WEB_ONLY_MISSING})
        return super().do_POST()


def handle(method, path, headers, body):
    """One API request from the page -> (status, content type, headers, body bytes)."""
    if body is not None and not isinstance(body, bytes):
        body = body.to_bytes() if hasattr(body, "to_bytes") else bytes(body)  # a JavaScript Uint8Array
    h = WebHandler(method, path, dict(headers or {}), body)
    try:
        (h.do_POST if method == "POST" else h.do_GET)()
    except Exception as e:  # the desktop server would log and drop the connection; tell the page instead
        import traceback
        traceback.print_exc()
        h.send(500, {"error": str(e) or e.__class__.__name__})
    if h.response is None:
        h.send(500, {"error": "No answer"})
    return h.response


def start():
    """Called once when the worker has loaded the code (not by the desktop app or the tests)."""
    threading.Thread = _InlineThread  # app.py looks it up each time it starts a job
    library.migrate_legacy_data()
    os.makedirs(library.HOME, exist_ok=True)
