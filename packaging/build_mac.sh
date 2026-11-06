#!/bin/bash
# Build Citrello for macOS: dist/Citrello.app (with the connector inside) and dist/Citrello-mac.zip.
# Run on a Mac from the repository folder:
#   bash packaging/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build dist
python3 -m pip install --upgrade python-docx citeproc-py pypdf pybtex pyinstaller pillow certifi

python3 packaging/build_style_catalog.py || echo "Using the committed styles/catalog.json.gz"
python3 brand_icon.py build-icon.png 1024 >/dev/null
python3 brand_icon.py mcpb-icon.png 512 >/dev/null
VERSION=$(python3 -c "import citrello_core; print(citrello_core.VERSION)")

# The app: a windowed .app bundle (it opens its interface in the browser)
python3 -m PyInstaller --noconfirm --windowed --name Citrello --icon build-icon.png \
  --osx-bundle-identifier app.citrello.Citrello \
  --add-data "ui.html:." --add-data "clip.html:." --add-data "mcpb-icon.png:." --add-data "styles:styles" \
  --collect-data docx --collect-data citeproc --hidden-import certifi --collect-data certifi app.py

# The connector Claude Desktop, Claude Code and Codex start
python3 -m PyInstaller --noconfirm --onefile --console --name CitrelloMCP \
  --add-data "styles:styles" --collect-data docx --collect-data citeproc \
  --hidden-import certifi --collect-data certifi mcp_server.py

APP=dist/Citrello.app
cp dist/CitrelloMCP "$APP/Contents/MacOS/CitrelloMCP"  # the Claude extension is built from it on the user's Mac
plutil -replace CFBundleShortVersionString -string "$VERSION" "$APP/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$APP/Contents/Info.plist"
# No Dock icon: Citrello lives in the browser and is closed with its Quit button
plutil -replace LSUIElement -bool true "$APP/Contents/Info.plist"
# Re-seal the bundle after adding files (ad-hoc signature; Developer ID signing comes later)
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

(cd dist && ditto -c -k --keepParent Citrello.app Citrello-mac.zip)
rm -f build-icon.png mcpb-icon.png
echo "Built dist/Citrello.app and dist/Citrello-mac.zip (version $VERSION)"
