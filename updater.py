"""
In-app updates.

check(): is there a newer release? (asked through mcp.citrello.app, which caches GitHub's answer, with GitHub
itself as a fallback). install(): download the new version from the citrello GitHub release, check its SHA-256
against the one GitHub publishes, swap it in, and start it. The new copy asks the old one to quit.

- Windows: Citrello.exe, CitrelloMCP.exe and Citrello.mcpb are replaced in their folder. A running .exe can't be
  overwritten but can be renamed, so the old files become *.old and are deleted at the next start.
- Mac: the whole Citrello.app is replaced (if its folder is writable), then opened again.
- Microsoft Store copies are updated by the Store; copies run from source are updated with git.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile

import citrello_core as core

REPO = "citrello/citrello"
ALLOWED_PREFIX = os.environ.get("CITRELLO_UPDATE_ALLOW") or f"https://github.com/{REPO}/releases/download/"  # env: tests only
LATEST_URLS = [os.environ.get("CITRELLO_UPDATE_URL") or "https://mcp.citrello.app/api/latest",
               f"https://api.github.com/repos/{REPO}/releases/latest"]
CHECK_EVERY = 6 * 3600
ASSET = {"windows": "Citrello-windows.zip", "mac": "Citrello-mac.zip"}

_cache = {"at": 0, "info": None}
STATE = {"phase": "idle", "done": 0, "total": 0, "error": None}  # idle / downloading / installing / restarting / error


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def platform_name():
    return "windows" if sys.platform.startswith("win") else "mac" if sys.platform == "darwin" else "other"


def how_installed(store=False):
    """'windows' / 'mac' when this copy can update itself, otherwise why not."""
    if store:
        return "store"
    if not getattr(sys, "frozen", False):
        return "source"
    return platform_name()


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Citrello/" + core.VERSION,
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _normalise(rel):
    """GitHub's release JSON (or the bridge's copy of it) -> what the app needs."""
    assets = {a["name"]: {"url": a.get("browser_download_url") or a.get("url"), "size": a.get("size", 0),
                          "sha256": (a.get("digest") or a.get("sha256") or "").replace("sha256:", "")}
              for a in rel.get("assets", []) if a.get("name")}
    return {"version": str(rel.get("tag_name") or rel.get("version") or "").lstrip("v"),
            "notes_url": rel.get("html_url") or rel.get("notes_url") or f"https://github.com/{REPO}/releases/latest",
            "published": rel.get("published_at") or rel.get("published") or "", "assets": assets}


def check(force=False, store=False):
    now = time.time()
    if not force and _cache["info"] and now - _cache["at"] < CHECK_EVERY:
        return _cache["info"]
    latest, error = None, None
    for url in LATEST_URLS:
        try:
            latest = _normalise(_get_json(url))
            if latest["version"]:
                break
        except Exception as e:
            error = str(e)
            latest = None
    how = how_installed(store)
    info = {"current": core.VERSION, "how": how, "checked": int(now)}
    if latest:
        asset = latest["assets"].get(ASSET.get(how, ""))
        info.update(latest=latest["version"], notes_url=latest["notes_url"], published=latest["published"],
                    available=version_tuple(latest["version"]) > version_tuple(core.VERSION),
                    can_install=bool(asset) and how in ASSET)
        info["_asset"] = asset
    else:
        info.update(available=False, can_install=False, error=error or "No release found")
    _cache.update(at=now, info=info)
    return info


def public(info):
    return {k: v for k, v in (info or {}).items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------

def _download(asset, folder):
    url = asset["url"]
    if not url.startswith(ALLOWED_PREFIX):
        raise ValueError("Update refused: it doesn't come from Citrello's GitHub releases.")
    path = os.path.join(folder, url.rsplit("/", 1)[-1])
    req = urllib.request.Request(url, headers={"User-Agent": "Citrello/" + core.VERSION})
    h = hashlib.sha256()
    STATE.update(phase="downloading", done=0, total=asset.get("size") or 0)
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            STATE["done"] += len(chunk)
    if asset.get("sha256") and h.hexdigest() != asset["sha256"].lower():
        raise ValueError("Update refused: the download doesn't match the release's checksum. Please try again.")
    if not zipfile.is_zipfile(path):
        raise ValueError("Update refused: the download isn't a valid zip file.")
    return path


def cleanup_old(app_dir=None):
    """Delete *.old files left by the last Windows update (they were still running then)."""
    app_dir = app_dir or os.path.dirname(sys.executable)
    for name in os.listdir(app_dir) if os.path.isdir(app_dir) else []:
        if name.endswith(".old"):
            try:
                os.remove(os.path.join(app_dir, name))
            except OSError:
                pass


def apply_windows(zip_path, app_dir):
    """Swap the files in app_dir for the ones in the zip (folder 'Citrello/' inside). Returns the new exe path."""
    work = tempfile.mkdtemp(prefix="citrello_update_")
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)
    src = os.path.join(work, "Citrello") if os.path.isdir(os.path.join(work, "Citrello")) else work
    names = [n for n in ("Citrello.exe", "CitrelloMCP.exe", "Citrello.mcpb") if os.path.isfile(os.path.join(src, n))]
    stale = os.path.join(app_dir, "Citrello.mcpb")  # older versions shipped it; it's now built when needed
    if "Citrello.mcpb" not in names and os.path.exists(stale):
        try:
            os.remove(stale)
        except OSError:
            pass
    if "Citrello.exe" not in names:
        raise ValueError("Update refused: Citrello.exe is missing from the download.")
    moved = []
    try:
        for n in names:
            dst = os.path.join(app_dir, n)
            if os.path.exists(dst):
                old = dst + ".old"
                if os.path.exists(old):
                    os.remove(old)
                os.replace(dst, old)  # allowed even while the .exe is running
                moved.append((old, dst))
            shutil.copy2(os.path.join(src, n), dst)
    except OSError:
        for old, dst in reversed(moved):  # put the old version back
            try:
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(old, dst)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return os.path.join(app_dir, "Citrello.exe")


def mac_bundle():
    """/Applications/Citrello.app for /Applications/Citrello.app/Contents/MacOS/Citrello."""
    p = os.path.abspath(sys.executable)
    for _ in range(3):
        p = os.path.dirname(p)
    return p if p.endswith(".app") else None


def apply_mac(zip_path, bundle):
    """Replace the .app bundle. Returns the new bundle path."""
    parent = os.path.dirname(bundle)
    if not os.access(parent, os.W_OK):
        raise PermissionError(f"Citrello can't replace itself in {parent}. Download the new version from citrello.app.")
    work = tempfile.mkdtemp(prefix="citrello_update_", dir=parent if os.access(parent, os.W_OK) else None)
    subprocess.run(["ditto", "-x", "-k", zip_path, work], check=True)  # keeps the app's links and signature
    new = os.path.join(work, "Citrello.app")
    if not os.path.isdir(new):
        raise ValueError("Update refused: Citrello.app is missing from the download.")
    old = bundle + ".old"
    shutil.rmtree(old, ignore_errors=True)
    os.replace(bundle, old)
    try:
        os.replace(new, bundle)
    except OSError:
        os.replace(old, bundle)
        raise
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    return bundle


def install(store=False, on_restart=None):
    """Download and install the latest version, then start it. Runs in the background; follow STATE."""
    info = check(force=True, store=store)
    if not info.get("available"):
        raise ValueError("Citrello is up to date.")
    if not info.get("can_install"):
        raise ValueError({"store": "The Microsoft Store updates Citrello for you.",
                          "source": "This copy runs from the source code: update it with git pull."}
                         .get(info["how"], "Download the new version from citrello.app/download."))

    def run():
        try:
            folder = tempfile.mkdtemp(prefix="citrello_dl_")
            zip_path = _download(info["_asset"], folder)
            STATE.update(phase="installing")
            if info["how"] == "windows":
                exe = apply_windows(zip_path, os.path.dirname(sys.executable))
                cmd = [exe, "--no-browser"]  # the open Citrello tab reloads itself
            else:
                bundle = apply_mac(zip_path, mac_bundle())
                cmd = ["open", "-n", bundle, "--args", "--no-browser"]
            shutil.rmtree(folder, ignore_errors=True)
            STATE.update(phase="restarting")
            time.sleep(1.5)  # let the page see "restarting"
            # The new copy asks this one to quit (and takes over its address), then opens the browser.
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(cmd, close_fds=True, creationflags=flags if sys.platform.startswith("win") else 0)
            if on_restart:
                on_restart()
        except Exception as e:
            STATE.update(phase="error", error=str(e))

    STATE.update(phase="downloading", done=0, total=0, error=None)
    threading.Thread(target=run, name="citrello-update", daemon=True).start()
    return {"started": True, "version": info["latest"]}
