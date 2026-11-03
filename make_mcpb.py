#!/usr/bin/env python3
"""
Build Citrello.mcpb - the one-click Claude Desktop extension (MCP Bundle).

Double-clicking the .mcpb opens Claude Desktop's install dialog, so nobody has to
edit claude_desktop_config.json. An .mcpb is a zip with manifest.json at its root.

  python make_mcpb.py dist\\CitrelloMCP.exe dist\\Citrello.mcpb
"""
import json
import os
import struct
import sys
import zipfile
import zlib



def _icon_png(size=512):
    """A simple Citrello icon (white "R" on a red rounded square), drawn without extra libraries."""
    glyph = ["11110", "10001", "10001", "11110", "10100", "10010", "10001"]  # 5x7 letter R
    red, white = (155, 28, 49, 255), (255, 255, 255, 255)
    cell, gx, gy = size // 11, None, None
    gx, gy = (size - 5 * cell) // 2, (size - 7 * cell) // 2
    radius = size // 5
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter byte
        for x in range(size):
            # rounded-square mask
            dx = max(radius - x, 0, x - (size - 1 - radius))
            dy = max(radius - y, 0, y - (size - 1 - radius))
            if dx * dx + dy * dy > radius * radius:
                px = (0, 0, 0, 0)
            else:
                cx, cy = (x - gx) // cell, (y - gy) // cell
                inside = 0 <= cx < 5 and 0 <= cy < 7 and x >= gx and y >= gy and glyph[cy][cx] == "1"
                px = white if inside else red
            row.extend(px)
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def manifest(tools):
    return {
        "manifest_version": "0.3",
        "name": "citrello",
        "display_name": "Citrello",
        "version": _version(),
        "description": "Your reference library in every Claude chat: search, add, check and cite references, "
                       "and write Word documents with live citations.",
        "long_description": (
            "Citrello is a Claude-friendly reference manager. Its library lives on your computer and is shared "
            "by the Citrello app and Claude, so the references you use are the same in every chat and project.\n\n"
            "Claude can search the library, add references from a DOI, a link or a pasted reference (looked up "
            "on Crossref, duplicates skipped, retracted papers and wrong DOIs flagged), organise them into "
            "projects, format them in any of ~10,000 journal styles, and create or restyle Word documents with "
            "live, Mendeley-compatible citations. Install the Citrello app for the library window, the Word tab "
            "and the browser button."),
        "author": {"name": "Citrello"},
        "icon": "icon.png",
        "server": {
            "type": "binary",
            "entry_point": "server/CitrelloMCP.exe",
            "mcp_config": {"command": "${__dirname}/server/CitrelloMCP.exe", "args": [], "env": {}},
        },
        "tools": tools,
        "keywords": ["references", "citations", "bibliography", "word", "mendeley", "zotero", "academic"],
        "compatibility": {"claude_desktop": ">=0.10.0", "platforms": ["win32"]},
    }


def _version():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import citrello_core
    return citrello_core.VERSION


def tool_list():
    """Tool names and descriptions, taken from the connector itself."""
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    stdout = sys.stdout
    try:
        import mcp_server  # noqa: F401  (it redirects stdout while importing)
    finally:
        sys.stdout = stdout
    return [{"name": n, "description": d.split(". ")[0].rstrip(".") + "."} for n, (_, d, _) in mcp_server.TOOLS.items()]


def build(exe, out):
    if not os.path.isfile(exe):
        raise SystemExit(f"Connector not found: {exe}")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(tool_list()), indent=2))
        z.writestr("icon.png", _icon_png())
        z.write(exe, "server/CitrelloMCP.exe")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print("Built", build(sys.argv[1], sys.argv[2]))
