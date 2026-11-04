"""
Anonymous usage count: once a day, while the app runs, tell mcp.citrello.app that this install is in use.

Sent: a random install ID (made on this computer, not linked to anything else), the app version, the operating
system and its version, whether it's the Microsoft Store version, and whether someone is signed in.
Never sent: names, emails, the library, documents, file paths or anything typed.

Turn it off in Settings (or set CITRELLO_NO_USAGE=1).
"""
import json
import os
import platform
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone

import citrello_core as core

INSTALL_FILE = os.environ.get("CITRELLO_INSTALL_FILE") or os.path.join(os.path.expanduser("~"), ".citrello_install.json")
CHECK_EVERY = 3 * 3600  # look every 3 hours; at most one ping per day


def _load():
    try:
        with open(INSTALL_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and len(str(d.get("id", ""))) == 32:
            return d
    except (OSError, ValueError):
        pass
    d = {"id": uuid.uuid4().hex}
    _save(d)
    return d


def _save(d):
    try:
        with open(INSTALL_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def os_info():
    if sys.platform.startswith("win"):
        try:
            build = int(platform.version().split(".")[-1])
        except ValueError:
            build = 0
        return "windows", "11" if build >= 22000 else platform.release()
    if sys.platform == "darwin":
        return "mac", (platform.mac_ver()[0] or "").split(".")[0]
    return "linux", ""


def payload(signed_in=False, store=False):
    name, ver = os_info()
    return {"install_id": _load()["id"], "version": core.VERSION, "os": name, "os_version": ver,
            "store": bool(store), "signed_in": bool(signed_in)}


def ping_once(bridge, signed_in=False, store=False, force=False):
    """Send today's ping unless it was already sent. Returns True if one was sent."""
    if os.environ.get("CITRELLO_NO_USAGE"):
        return False
    d = _load()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if d.get("last") == today and not force:
        return False
    req = urllib.request.Request(bridge.rstrip("/") + "/api/ping", data=json.dumps(payload(signed_in, store)).encode(),
                                 method="POST", headers={"Content-Type": "application/json",
                                                         "User-Agent": "Citrello/" + core.VERSION})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except OSError:
        return False
    d["last"] = today
    _save(d)
    return True


def start(bridge, enabled, signed_in, store):
    """Background loop. enabled, signed_in and store are functions, read each time."""
    def run():
        time.sleep(20)  # let the app start first
        while True:
            try:
                if enabled():
                    ping_once(bridge, signed_in(), store())
            except Exception:
                pass
            time.sleep(CHECK_EVERY)
    threading.Thread(target=run, name="citrello-usage", daemon=True).start()
