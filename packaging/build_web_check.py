#!/usr/bin/env python3
"""
Prepare the parts of citrello.app that run Citrello's own Python in the browser (Pyodide):

  /check  the paper checker: site/public/check-py/
  /app    the web app: the desktop interface (ui.html) as site/public/app/index.html, and the desktop app's code
          in site/public/app/py/ (webapp.py answers the interface's requests with app.py, inside the browser)

The paper and the library never leave the visitor's computer. This copies the modules, the bundled citation
styles (and BibTeX styles for arXiv) and the pure-Python wheels, with a manifest each page reads.

  python packaging/build_web_check.py
"""
import glob
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "site", "public", "check-py")
MODULES = ["doccheck.py", "citeformat.py", "citrello_core.py", "library.py", "latex.py"]
# pure-Python packages (lxml and typing-extensions come from Pyodide itself); pinned for reproducible builds
WHEELS = ["python-docx==1.2.0", "citeproc-py==0.12.1", "pypdf==6.17.0", "pyodide-http==0.2.2"]

APP_OUT = os.path.join(ROOT, "site", "public", "app")
# everything app.py imports, plus the web runtime
APP_MODULES = ["webapp.py", "app.py", "citrello_core.py", "library.py", "citeformat.py", "doccheck.py", "latex.py",
               "arxiv.py", "zotero_local.py", "bridge_client.py", "usage.py", "updater.py", "word_addin.py",
               "codex_config.py", "mcp_server.py"]
APP_WHEELS = WHEELS + ["pybtex==0.26.1", "latexcodec==3.0.1"]
# from Pyodide's own repository (compiled for the browser)
APP_PYODIDE = ["lxml", "micropip", "typing-extensions", "sqlite3", "ssl", "pyyaml"]


def _version():
    import re
    return re.search(r'^VERSION = "([^"]+)"', open(os.path.join(ROOT, "citrello_core.py"), encoding="utf-8").read(),
                     re.M).group(1)


def build_app():
    out = os.path.join(APP_OUT, "py")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for m in APP_MODULES:
        shutil.copy2(os.path.join(ROOT, m), out)
    styles = []
    for p in sorted(glob.glob(os.path.join(ROOT, "styles", "**", "*"), recursive=True)):
        rel = os.path.relpath(p, os.path.join(ROOT, "styles")).replace(os.sep, "/")
        if os.path.isfile(p) and not rel.endswith(".md"):
            os.makedirs(os.path.dirname(os.path.join(out, "styles", rel)), exist_ok=True)
            shutil.copy2(p, os.path.join(out, "styles", rel))
            styles.append(rel)
    wheel_dir = os.path.join(out, "wheels")
    subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:",
                    "--dest", wheel_dir] + APP_WHEELS, check=True)
    wheels = sorted(os.path.basename(p) for p in glob.glob(os.path.join(wheel_dir, "*.whl")))
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump({"version": _version(), "modules": APP_MODULES, "styles": styles, "wheels": wheels,
                   "pyodide_packages": APP_PYODIDE}, f, indent=1)
    # the interface: the desktop app's own page, plus web.js (sends its requests to the worker)
    with open(os.path.join(ROOT, "ui.html"), encoding="utf-8") as f:
        page = f.read()
    head = ('<meta name="description" content="Citrello in your browser: your reference library, checks and '
            'citation styles, with nothing to install.">\n'
            '<link rel="icon" href="/favicon.svg" type="image/svg+xml">\n<script src="web.js"></script>\n')
    assert page.count("</head>") == 1
    page = page.replace("</head>", head + "</head>")
    with open(os.path.join(APP_OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Web app ready in {APP_OUT}: {len(APP_MODULES)} modules, {len(styles)} style files, {len(wheels)} wheels")


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(os.path.join(OUT, "styles"))
    for m in MODULES:
        shutil.copy2(os.path.join(ROOT, m), OUT)
    styles = []
    for p in sorted(glob.glob(os.path.join(ROOT, "styles", "*.csl"))):
        shutil.copy2(p, os.path.join(OUT, "styles"))
        styles.append(os.path.basename(p))
    wheel_dir = os.path.join(OUT, "wheels")
    subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--no-deps", "--only-binary=:all:",
                    "--dest", wheel_dir] + WHEELS, check=True)
    wheels = sorted(os.path.basename(p) for p in glob.glob(os.path.join(wheel_dir, "*.whl")))
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump({"version": _version(), "modules": MODULES, "styles": styles, "wheels": wheels}, f, indent=1)
    print(f"Web checker ready in {OUT}: {len(MODULES)} modules, {len(styles)} styles, {len(wheels)} wheels")
    build_app()


if __name__ == "__main__":
    main()
