#!/usr/bin/env python3
"""
Citrello - a Claude-friendly reference manager.

Runs a small web server on this computer only (http://localhost:8585) and opens
it in the browser:
  Library    your references (shared with the Claude connector), add by DOI or
             pasted reference, projects, reliability checks, import/export
             (Mendeley, Zotero, RIS, BibTeX, CSL-JSON)
  Documents  convert typed citations into live ones; update/restyle documents
             with [@Key] markers or live citations in any journal style
  Settings   connect Claude Desktop, Mendeley, Zotero; default style
"""
import argparse
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import docx

import citeformat
import library
import citrello_core as core
import word_addin

VERSION = core.VERSION
PORT = 8585
REDIRECT = f"http://localhost:{PORT}/callback"
USER_CONFIG = os.path.expanduser("~/.citrello_app.json")
if os.environ.get("CITRELLO_API"):  # for testing against a mock server
    core.API = os.environ["CITRELLO_API"]


def resource(name):
    """Path of a file shipped with the app (works inside a PyInstaller .exe too)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_config():
    cfg = {}
    for path in (resource("app_config.json"),
                 os.path.join(os.path.dirname(sys.executable), "citrello_config.json"),
                 USER_CONFIG):
        try:
            with open(path, encoding="utf-8") as f:
                cfg.update({k: v for k, v in json.load(f).items() if v})
        except (OSError, ValueError):
            pass
    return cfg


def save_user_config(**values):
    cfg = {}
    try:
        cfg = json.load(open(USER_CONFIG, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    cfg.update(values)
    with open(USER_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def logged_in():
    return os.path.exists(core.TOKEN_FILE)


def profile_name():
    try:
        me, _ = core.mendeley_call("/profiles/me", media="application/vnd.mendeley-profiles.1+json")
        return me.get("display_name") or me.get("email") or "your Mendeley account"
    except BaseException:  # expired/invalid login -> treat as logged out
        return None


# --------------------------------------------------------------------------
# Jobs (one per uploaded article), run in background threads
# --------------------------------------------------------------------------

JOBS = {}
OAUTH_STATES = set()
VERIFY = {}  # verification runs: id -> {"done", "i", "n", "key", "result", "error"}


def start_verify(keys, include_verified):
    vid = secrets.token_hex(6)
    state = VERIFY[vid] = {"done": False, "i": 0, "n": len(keys) if keys else 0, "key": "", "result": None, "error": None}

    def progress(i, n, key):
        state.update(i=i, n=n, key=key)

    def run():
        try:
            state["result"] = library.verify(keys, include_verified, load_config().get("email"), progress)
        except Exception as e:
            traceback.print_exc()
            state["error"] = str(e)
        state["done"] = True
    threading.Thread(target=run, daemon=True).start()
    return vid


class Job:
    def __init__(self, filename, data):
        self.id = secrets.token_hex(8)
        self.dir = tempfile.mkdtemp(prefix="citrello_")
        safe = re.sub(r"[^\w\-. ]", "_", os.path.basename(filename)) or "article.docx"
        if not safe.lower().endswith(".docx"):
            safe += ".docx"
        self.filename = safe
        self.docx = os.path.join(self.dir, safe)
        with open(self.docx, "wb") as f:
            f.write(data)
        self.state = "analysing"  # analysing -> ready -> converting -> done | error
        self.progress, self.total, self.message = 0, 0, "Reading the document..."
        self.refs, self.index, self.result, self.error = [], None, None, None
        self.cited, self.mode, self.found = set(), None, 0
        self.check_csv = None

    def public(self):
        return {"id": self.id, "state": self.state, "progress": self.progress, "total": self.total,
                "message": self.message, "error": self.error, "filename": self.filename,
                "mode": self.mode, "found": self.found,
                "refs": [{"number": r["number"], "raw": r["raw"], "source": r["source"],
                          "title": r["csl"].get("title", ""), "authors": short_authors(r["csl"]),
                          "container": r["csl"].get("container-title") or r["csl"].get("publisher", ""),
                          "year": core.year_of(r["csl"]), "doi": r["csl"].get("DOI", ""),
                          "library": r.get("library"), "cited": r["number"] in self.cited,
                          "reliability": r.get("reliability"), "checks": r.get("checks", [])}
                         for r in self.refs],
                "check_csv": self.check_csv,
                "result": self.result}

    def run(self, fn, *args):
        def target():
            try:
                fn(self, *args)
            except BaseException as e:  # SystemExit from citrello carries a readable message
                self.state, self.error = "error", str(e) or e.__class__.__name__
                traceback.print_exc()
        threading.Thread(target=target, daemon=True).start()


def short_authors(c):
    names = [a.get("family") or a.get("literal", "") for a in c.get("author", [])]
    if not names:
        return ""
    return names[0] + (" et al." if len(names) > 2 else (" & " + names[1] if len(names) == 2 else ""))


def analyse(job, offline=False):
    document = docx.Document(job.docx)
    try:
        heading, entries = core.find_bibliography(document)
    except SystemExit as e:
        raise SystemExit(f"{e} Make sure the reference list starts with a heading such as 'References'.")
    job.total = len(entries)
    email = load_config().get("email")
    for i, (num, _, text) in enumerate(entries, 1):
        job.message = f"Looking up reference {i} of {job.total}..."
        job.refs.append(core.resolve_reference(num, text, offline, email))
        job.progress = i
    core.assess_references(job.refs)
    job.check_csv = os.path.splitext(job.filename)[0] + "_reference_check.csv"
    core.write_check_csv(job.refs, os.path.join(job.dir, job.check_csv))
    job.message = "Finding the citations in the text..."
    preview = core.convert_document(job.docx, job.refs)  # dry run: nothing is saved
    job.cited, job.mode, job.found = set(preview["cited"]), preview["mode"], preview["converted"]
    job.message = "Checking your library for references you already have..."
    job.index = library._index(library.load()["items"])
    mark_library(job)
    job.state, job.message = "ready", ""


def mark_library(job):
    for r in job.refs:
        hit, how = job.index.find(r["csl"])
        r["library"] = {"status": "existing", "how": how} if hit else {"status": "new"}
        if not hit:
            job.index.add(f"pending-{r['number']}", r["csl"].get("title"), r["csl"].get("DOI"),
                          core.year_of(r["csl"]))  # a repeat later in the list is not "new"
            r["library"]["first"] = True


def convert(job, push, styles, style_id=None, project=None):
    job.state, job.progress = "converting", 0
    job.message = "Adding the references to your library..."
    before = len(library.load()["items"])
    in_lib = library.add_records(job.refs, project or os.path.splitext(job.filename)[0])
    lib_added = len(library.load()["items"]) - before
    for r in job.refs:
        r["key"] = in_lib[r["number"]]["key"]
    added = 0
    if push:
        job.message = "Re-checking your Mendeley library..."
        with contextlib.redirect_stdout(io.StringIO()):
            index = core.library_index()  # fresh copy: the library may have changed
        new = []
        for r in job.refs:
            hit, _ = index.find(r["csl"])
            if hit:
                r["mendeley_id"] = hit
            else:
                new.append(r)
        job.total = len(new)
        for i, r in enumerate(new, 1):
            hit, _ = index.find(r["csl"])  # same paper listed twice
            if hit:
                r["mendeley_id"] = hit
                continue
            job.message = f"Adding reference {i} of {len(new)} to Mendeley..."
            doc, _ = core.mendeley_call("/documents", "POST", core.csl_to_mendeley(r["csl"]),
                                           core.DOC_MEDIA)
            r["mendeley_id"] = doc["id"]
            index.add(doc["id"], r["csl"].get("title"), r["csl"].get("DOI"), core.year_of(r["csl"]))
            added += 1
            job.progress = i
    else:
        for r in job.refs:
            if str(r.get("mendeley_id", "")).startswith("pending-"):
                r.pop("mendeley_id")

    job.message = "Converting the citations in your article..."
    stem = os.path.splitext(job.filename)[0]
    refs_json = os.path.join(job.dir, "refs.json")
    with open(refs_json, "w", encoding="utf-8") as f:
        json.dump(job.refs, f, ensure_ascii=False)
    core.write_ris(job.refs, os.path.join(job.dir, stem + "_references.ris"))
    core.write_bib(job.refs, os.path.join(job.dir, stem + "_references.bib"))
    out = os.path.join(job.dir, stem + "_live.docx")
    summary = core.convert_document(job.docx, job.refs, styles, "auto", out)
    styled = None
    if style_id and style_id != "keep":
        job.message = "Formatting citations and bibliography..."
        styled = citeformat.update_document(out, out, style_id)
    job.result = {
        "library_added": lib_added, "library_existing": len(job.refs) - lib_added,
        "style": styled["style"]["title"] if styled else None,
        "converted": summary["converted"], "entries": summary["entries"], "report": summary["report"],
        "added": added, "existing": sum(1 for r in job.refs if r.get("mendeley_id")) - added,
        "pushed": push,
        "files": {"docx": stem + "_live.docx", "ris": stem + "_references.ris",
                  "bib": stem + "_references.bib"},
    }
    job.state, job.message = "done", ""


def item_public(it):
    c = it["csl"]
    return {"key": it["key"], "id": it["id"], "title": c.get("title", ""), "authors": short_authors(c),
            "year": core.year_of(c), "container": c.get("container-title") or c.get("publisher", ""),
            "doi": c.get("DOI", ""), "type": c.get("type", ""), "projects": it.get("projects", []),
            "reliability": it.get("reliability"), "checks": it.get("checks", []), "added": it.get("added", ""),
            "mendeley": bool(it.get("mendeley_id")), "zotero": bool(it.get("zotero_key"))}


# --------------------------------------------------------------------------
# Claude Desktop connection
# --------------------------------------------------------------------------

def claude_config_paths():
    """Every place Claude Desktop may read its settings from on this computer.

    On Windows, the Microsoft Store / MSIX install keeps them in a per-app folder
    (%LOCALAPPDATA%\\Packages\\Claude_*\\LocalCache\\Roaming\\Claude) instead of %APPDATA%\\Claude.
    """
    if sys.platform.startswith("win"):
        paths = [os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Claude",
                              "claude_desktop_config.json")]
        pkgs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages")
        if os.path.isdir(pkgs):
            for d in sorted(os.listdir(pkgs)):
                if d.lower().startswith("claude"):
                    paths.append(os.path.join(pkgs, d, "LocalCache", "Roaming", "Claude", "claude_desktop_config.json"))
        return paths
    if sys.platform == "darwin":
        return [os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")]
    return [os.path.expanduser("~/.config/Claude/claude_desktop_config.json")]


def claude_config_path():
    """The settings file Claude Desktop most likely uses (an existing one wins)."""
    paths = claude_config_paths()
    return next((p for p in reversed(paths) if os.path.isdir(os.path.dirname(p))), paths[0])


def connector_command():
    """How Claude Desktop should start the Citrello connector on this computer."""
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(sys.executable),
                           "CitrelloMCP.exe" if sys.platform.startswith("win") else "CitrelloMCP")
        return {"command": exe}
    py = sys.executable
    if os.path.basename(py).lower().startswith("pythonw"):  # the connector needs a console Python
        py = os.path.join(os.path.dirname(py), os.path.basename(py).lower().replace("pythonw", "python"))
    entry = {"command": py, "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")]}
    if os.environ.get("CITRELLO_HOME"):
        entry["env"] = {"CITRELLO_HOME": os.environ["CITRELLO_HOME"]}
    return entry


def _read_cfg(path):
    try:
        raw = open(path, encoding="utf-8").read()
        return json.loads(raw) if raw.strip() else {}
    except OSError:
        return None
    except ValueError:
        return "invalid"


def extension_path():
    """Citrello.mcpb, the one-click Claude Desktop extension, built next to Citrello.exe."""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(base, "Citrello.mcpb")
    return p if os.path.exists(p) else None


def extension_installed():
    """Best effort: has Claude Desktop unpacked a Citrello extension?"""
    for cfg in claude_config_paths():
        root = os.path.dirname(cfg)
        for sub in ("Claude Extensions", "extensions"):
            d = os.path.join(root, sub)
            if os.path.isdir(d) and any("citrello" in n.lower() for n in os.listdir(d)):
                return True
    return False


def claude_running():
    if not sys.platform.startswith("win"):
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq claude.exe", "/NH"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return "claude.exe" in out.lower()
    except Exception:
        return False


def remove_manual_connector():
    """An old hand-made "citrello" entry would run next to the extension: take it out (with a backup)."""
    removed = 0
    for path in claude_config_paths():
        cfg = _read_cfg(path)
        names = [n for n in ("citrello", "refcite") if isinstance(cfg, dict) and n in cfg.get("mcpServers", {})]
        if names:  # "refcite": Citrello's old name
            shutil.copyfile(path, path + ".citrello-backup")
            for n in names:
                del cfg["mcpServers"][n]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            removed += 1
    return removed


def install_extension():
    path = extension_path()
    if not path:
        raise ValueError("Citrello.mcpb was not found next to Citrello.exe. Run build_windows.bat again; it creates it in dist.")
    removed = remove_manual_connector()
    if sys.platform.startswith("win"):
        os.startfile(path)  # Claude Desktop opens its install window
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        raise ValueError("Opening the extension automatically works on Windows and macOS. Open Citrello.mcpb with Claude Desktop.")
    return {"opened": path, "removed_manual": removed}


def claude_status():
    cmd = connector_command()
    found = []
    for p in claude_config_paths():
        cfg = _read_cfg(p)
        entry = cfg.get("mcpServers", {}).get("citrello") if isinstance(cfg, dict) else None
        found.append({"path": p, "folder_exists": os.path.isdir(os.path.dirname(p)),
                      "connected": bool(entry), "up_to_date": entry == cmd})
    used = [f for f in found if f["folder_exists"]] or found[:1]
    return {"config_path": claude_config_path(), "config_paths": found,
            "extension_file": extension_path(), "extension_installed": extension_installed(),
            "claude_running": claude_running(),
            "claude_installed": any(f["folder_exists"] for f in found),
            "connected": all(f["connected"] for f in used), "up_to_date": all(f["up_to_date"] for f in used),
            "connector_exists": os.path.exists(cmd["command"]) or not getattr(sys, "frozen", False),
            "snippet": json.dumps({"mcpServers": {"citrello": cmd}}, indent=2)}


def claude_connect():
    """Add Citrello to every Claude Desktop settings file on this computer (backing each one up)."""
    targets = [p for p in claude_config_paths() if os.path.isdir(os.path.dirname(p))] or claude_config_paths()[:1]
    for path in targets:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cfg = _read_cfg(path)
        if cfg == "invalid":
            raise ValueError("Claude's settings file is not valid JSON, so it was left untouched: " + path)
        if cfg is None:
            cfg = {}
        else:
            shutil.copyfile(path, path + ".citrello-backup")
        cfg.setdefault("mcpServers", {})["citrello"] = connector_command()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    return claude_status()


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Citrello"

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, url):
        self.send(302, b"", "text/plain", {"Location": url})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def json_body(self):
        try:
            return json.loads(self.body() or b"{}")
        except ValueError:
            return {}

    def same_origin(self):
        """Refuse API writes coming from other web sites."""
        origin = self.headers.get("Origin")
        return origin is None or origin in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")

    # ---------------- GET ----------------
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path, qs = url.path, urllib.parse.parse_qs(url.query)
        if path == "/":
            with open(resource("ui.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/clip":  # the browser button's pop-up
            with open(resource("clip.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/status":
            cfg = load_config()
            name = profile_name() if logged_in() else None
            return self.send(200, {"version": VERSION, "configured": bool(cfg.get("client_id") and cfg.get("client_secret")),
                                   "connected": bool(name), "name": name, "email": cfg.get("email", ""),
                                   "style": cfg.get("style", citeformat.DEFAULT_STYLE),
                                   "zotero_user": cfg.get("zotero_user", ""), "has_zotero_key": bool(cfg.get("zotero_key"))})
        if path == "/login":
            cfg = load_config()
            state = secrets.token_urlsafe(16)
            OAUTH_STATES.add(state)
            return self.redirect(core.API + "/oauth/authorize?" + urllib.parse.urlencode(
                {"client_id": cfg.get("client_id", ""), "redirect_uri": REDIRECT,
                 "response_type": "code", "scope": "all", "state": state}))
        if path == "/callback":
            state = qs.get("state", [""])[0]
            if "code" not in qs or state not in OAUTH_STATES:
                msg = qs.get("error_description", qs.get("error", ["Login was cancelled."]))[0]
                return self.redirect("/?login_error=" + urllib.parse.quote(msg))
            OAUTH_STATES.discard(state)
            cfg = load_config()
            try:
                core._token_request({"grant_type": "authorization_code", "code": qs["code"][0],
                                        "redirect_uri": REDIRECT}, cfg["client_id"], cfg["client_secret"])
            except BaseException as e:
                return self.redirect("/?login_error=" + urllib.parse.quote(str(e)))
            return self.redirect("/?connected=1")
        m = re.fullmatch(r"/api/verify/(\w+)", path)
        if m and m.group(1) in VERIFY:
            return self.send(200, VERIFY[m.group(1)])
        if path == "/api/library/search.txt":  # for the Word button's "Insert citation"
            data = library.load()
            hits = library.search(data, qs.get("q", [""])[0])[:10]  # Word's InputBox shows ~1000 characters
            return self.send(200, "\n".join(f"{it['key']}|{library.short_ref(it)[:75]}" for it in hits),
                             "text/plain; charset=utf-8")
        if path == "/api/library/search.tsv":  # the Word tab's citation dialog
            data = library.load()
            n = max(1, min(int(qs.get("n", ["40"])[0] or 40), 5000))
            rows = []
            for it in library.search(data, qs.get("q", [""])[0])[:n]:
                c = it["csl"]
                mark = {"problem": "⚠ ", "check": ""}.get(it.get("reliability"), "")
                where = c.get("container-title") or c.get("publisher") or ""
                text = f"{mark}{library.short_ref(it)}" + (f" — {where}" if where else "")
                rows.append(it["key"] + "\t" + text.replace("\t", " ").replace("\n", " ")[:180])
            return self.send(200, "\n".join(rows), "text/plain; charset=utf-8")
        if path == "/api/ping":
            return self.send(200, "ok", "text/plain; charset=utf-8")
        if path == "/api/styles/check":  # Word's style dialog: is this a usable style id?
            try:
                info = citeformat.style_info(citeformat.style_path(qs.get("id", [""])[0]))
                if info["format"] == "note":
                    raise ValueError("Footnote styles are not supported yet.")
            except Exception as e:
                return self.send(400, str(e), "text/plain; charset=utf-8")
            return self.send(200, info["title"], "text/plain; charset=utf-8")
        if path == "/api/styles.txt":  # for the Word button
            lines = [f"{s['id']}|{s['title']}" for s in citeformat.list_styles()]
            return self.send(200, "\n".join(lines), "text/plain; charset=utf-8")
        if path == "/api/word":
            return self.send(200, word_addin.status())
        if path == "/api/word/Citrello.bas":
            return self.send(200, word_addin.bas_file().encode("cp1252", "replace"), "application/octet-stream",
                             {"Content-Disposition": "attachment; filename=Citrello.bas"})
        if path == "/api/library":
            data = library.load()
            q, project = qs.get("q", [""])[0], qs.get("project", [""])[0]
            items = library.search(data, q, None if project in ("", "*", "-") else project)
            if project == "-":
                items = [it for it in items if not it.get("projects")]
            return self.send(200, {"total": len(data["items"]), "projects": data["projects"],
                                   "path": library.library_path(), "items": [item_public(it) for it in items]})
        if path == "/api/library/export":
            data = library.load()
            project = qs.get("project", [""])[0]
            items = library.search(data, qs.get("q", [""])[0], None if project in ("", "*", "-") else project)
            fmt = qs.get("fmt", ["ris"])[0]
            fd, tmp = tempfile.mkstemp(suffix="." + fmt)
            os.close(fd)
            library.export(items, fmt, tmp)
            with open(tmp, "rb") as f:
                body = f.read()
            os.remove(tmp)
            name = f"citrello_{(project if project not in ('', '*', '-') else 'library')}.{fmt}"
            name = re.sub(r"[^\w\-. ]", "_", name)
            return self.send(200, body, "application/octet-stream",
                             {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(name)})
        if path == "/api/styles":
            return self.send(200, {"styles": citeformat.list_styles(),
                                   "default": load_config().get("style", citeformat.DEFAULT_STYLE)})
        if path == "/api/claude":
            return self.send(200, claude_status())
        m = re.fullmatch(r"/api/job/(\w+)", path)
        if m and m.group(1) in JOBS:
            return self.send(200, JOBS[m.group(1)].public())
        m = re.fullmatch(r"/api/job/(\w+)/file/([^/]+)", path)
        if m and m.group(1) in JOBS:
            job, name = JOBS[m.group(1)], urllib.parse.unquote(m.group(2))
            allowed = set(job.result["files"].values()) if job.result else set()
            if job.check_csv:
                allowed.add(job.check_csv)
            if name in allowed:
                with open(os.path.join(job.dir, name), "rb") as f:
                    data = f.read()
                ctype = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                         if name.endswith(".docx") else "application/octet-stream")
                disp = "attachment; filename*=UTF-8''" + urllib.parse.quote(name)
                return self.send(200, data, ctype, {"Content-Disposition": disp})
        self.send(404, {"error": "not found"})

    # ---------------- library / documents / connections ----------------
    def library_post(self, path):
        try:
            if path == "/api/library/add":
                b = self.json_body()
                results = []
                for line in [l.strip() for l in b.get("text", "").splitlines() if l.strip()]:
                    try:
                        it, created, how = library.add_reference(line, (b.get("project") or "").strip() or None,
                                                                 email=load_config().get("email"))
                        results.append({"ok": True, "created": created, "how": how, "item": item_public(it)})
                    except Exception as e:
                        results.append({"ok": False, "text": line[:120], "error": str(e)})
                return self.send(200, {"results": results})
            if path == "/api/clip/preview":  # browser button: what is this page?
                b = self.json_body()
                rec = library.resolve_clip(str(b.get("url", ""))[:2000], str(b.get("title", ""))[:500],
                                           b.get("meta") if isinstance(b.get("meta"), dict) else {},
                                           str(b.get("sel", ""))[:3000], load_config().get("email"))
                data = library.load()
                existing, _ = library.find_duplicate(data, rec["csl"]) if rec["csl"].get("title") else (None, None)
                return self.send(200, {"rec": {k: rec.get(k) for k in ("csl", "checks", "reliability", "source", "raw")},
                                       "existing": existing["key"] if existing else None, "projects": data["projects"]})
            if path == "/api/clip/add":  # browser button: the user clicked "Add"
                b = self.json_body()
                rec = b.get("rec") or {}
                if not isinstance(rec.get("csl"), dict) or not rec["csl"].get("title"):
                    return self.send(400, {"error": "Nothing to add."})
                with library.editing() as data:
                    it, created, _ = library.add_csl(data, rec["csl"], (b.get("project") or "").strip() or None,
                                                     str(rec.get("source") or "web"), raw=str(rec.get("raw") or "")[:2000],
                                                     extra={"checks": rec.get("checks") or [],
                                                            "reliability": rec.get("reliability") or "check"})
                    key = it["key"]
                return self.send(200, {"key": key, "created": created})
            if path == "/api/library/delete":
                return self.send(200, {"deleted": library.delete(self.json_body().get("keys", []))})
            if path == "/api/library/projects":
                b = self.json_body()
                return self.send(200, item_public(library.set_projects(b["key"], b.get("projects", []))))
            if path == "/api/library/import":
                name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
                project = urllib.parse.unquote(self.headers.get("X-Project", "")).strip() or None
                text = self.body().decode("utf-8-sig", errors="replace")
                added, skipped = library.import_items(library.parse_any(text, name), project, "import")
                return self.send(200, {"added": added, "skipped": skipped})
            if path == "/api/library/import-mendeley":
                if not logged_in():
                    return self.send(400, {"error": "Connect your Mendeley account first (Settings)."})
                added, skipped = library.import_from_mendeley(self.json_body().get("project") or None)
                return self.send(200, {"added": added, "skipped": skipped})
            if path == "/api/library/export-mendeley":
                if not logged_in():
                    return self.send(400, {"error": "Connect your Mendeley account first (Settings)."})
                b = self.json_body()
                data = library.load()
                items = library.search(data, b.get("q", ""), b.get("project") or None)
                added, present = library.export_to_mendeley(items)
                return self.send(200, {"added": added, "present": present})
            if path == "/api/library/import-zotero":
                b = self.json_body()
                cfg = load_config()
                user, key = (b.get("user") or cfg.get("zotero_user", "")).strip(), (b.get("key") or cfg.get("zotero_key", "")).strip()
                if not (user and key):
                    return self.send(400, {"error": "Enter your Zotero user ID and API key."})
                save_user_config(zotero_user=user, zotero_key=key)
                added, skipped = library.import_from_zotero(user, key, b.get("project") or None)
                return self.send(200, {"added": added, "skipped": skipped})
            if path == "/api/doc/update":
                data = self.body()
                if not data.startswith(b"PK"):
                    return self.send(400, {"error": "That file is not a Word .docx document."})
                job = Job(urllib.parse.unquote(self.headers.get("X-Filename", "document.docx")), data)
                JOBS[job.id] = job
                style = urllib.parse.unquote(self.headers.get("X-Style", "")) or load_config().get("style")
                out = os.path.splitext(job.filename)[0] + "_formatted.docx"
                res = citeformat.update_document(job.docx, os.path.join(job.dir, out), style)
                job.state = "done"
                job.result = {"files": {"docx": out}, "citations": res["citations"], "markers": res["markers"],
                              "entries": res["entries"], "style": res["style"]["title"], "report": res["report"]}
                return self.send(200, {"id": job.id, "result": job.result})
            if path == "/api/styles/add":
                sid = self.json_body().get("id", "").strip()
                info = citeformat.style_info(citeformat.style_path(sid))  # downloads it once
                if info["format"] == "note":
                    return self.send(400, {"error": "Footnote styles are not supported yet."})
                save_user_config(style=os.path.basename(citeformat.style_path(sid))[:-4])
                return self.send(200, info)
            if path == "/api/library/verify":
                b = self.json_body()
                data = library.load()
                project = b.get("project") or ""
                items = library.search(data, b.get("q", ""), None if project in ("", "*", "-") else project)
                if project == "-":
                    items = [it for it in items if not it.get("projects")]
                keys = b.get("keys") or [it["key"] for it in items]
                if not keys:
                    return self.send(400, {"error": "No references to verify."})
                return self.send(200, {"id": start_verify(keys, bool(b.get("include_verified")))})
            if path == "/api/doc/update-file":  # called by the Word button
                b = self.json_body()
                try:
                    msg, _ = word_addin.update_file(b.get("path", ""), (b.get("style") or "").strip() or None,
                                                    load_config().get("style"))
                except Exception as e:
                    traceback.print_exc()
                    return self.send(400, str(e), "text/plain; charset=utf-8")
                return self.send(200, msg, "text/plain; charset=utf-8")
            if path.startswith("/api/word/") and path in ("/api/word/cite", "/api/word/marker",
                                                           "/api/word/items", "/api/word/render"):
                b = self.json_body()
                try:
                    if path == "/api/word/cite":
                        group = [{"key": str(g.get("key", "")), "locator": (g.get("locator") or "").strip() or None,
                                  "label": "page", "prefix": (g.get("prefix") or "").strip() or None,
                                  "suppress": bool(g.get("suppress"))} for g in b.get("items", []) if g.get("key")]
                        if not group:
                            raise ValueError("Choose at least one reference.")
                        out = citeformat.code_for_group(group)
                    elif path == "/api/word/marker":
                        m = citeformat.MARKER_RE.fullmatch(b.get("text", "").strip())
                        group = citeformat.parse_marker(m.group(1)) if m else None
                        if not group:
                            raise ValueError(f"Not a citation marker: {b.get('text', '')!r}")
                        out = citeformat.code_for_group(group)
                    elif path == "/api/word/items":
                        out = "\n".join("\t".join(x.replace("\t", " ") for x in row)
                                        for row in citeformat.items_from_code(b.get("code", "")))
                    else:
                        style = (b.get("style") or "").strip() or load_config().get("style")
                        out = citeformat.render_lines(citeformat.render_codes(b.get("codes", []), style))
                except Exception as e:
                    traceback.print_exc()
                    return self.send(400, str(e), "text/plain; charset=utf-8")
                return self.send(200, out, "text/plain; charset=utf-8")
            if path == "/api/word/install":
                return self.send(200, word_addin.install())
            if path == "/api/word/remove":
                return self.send(200, word_addin.remove())
            if path == "/api/claude/install-extension":
                return self.send(200, install_extension())
            if path == "/api/claude/connect":
                return self.send(200, claude_connect())
        except Exception as e:
            traceback.print_exc()
            return self.send(400, {"error": str(e)})
        return None

    # ---------------- POST ----------------
    def do_POST(self):
        if not self.same_origin():
            return self.send(403, {"error": "forbidden"})
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/config":
            b = self.json_body()
            save_user_config(**{k: (b.get(k) or "").strip() for k in
                                ("client_id", "client_secret", "email", "style", "zotero_user", "zotero_key") if k in b})
            return self.send(200, {"ok": True})
        if path == "/api/logout":
            with contextlib.suppress(OSError):
                os.remove(core.TOKEN_FILE)
            return self.send(200, {"ok": True})
        if path == "/api/upload":
            data = self.body()
            if not data.startswith(b"PK"):
                return self.send(400, {"error": "That file is not a Word .docx document."})
            name = urllib.parse.unquote(self.headers.get("X-Filename", "article.docx"))
            job = Job(name, data)
            JOBS[job.id] = job
            job.run(analyse, bool(self.headers.get("X-Offline")))
            return self.send(200, {"id": job.id})
        m = re.fullmatch(r"/api/job/(\w+)/convert", path)
        if m and m.group(1) in JOBS:
            job, b = JOBS[m.group(1)], self.json_body()
            if job.state not in ("ready", "done"):
                return self.send(409, {"error": "busy"})
            push = bool(b.get("push")) and logged_in()
            styles = [s for s in b.get("styles", ["bracket", "superscript"])
                      if s in ("bracket", "superscript", "paren")] or ["bracket"]
            job.run(convert, push, styles, b.get("style"), (b.get("project") or "").strip() or None)
            return self.send(200, {"ok": True})
        handled = self.library_post(path)
        if handled is not None:
            return handled
        if path == "/api/quit":
            self.send(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description="Citrello app")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    library.migrate_from_refcite()
    url = f"http://localhost:{PORT}/"
    server = None
    for attempt in range(20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
            break
        except OSError:
            if attempt == 0:  # an older copy is still running: ask it to quit, then take its place
                with contextlib.suppress(Exception):
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                    opener.open(urllib.request.Request(url + "api/quit", data=b"", method="POST"), timeout=3)
            time.sleep(0.5)
    if server is None:  # port held by something that would not quit
        webbrowser.open(url)
        return
    print(f"Citrello is running at {url}  (close it with the Quit button on the page)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    finally:
        for job in JOBS.values():
            shutil.rmtree(job.dir, ignore_errors=True)


if __name__ == "__main__":
    main()
