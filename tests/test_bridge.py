"""The app side of the ChatGPT / web connector: WebSocket client and the limits on web requests."""
import json
import os
import socket
import struct
import threading

import pytest

import bridge_client as bc
import library
import mcp_server


def _server_frame(opcode, payload, fin=True):
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    return head + payload


def _read_client_frame(sock):
    def exact(n):
        b = b""
        while len(b) < n:
            b += sock.recv(n - len(b))
        return b
    b1, b2 = exact(2)
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", exact(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", exact(8))[0]
    assert b2 & 0x80, "client frames must be masked"
    mask = exact(4)
    return b1 & 0x0F, bc._mask(exact(n), mask)


def _pair():
    a, b = socket.socketpair()
    ws = bc.WebSocket.__new__(bc.WebSocket)
    ws.sock, ws.buf, ws.send_lock, ws.closed = a, b"", threading.Lock(), False
    return ws, b


def test_websocket_frames_round_trip():
    ws, server = _pair()
    big = "x" * 70000
    ws.send("hello")
    ws.send(big)
    assert _read_client_frame(server) == (1, b"hello")
    assert _read_client_frame(server) == (1, big.encode())
    # server: ping, then a message split into two fragments, then a close
    server.sendall(_server_frame(0x9, b"hb") + _server_frame(0x1, b"par", fin=False) + _server_frame(0x0, b"ts")
                   + _server_frame(0x8, struct.pack(">H", 1000)))
    assert ws.recv() == "parts"
    assert _read_client_frame(server) == (0xA, b"hb")  # pong answered the ping
    assert ws.recv() is None
    assert _read_client_frame(server)[0] == 0x8


def test_mask_matches_reference():
    data, mask = os.urandom(1000), os.urandom(4)
    assert bc._mask(data, mask) == bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    assert bc._mask(b"", mask) == b""


def _call(name, **args):
    r = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r["result"]


def test_web_requests_are_limited(tmp_path):
    outside = str(tmp_path / "paper.docx")
    with mcp_server.remote_call():
        r = _call("create_word_document", text="Hello", output_path=outside)
        assert r["isError"] and "Documents" in r["content"][0]["text"]
        r = _call("import_references", file_path=str(tmp_path / "secrets.key"))
        assert r["isError"]
        r = _call("create_word_document", text="Hello", title="Web test")
        assert not r["isError"]
        saved = json.loads(r["content"][0]["text"])["saved_to"]
        assert os.path.realpath(saved).startswith(os.path.realpath(os.path.join(library.HOME, "Documents")))
        # updating a file elsewhere saves a copy in Documents and leaves the original alone
        import shutil
        shutil.copy(saved, outside)
        before = open(outside, "rb").read()
        r = _call("update_word_document", path=outside)
        assert not r["isError"], r
        assert json.loads(r["content"][0]["text"])["saved_to"] != outside
        assert open(outside, "rb").read() == before
    # the same request from Claude Desktop (local) may write anywhere
    r = _call("create_word_document", text="Hello", output_path=outside)
    assert not r["isError"]


def test_signed_out_status():
    assert not bc.load_account()
    st = bc.CONNECTION.status()
    assert st["signed_in"] is False and st["state"] == "signed_out"


def test_usage_ping_once_a_day(tmp_path, monkeypatch):
    import http.server
    import usage
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(usage, "INSTALL_FILE", str(tmp_path / "install.json"))
    monkeypatch.delenv("CITRELLO_NO_USAGE", raising=False)
    url = f"http://127.0.0.1:{srv.server_port}"
    assert usage.ping_once(url, signed_in=True)
    assert not usage.ping_once(url)                  # already sent today
    assert len(got) == 1
    p = got[0]
    assert set(p) == {"install_id", "version", "os", "os_version", "store", "signed_in"}  # nothing personal
    assert len(p["install_id"]) == 32 and p["signed_in"] is True
    monkeypatch.setenv("CITRELLO_NO_USAGE", "1")
    assert not usage.ping_once(url, force=True)      # switched off
    srv.shutdown()
