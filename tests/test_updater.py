"""In-app updates: version check, download checks, and swapping the files."""
import hashlib
import http.server
import json
import os
import threading
import zipfile

import pytest

import citrello_core as core
import updater


def test_version_order():
    assert updater.version_tuple("1.0.10") > updater.version_tuple("1.0.9")
    assert updater.version_tuple("v2.0.0") > updater.version_tuple("1.9.9")
    assert updater.version_tuple("1.0.1") == (1, 0, 1)


@pytest.fixture()
def server(tmp_path):
    files = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write(body or b"")

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield base, files
    srv.shutdown()


def _zip(tmp_path, content=b"new exe"):
    p = tmp_path / "Citrello-windows.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("Citrello/Citrello.exe", content)
        z.writestr("Citrello/CitrelloMCP.exe", b"new mcp")
        z.writestr("Citrello/Citrello.mcpb", b"new mcpb")
    return p.read_bytes()


def _release(base, data, version="9.9.9", digest=None):
    return json.dumps({"tag_name": "v" + version, "html_url": "https://github.com/citrello/citrello/releases/tag/v" + version,
                       "assets": [{"name": "Citrello-windows.zip", "size": len(data),
                                   "digest": "sha256:" + (digest or hashlib.sha256(data).hexdigest()),
                                   "browser_download_url": base + "/dl/Citrello-windows.zip"}]}).encode()


def test_check_finds_newer_version(server, monkeypatch, tmp_path):
    base, files = server
    data = _zip(tmp_path)
    files["/latest"] = _release(base, data)
    monkeypatch.setattr(updater, "LATEST_URLS", [base + "/nowhere", base + "/latest"])  # first one fails: fallback
    info = updater.check(force=True)
    assert info["available"] and info["latest"] == "9.9.9" and info["current"] == core.VERSION
    assert "_asset" not in updater.public(info)
    files["/latest"] = _release(base, data, version="0.0.1")
    assert not updater.check(force=True)["available"]


def test_download_is_checked(server, monkeypatch, tmp_path):
    base, files = server
    data = _zip(tmp_path)
    files["/dl/Citrello-windows.zip"] = data
    asset = {"url": base + "/dl/Citrello-windows.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    with pytest.raises(ValueError, match="doesn't come from"):
        updater._download(asset, str(tmp_path))  # not a citrello GitHub release address
    monkeypatch.setattr(updater, "ALLOWED_PREFIX", base + "/dl/")
    assert zipfile.is_zipfile(updater._download(asset, str(tmp_path)))
    with pytest.raises(ValueError, match="checksum"):
        updater._download(dict(asset, sha256="0" * 64), str(tmp_path))


def test_windows_swap(tmp_path):
    app = tmp_path / "Citrello"
    app.mkdir()
    for n in ("Citrello.exe", "CitrelloMCP.exe", "Citrello.mcpb"):
        (app / n).write_bytes(b"old")
    z = tmp_path / "new.zip"
    z.write_bytes(_zip(tmp_path, b"new exe"))
    exe = updater.apply_windows(str(z), str(app))
    assert open(exe, "rb").read() == b"new exe"
    assert (app / "CitrelloMCP.exe").read_bytes() == b"new mcp"
    assert (app / "Citrello.exe.old").read_bytes() == b"old"   # the running one is renamed, not overwritten
    updater.cleanup_old(str(app))
    assert not (app / "Citrello.exe.old").exists()


def test_copies_that_cant_update_themselves():
    assert updater.how_installed(store=True) == "store"
    assert updater.how_installed() == "source"  # tests run from source
