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
    """Fallback icon without Pillow: a lemon disc on a cobalt rounded square, drawn without extra libraries."""
    cobalt, lemon, white = (29, 63, 143, 255), (246, 214, 70, 255), (255, 255, 255, 255)
    radius, c, r_out, r_in = size // 5, (size - 1) / 2, size * 0.34, size * 0.29
    rows = []
    for y in range(size):
        row = bytearray([0])  # filter byte
        for x in range(size):
            dx = max(radius - x, 0, x - (size - 1 - radius))
            dy = max(radius - y, 0, y - (size - 1 - radius))
            if dx * dx + dy * dy > radius * radius:
                px = (0, 0, 0, 0)
            else:
                d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
                px = lemon if d <= r_in else white if d <= r_out else cobalt
            row.extend(px)
        rows.append(bytes(row))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def _icon():
    """The Citrello lemon-slice icon (brand_icon.py, needs Pillow); a plain fallback without Pillow."""
    try:
        import brand_icon
        return brand_icon.png_bytes(512)
    except ImportError:
        return _icon_png()


def manifest(tools, platform="win32"):
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
            "entry_point": "server/" + _server_name(platform),
            "mcp_config": {"command": "${__dirname}/server/" + _server_name(platform), "args": [], "env": {}},
        },
        "tools": tools,
        "keywords": ["references", "citations", "bibliography", "word", "mendeley", "zotero", "academic"],
        "compatibility": {"claude_desktop": ">=0.10.0", "platforms": [platform]},
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


def _server_name(platform):
    return "CitrelloMCP.exe" if platform == "win32" else "CitrelloMCP"


def build(exe, out):
    """The platform follows the connector: CitrelloMCP.exe -> Windows, CitrelloMCP -> macOS."""
    if not os.path.isfile(exe):
        raise SystemExit(f"Connector not found: {exe}")
    platform = "win32" if exe.lower().endswith(".exe") else "darwin"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest(tool_list(), platform), indent=2))
        z.writestr("icon.png", _icon())
        info = zipfile.ZipInfo("server/" + _server_name(platform))
        info.external_attr = 0o755 << 16  # keep it executable on macOS
        info.compress_type = zipfile.ZIP_DEFLATED
        with open(exe, "rb") as f:
            z.writestr(info, f.read())
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print("Built", build(sys.argv[1], sys.argv[2]))
