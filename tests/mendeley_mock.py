"""A stand-in for Mendeley's API (documents, two pages) that browsers may call, like the real one."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "mendeley-test-token"
DOCS = [
    {"id": "m1", "type": "journal", "title": "Sustainable decommissioning of an offshore wind farm",
     "source": "Renewable Energy", "year": 2017, "volume": "102", "pages": "470-480",
     "authors": [{"first_name": "Eva", "last_name": "Topham"}, {"first_name": "David", "last_name": "McMillan"}],
     "identifiers": {"doi": "10.1016/j.renene.2016.10.066"}},
    {"id": "m2", "type": "book", "title": "Wind Energy Handbook", "publisher": "Wiley", "year": 2011,
     "authors": [{"first_name": "Tony", "last_name": "Burton"}]},
]


def start():
    """-> (base URL, server). Page 1 has one document and a Link to page 2, as Mendeley paginates."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "authorization,accept")
            self.send_header("Access-Control-Expose-Headers", "Link")

        def do_OPTIONS(self):
            self.send_response(200)
            self.cors()
            self.end_headers()

        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                self.send_response(401)
                self.cors()
                self.end_headers()
                return
            page2 = "page=2" in self.path
            body = json.dumps(DOCS[1:] if page2 else DOCS[:1]).encode()
            self.send_response(200)
            self.cors()
            self.send_header("Content-Type", "application/vnd.mendeley-document.1+json")
            if not page2:
                self.send_header("Link", f'<{base}/documents?limit=500&view=bib&page=2>; rel="next"')
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return base, srv
