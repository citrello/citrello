"""The browser extension's store packages: the right files and the right manifest for each browser."""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packaging"))
pytest.importorskip("PIL")
import build_extension as B  # noqa: E402


@pytest.mark.parametrize("kind", ["chrome", "firefox"])
def test_store_package(tmp_path, kind):
    out = str(tmp_path / kind)
    man = B.build(out, kind)
    assert man["manifest_version"] == 3 and man["name"] == "Citrello"
    for f in B.FILES + ["manifest.json", "icons/16.png", "icons/128.png"]:
        assert os.path.exists(os.path.join(out, f)), f
    assert json.load(open(os.path.join(out, "manifest.json")))["version"] == man["version"]
    # access to all websites only when the person allows it (first bulk fetch), never at install
    # citrello.app (to find the Citrello tab) and the desktop app on this computer (for the toolbar panel)
    assert man["host_permissions"] == ["https://citrello.app/*", "http://localhost:8585/*", "http://127.0.0.1:8585/*"]
    assert man["optional_host_permissions"] == ["<all_urls>"]
    assert man["action"]["default_popup"] == "popup.html"  # a panel under the toolbar button, not a new window
    assert man["commands"]["_execute_action"]["suggested_key"]["default"] == "Alt+Shift+C"
    assert set(man["permissions"]) == {"activeTab", "scripting", "storage"}
    assert man["content_scripts"][0]["matches"][0] == "https://citrello.app/*"
    if kind == "firefox":
        assert man["background"] == {"scripts": ["grab.js", "background.js"]}
        assert man["browser_specific_settings"]["gecko"]["id"] == "extension@citrello.app"
    else:
        assert man["background"] == {"service_worker": "background.js"}
    if shutil.which("node"):
        for f in B.FILES:
            if f.endswith(".js"):
                assert subprocess.run(["node", "--check", os.path.join(out, f)]).returncode == 0, f


def test_test_copy_runs_on_a_local_citrello(tmp_path):
    man = B.build(str(tmp_path / "t"), "chrome", test=True)
    assert "http://localhost:8099/*" in man["content_scripts"][0]["matches"] and "<all_urls>" not in man["host_permissions"]


def test_desktop_app_lets_the_extension_add_papers_only():
    """The desktop app refuses API writes from other sites; the extension's panel may use /api/clip/ only."""
    import http.client
    sys.path.insert(0, ROOT)
    import app

    def allowed(origin, path):
        h = app.Handler.__new__(app.Handler)
        h.path, h.headers = path, http.client.HTTPMessage()
        if origin:
            h.headers["Origin"] = origin
        return h.same_origin()
    ext = "chrome-extension://abcdefghijklmnop"
    assert allowed(ext, "/api/clip/preview") and allowed("moz-extension://x-y", "/api/clip/add")
    assert not allowed(ext, "/api/library/delete") and not allowed(ext, "/api/config")
    assert not allowed("https://evil.example", "/api/clip/add")
    assert allowed("http://localhost:8585", "/api/library/delete") and allowed(None, "/api/config")
