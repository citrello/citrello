"""A pretend Claude API for tests (the bridge's ANTHROPIC_BASE_URL points here): answers /v1/messages like Claude
would for Citrello's two requests, with simple rules instead of a model, and records what it was sent."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEEN = []  # the requests, for the tests to look at
WEB_PAPERS = [{"title": "Cable recovery dominates offshore wind decommissioning costs", "authors": "Lee et al.", "year": "2021",
               "doi": "10.1016/j.example.2021.0042", "url": "https://doi.org/10.1016/j.example.2021.0042", "verdict": "supports",
               "quote": "Export cable recovery was the costliest operation in all three farms.",
               "reason": "It finds cable recovery was the most expensive step."}]


def _words(t):
    return {w[:6] for w in re.findall(r"[a-z]{4,}", t.lower())}


def judge(text):
    """Rules: "doubled" contradicts "halved"; a passage sharing 3+ words supports; else not found."""
    results = []
    for item in re.finditer(r'<item id="([^"]+)">\s*<claim>(.*?)</claim>(.*?)</item>', text, re.S):
        iid, claim, rest = item.groups()
        for paper in re.finditer(r'<paper ref="([^"]+)"[^>]*>(.*?)</paper>', rest, re.S):
            ref, body = paper.groups()
            passages = re.findall(r"<passage n=\"(\d+)\">(.*?)</passage>", body, re.S)
            best = max(passages, key=lambda p: len(_words(p[1]) & _words(claim)), default=None)
            shared = len(_words(best[1]) & _words(claim)) if best else 0
            sentence = lambda p: next((s for s in re.split(r"(?<=[.!?])\s+", p) if len(_words(s) & _words(claim)) >= 2), p[:120])
            if best and "doubled" in claim and "halved" in best[1]:
                q = next(s for s in re.split(r"(?<=[.!?])\s+", best[1]) if "halved" in s)
                results.append(dict(id=iid, paper=ref, verdict="contradicts", passage=int(best[0]), quote=q, reason="The paper says it halved the cost."))
            elif best and shared >= 3:
                results.append(dict(id=iid, paper=ref, verdict="supports", passage=int(best[0]), quote=sentence(best[1]), reason="The paper says this."))
            else:
                results.append(dict(id=iid, paper=ref, verdict="not_found", passage=0, quote="", reason="The passages don't address this."))
    return {"results": results}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append({"path": self.path, "key": self.headers.get("x-api-key") or (self.headers.get("authorization") or "").replace("Bearer ", ""), "headers": dict(self.headers), "body": body})
        user = body["messages"][0]["content"]
        if any(t.get("type", "").startswith("web_search") for t in body.get("tools", [])):
            text = "I searched and found these.\n" + json.dumps({"papers": WEB_PAPERS})
        else:
            text = json.dumps(judge(user))
        out = {"id": "msg_test", "type": "message", "role": "assistant", "model": body.get("model"),
               "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
               "usage": {"input_tokens": 100, "output_tokens": 50}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    allow_reuse_address = True

    def stop(self):
        self.shutdown()
        self.server_close()


def start(port=8799):
    srv = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    import time
    start()
    print("pretend Claude on http://127.0.0.1:8799")
    while True:
        time.sleep(3600)
