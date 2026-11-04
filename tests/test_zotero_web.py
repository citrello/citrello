"""Import from the Zotero web API: a large library, odd data, and Zotero asking to slow down."""
import http.server
import json
import threading
import time
import urllib.parse

import pytest

import library

N = 1234


def entry(i):
    kind = "attachment" if i % 97 == 0 else "note" if i % 89 == 0 else "journalArticle"
    c = {"id": f"123/K{i:06d}", "type": "article-journal", "title": f"Offshore wind study number {i} on blade recycling",
         "author": [{"family": f"Author{i % 40}", "given": "A."}], "container-title": "Energy",
         "issued": {"date-parts": [[str(1990 + i % 30), i % 12 + 1]]}, "volume": i % 50, "page": i,
         "DOI": f"10.1000/z.{i}"}
    if i % 101 == 0:
        c["issued"] = {"raw": "Spring 2019"}
    if i % 103 == 0:
        c["author"] = [{"literal": "World Wind Energy Association"}]
    if i % 107 == 0:
        c.pop("title")  # no title: skipped, not an error
    if kind != "journalArticle":
        c = {"id": f"123/K{i:06d}", "title": f"file{i}.pdf"}
    return {"key": f"K{i:06d}", "data": {"itemType": kind}, "csljson": c}


class Zotero(http.server.BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        Zotero.hits += 1
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if self.headers.get("Zotero-API-Key") != "good":
            return self._send(403, b"Forbidden")
        if "/users/123/" not in u.path:
            return self._send(404, b"Not found")
        if Zotero.hits == 3:  # Zotero asks to slow down once
            return self._send(429, b"Too many requests", {"Retry-After": "1"})
        start, limit = int(q.get("start", 0)), min(int(q.get("limit", 25)), 100)
        body = json.dumps([entry(i) for i in range(start, min(start + limit, N))]).encode()
        self._send(200, body, {"Total-Results": str(N), "Content-Type": "application/json"})

    def _send(self, code, body, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture()
def zotero(monkeypatch, tmp_path):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Zotero)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(library, "ZOTERO_API", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(library, "HOME", str(tmp_path / "lib"))
    Zotero.hits = 0
    yield
    srv.shutdown()


def test_large_library_with_odd_items(zotero):
    t = time.time()
    added, skipped = library.import_from_zotero("123", "good", "From Zotero")
    took = time.time() - t
    refs = [i for i in range(N) if not (i % 97 == 0 or (i % 89 == 0))]
    no_title = [i for i in refs if i % 107 == 0]
    assert added == len(refs) - len(no_title), (added, skipped)
    assert took < 30, f"import took {took:.1f}s"
    data = library.load()
    assert not any(it["csl"]["title"].endswith(".pdf") for it in data["items"])  # attachments left out
    it = next(i for i in data["items"] if i.get("zotero_key") == "K000202")  # 202 % 101 == 0: raw date
    assert it["csl"]["issued"] == {"date-parts": [[2019]]} and it["csl"]["volume"] == "2"
    again = library.import_from_zotero("123", "good")
    assert again[0] == 0  # nothing twice


def test_clear_messages_for_setup_mistakes(zotero):
    with pytest.raises(ValueError, match="refused the API key"):
        library.import_from_zotero("123", "wrong")
    with pytest.raises(ValueError, match="user ID"):
        library.import_from_zotero("armin", "good")
