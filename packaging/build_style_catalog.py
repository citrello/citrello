"""Build styles/catalog.json.gz: every citation style in the CSL style repository (id, title, short title,
citation format), so the app can search all of them offline and download only the one a person picks.

    python packaging/build_style_catalog.py              # clones the repository into a temporary folder
    python packaging/build_style_catalog.py path/to/styles   # or uses an existing clone
"""
import glob
import gzip
import html
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "styles", "catalog.json.gz")
REPO = "https://github.com/citation-style-language/styles.git"
PARENT = re.compile(r'<link\b[^>]*rel="independent-parent"[^>]*>')


def info(path):
    head = open(path, encoding="utf-8").read(10000)
    title = re.search(r"<title>([^<]+)</title>", head)
    short = re.search(r"<title-short>([^<]+)</title-short>", head)
    fmt = re.search(r'citation-format="([^"]+)"', head)
    link = PARENT.search(head)
    parent = re.search(r'href="https?://www\.zotero\.org/styles/([^"]+)"', link.group(0)) if link else None
    return {"title": html.unescape(title.group(1)).strip() if title else "",
            "short": html.unescape(short.group(1)).strip() if short else "",
            "format": fmt.group(1) if fmt else "", "parent": parent.group(1) if parent else ""}


def build(repo):
    styles = {}
    for p in glob.glob(os.path.join(repo, "*.csl")):
        styles[os.path.basename(p)[:-4]] = info(p)
    for p in glob.glob(os.path.join(repo, "dependent", "*.csl")):
        sid = os.path.basename(p)[:-4]
        if sid not in styles:
            styles[sid] = info(p)
    rows = []
    for sid, s in sorted(styles.items()):
        fmt = s["format"] or styles.get(s["parent"], {}).get("format") or "author-date"
        rows.append([sid, s["title"] or sid, s["short"], fmt])
    return rows


def main():
    if len(sys.argv) > 1:
        repo = sys.argv[1]
        rows = build(repo)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "clone", "-q", "--depth", "1", REPO, tmp], check=True)
            rows = build(tmp)
    data = json.dumps({"columns": ["id", "title", "short", "format"], "styles": rows},
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(OUT, "wb", mtime=0) as f:
        f.write(data)
    usable = sum(1 for r in rows if r[3] != "note")
    print(f"{len(rows)} styles ({usable} usable, {len(rows) - usable} footnote) -> {OUT} "
          f"({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
