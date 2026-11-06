#!/usr/bin/env python3
"""
Prepare the in-browser paper checker (citrello.app/check).

The checker runs Citrello's own Python code in the visitor's browser with Pyodide, so the paper never leaves
their computer. This copies the modules it needs, the bundled citation styles and the pure-Python wheels into
site/public/check-py/, with a manifest the page reads.

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
    sys.path.insert(0, ROOT)
    import citrello_core
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump({"version": citrello_core.VERSION, "modules": MODULES, "styles": styles, "wheels": wheels}, f, indent=1)
    print(f"Web checker ready in {OUT}: {len(MODULES)} modules, {len(styles)} styles, {len(wheels)} wheels")


if __name__ == "__main__":
    main()
