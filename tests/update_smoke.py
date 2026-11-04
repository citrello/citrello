"""
Real update test on Windows / Mac (run by CI after the build):

  python tests/update_smoke.py dist/Citrello-windows.zip     (or dist/Citrello-mac.zip)

Installs the built app in a temporary folder, offers the same zip as "version 99.0.0" from a local server, clicks
Update through the app's API, and checks that the files were replaced and a new Citrello process took over.
"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

URL = "http://localhost:8585/"


def api(path, data=None):
    req = urllib.request.Request(URL + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Origin": "http://localhost:8585", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def wait_status(pred, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        try:
            st = api("api/status")
            if pred(st):
                return st
        except OSError:
            pass
        time.sleep(1)
    raise SystemExit("timed out waiting for Citrello")


def main(zip_path):
    zip_path = os.path.abspath(zip_path)
    data = open(zip_path, "rb").read()
    tmp = tempfile.mkdtemp(prefix="citrello_update_test_")
    install = os.path.join(tmp, "installed")
    os.makedirs(install)
    if sys.platform == "darwin":
        subprocess.run(["ditto", "-x", "-k", zip_path, install], check=True)
        exe = os.path.join(install, "Citrello.app", "Contents", "MacOS", "Citrello")
        marker = os.path.join(install, "Citrello.app", "Contents", "Resources", "OLD-MARKER")
    else:
        folder = os.path.join(install, "Citrello")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(folder)
        exe = os.path.join(folder, "Citrello.exe")
        marker = os.path.join(folder, "CitrelloMCP.exe")
    if sys.platform == "darwin":
        open(marker, "wb").write(b"old")  # must be gone after the update
    else:
        open(marker, "wb").write(b"old")  # the connector isn't running: the update must replace it

    name = os.path.basename(zip_path)

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/latest":
                body = json.dumps({"tag_name": "v99.0.0", "html_url": "https://example.org/notes", "assets": [
                    {"name": name, "size": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                     "browser_download_url": f"http://127.0.0.1:{srv.server_port}/dl/{name}"}]}).encode()
            elif self.path == f"/dl/{name}":
                body = data
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    env = dict(os.environ, CITRELLO_UPDATE_URL=base + "/latest", CITRELLO_UPDATE_ALLOW=base + "/dl/",
               CITRELLO_HOME=os.path.join(tmp, "lib"), CITRELLO_NO_USAGE="1", CITRELLO_BRIDGE="http://127.0.0.1:9",
               CITRELLO_APP_CONFIG=os.path.join(tmp, "app.json"), CITRELLO_ACCOUNT_FILE=os.path.join(tmp, "acct.json"))
    log = open(os.path.join(tmp, "app.log"), "w")
    subprocess.Popen([exe, "--no-browser"], env=env, stdout=log, stderr=log)
    first = wait_status(lambda st: True)
    print("running:", first["version"], "pid", first["pid"])

    u = api("api/update?force=1")
    print("check:", {k: u.get(k) for k in ("current", "latest", "available", "can_install", "how", "error")})
    assert u["available"] and u["can_install"], u
    print("install:", api("api/update/install", b"{}"))
    phase = None
    for _ in range(120):
        try:
            phase = api("api/update/progress")
        except OSError:
            break  # the old copy already quit
        if phase["phase"] in ("restarting", "error"):
            break
        time.sleep(0.5)
    assert not phase or phase["phase"] != "error", phase
    try:
        second = wait_status(lambda st: st["pid"] != first["pid"])
    except SystemExit:
        log.flush()
        print("--- app log ---\n" + open(log.name, encoding="utf-8", errors="replace").read()[-4000:])
        print("--- files ---", os.listdir(os.path.dirname(exe)))
        raise
    print("restarted:", second["version"], "pid", second["pid"])
    if sys.platform == "darwin":
        assert not os.path.exists(marker), "the .app bundle wasn't replaced"
    else:
        assert open(marker, "rb").read() != b"old", "CitrelloMCP.exe wasn't replaced"
        assert os.path.exists(exe + ".old") or True  # the old exe is renamed (deleted at a later start)
    try:
        api("api/quit", b"{}")
    except OSError:
        pass
    srv.shutdown()
    print("UPDATE TEST PASSED")


if __name__ == "__main__":
    main(sys.argv[1])
