#!/usr/bin/env python3
"""
Build the Citrello browser extension (extension/) for the stores:

  dist/extension/chrome/   + dist/citrello-extension-chrome.zip    Chrome Web Store and Microsoft Edge Add-ons
  dist/extension/firefox/  + dist/citrello-extension-firefox.zip   Firefox Add-ons

  python packaging/build_extension.py            the store packages
  python packaging/build_extension.py --test DIR a Chrome copy for the tests: it also runs on a locally served
                                                 Citrello (localhost:8099) and already has access to all sites,
                                                 because tests can't click the browser's permission prompt
"""
import argparse
import json
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "extension")
FILES = ["background.js", "bridge.js", "grab.js", "fetch.html", "fetch.js", "options.html", "options.js"]


def icons(out):
    sys.path.insert(0, ROOT)
    import brand_icon
    os.makedirs(os.path.join(out, "icons"), exist_ok=True)
    for size in (16, 32, 48, 128):
        with open(os.path.join(out, "icons", f"{size}.png"), "wb") as f:
            f.write(brand_icon.png_bytes(size))


def build(out, kind, test=False):
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for f in FILES:
        shutil.copy2(os.path.join(SRC, f), out)
    icons(out)
    man = json.load(open(os.path.join(SRC, "manifest.json"), encoding="utf-8"))
    if kind == "firefox":
        man["background"] = {"scripts": ["grab.js", "background.js"]}
        man["browser_specific_settings"] = {"gecko": {"id": "extension@citrello.app", "strict_min_version": "128.0"}}
    if test:
        man["content_scripts"][0]["matches"] += ["http://localhost:8099/*", "http://127.0.0.1:8099/*"]
        man["host_permissions"] = ["<all_urls>"]
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1)
    return man


def zip_dir(src, path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, _, files in os.walk(src):
            for f in sorted(files):
                full = os.path.join(base, f)
                z.write(full, os.path.relpath(full, src))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", metavar="DIR")
    a = ap.parse_args()
    if a.test:
        build(a.test, "chrome", test=True)
        print(f"Test copy of the extension in {a.test}")
        return
    dist = os.path.join(ROOT, "dist")
    for kind in ("chrome", "firefox"):
        out = os.path.join(dist, "extension", kind)
        man = build(out, kind)
        zip_dir(out, os.path.join(dist, f"citrello-extension-{kind}.zip"))
    print(f"Citrello extension {man['version']}: dist/citrello-extension-chrome.zip, dist/citrello-extension-firefox.zip")


if __name__ == "__main__":
    main()
