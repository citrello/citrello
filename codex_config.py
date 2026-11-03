"""
Connect Citrello to OpenAI Codex (app, CLI and IDE extension).

Codex runs local MCP servers listed in ~/.codex/config.toml (or $CODEX_HOME/config.toml):

    [mcp_servers.citrello]
    command = 'C:\\...\\CitrelloMCP.exe'
    args = []

This module adds or replaces that one table and leaves everything else in the file untouched.
"""
import json
import os
import re
import shutil

try:
    import tomllib
except ImportError:  # Python < 3.11: skip validation
    tomllib = None

SECTION = "citrello"


def config_path():
    home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(home, "config.toml")


def _toml_str(s):
    s = str(s)
    if "'" not in s and "\n" not in s:
        return "'" + s + "'"  # literal string: Windows backslashes need no escaping
    return json.dumps(s)      # basic string (JSON escaping is valid TOML here)


def _strip_section(text):
    """Remove [mcp_servers.citrello] and its sub-tables (e.g. [mcp_servers.citrello.env])."""
    out, skipping = [], False
    head = re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*" + SECTION + r"\s*(\.[^\]]*)?\]\s*(#.*)?$")
    for line in text.splitlines():
        if re.match(r"^\s*\[", line):
            skipping = bool(head.match(line))
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip() + "\n" if any(l.strip() for l in out) else ""


def block(entry):
    lines = ["[mcp_servers." + SECTION + "]", "command = " + _toml_str(entry["command"]),
             "args = [" + ", ".join(_toml_str(a) for a in entry.get("args", [])) + "]",
             "startup_timeout_sec = 30"]
    if entry.get("env"):
        lines.append("")
        lines.append("[mcp_servers." + SECTION + ".env]")
        lines += [f"{k} = {_toml_str(v)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def read_entry():
    p = config_path()
    if not (os.path.exists(p) and tomllib):
        return None
    try:
        with open(p, "rb") as f:
            return tomllib.load(f).get("mcp_servers", {}).get(SECTION)
    except (OSError, ValueError):
        return None


def status(entry):
    p = config_path()
    cur = read_entry()
    return {"config_path": p, "codex_found": os.path.isdir(os.path.dirname(p)),
            "connected": bool(cur), "up_to_date": bool(cur) and cur.get("command") == entry["command"]
            and list(cur.get("args", [])) == list(entry.get("args", [])), "snippet": block(entry)}


def connect(entry):
    """Add/replace Citrello's table in Codex's config.toml (with a backup). Returns status."""
    p = config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    old = open(p, encoding="utf-8").read() if os.path.exists(p) else ""
    if old:
        shutil.copyfile(p, p + ".citrello-backup")
    rest = _strip_section(old)
    new = (rest + "\n" if rest else "") + block(entry)
    if tomllib:
        try:
            tomllib.loads(new)
        except ValueError as e:
            raise ValueError(f"Codex's settings file has a mistake in it, so it was left untouched ({e}): {p}")
    with open(p, "w", encoding="utf-8") as f:
        f.write(new)
    return status(entry)
