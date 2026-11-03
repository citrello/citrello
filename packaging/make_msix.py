#!/usr/bin/env python3
"""
Build Citrello's Microsoft Store package (.msix) from the built .exe files.

  python packaging/make_msix.py dist

Reads dist/Citrello.exe, dist/CitrelloMCP.exe and dist/Citrello.mcpb and writes dist/Citrello-<version>.msix.
The package is unsigned: the Microsoft Store signs it. For a local test install, sign it with a test certificate.

The Store identity comes from Partner Center (Product management -> Product identity). Set it with:
  MSIX_IDENTITY_NAME     e.g. 12345ArminHosseinghorbani.Citrello
  MSIX_PUBLISHER         e.g. CN=AB12CD34-...
  MSIX_PUBLISHER_NAME    e.g. Armin Hosseinghorbani
Without them a placeholder identity is used (fine for checking the build, not for the Store).
"""
import glob
import os
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape, quoteattr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brand_icon  # noqa: E402

DESCRIPTION = ("Free reference manager for Claude, ChatGPT, Word and Overleaf. Your library stays on your computer, "
               "and every citation stays live and checked.")

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap3="http://schemas.microsoft.com/appx/manifest/uap/windows10/3"
  xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
  xmlns:desktop6="http://schemas.microsoft.com/appx/manifest/desktop/windows10/6"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap3 desktop desktop6 rescap">

  <Identity Name={name} Publisher={publisher} Version={version} ProcessorArchitecture="x64" />

  <Properties>
    <DisplayName>Citrello</DisplayName>
    <PublisherDisplayName>{publisher_name}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
    <!-- Citrello installs its Word tab in Word's own STARTUP folder and adds itself to Claude Desktop's and
         Codex's settings. Those files must be the real ones, not a private copy of the package. -->
    <desktop6:FileSystemWriteVirtualization>disabled</desktop6:FileSystemWriteVirtualization>
    <desktop6:RegistryWriteVirtualization>disabled</desktop6:RegistryWriteVirtualization>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>

  <Resources>
    <Resource Language="en-us" />
  </Resources>

  <Applications>
    <Application Id="Citrello" Executable="Citrello.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="Citrello" Description={description} BackgroundColor="transparent"
        Square150x150Logo="Assets\\Square150x150Logo.png" Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" Square71x71Logo="Assets\\Square71x71Logo.png"
          Square310x310Logo="Assets\\Square310x310Logo.png" ShortName="Citrello" />
      </uap:VisualElements>
      <Extensions>
        <!-- citrello-mcp.exe: how Claude Desktop, Claude Code and Codex start the connector of the Store version -->
        <uap3:Extension Category="windows.appExecutionAlias" Executable="CitrelloMCP.exe" EntryPoint="Windows.FullTrustApplication">
          <uap3:AppExecutionAlias>
            <desktop:ExecutionAlias Alias="citrello-mcp.exe" />
          </uap3:AppExecutionAlias>
        </uap3:Extension>
        <uap3:Extension Category="windows.appExecutionAlias" Executable="Citrello.exe" EntryPoint="Windows.FullTrustApplication">
          <uap3:AppExecutionAlias>
            <desktop:ExecutionAlias Alias="citrello.exe" />
          </uap3:AppExecutionAlias>
        </uap3:Extension>
      </Extensions>
    </Application>
  </Applications>

  <Capabilities>
    <Capability Name="internetClient" />
    <rescap:Capability Name="runFullTrust" />
    <rescap:Capability Name="unvirtualizedResources" />
  </Capabilities>
</Package>
"""

# Store tile and logo images (base size, plus 200% for sharp high-DPI screens)
SQUARES = {"Square44x44Logo": 44, "Square71x71Logo": 71, "Square150x150Logo": 150, "Square310x310Logo": 310,
           "StoreLogo": 50}


def msix_version(v):
    """1.0.0 -> 1.0.0.0 (the Store needs four parts, the last one 0)."""
    parts = [int(p) for p in v.split(".")[:3]] + [0, 0, 0]
    return ".".join(str(p) for p in parts[:3]) + ".0"


def write_assets(folder):
    os.makedirs(folder, exist_ok=True)
    for name, size in SQUARES.items():
        small = size <= 71
        for scale in (100, 200):
            px = size * scale // 100
            im = brand_icon.image(px, padding=0.04 if small else 0.12, rounded=True)
            suffix = "" if scale == 100 else f".scale-{scale}"
            im.save(os.path.join(folder, f"{name}{suffix}.png"))
    # taskbar and Start menu icon without the tile plate
    for t in (16, 24, 32, 48, 256):
        brand_icon.image(t).save(os.path.join(folder, f"Square44x44Logo.targetsize-{t}_altform-unplated.png"))
    for scale in (100, 200):
        w, h = 310 * scale // 100, 150 * scale // 100
        brand_icon.wide(w, h).save(os.path.join(folder, "Wide310x150Logo" + ("" if scale == 100 else ".scale-200") + ".png"))


def find_makeappx():
    found = shutil.which("makeappx") or shutil.which("makeappx.exe")
    if found:
        return found
    hits = sorted(glob.glob(r"C:\Program Files (x86)\Windows Kits\10\bin\10.*\x64\makeappx.exe"))
    return hits[-1] if hits else None


def build(dist):
    import citrello_core
    version = msix_version(citrello_core.VERSION)
    layout = os.path.join(dist, "msix")
    shutil.rmtree(layout, ignore_errors=True)
    os.makedirs(layout)
    for f in ("Citrello.exe", "CitrelloMCP.exe", "Citrello.mcpb"):
        src = os.path.join(dist, f)
        if not os.path.isfile(src):
            raise SystemExit(f"Missing {src}: build the .exe files and the .mcpb first.")
        shutil.copy2(src, layout)
    shutil.copy2(os.path.join(ROOT, "LICENSE"), os.path.join(layout, "LICENSE.txt"))
    write_assets(os.path.join(layout, "Assets"))
    manifest = MANIFEST.format(
        name=quoteattr(os.environ.get("MSIX_IDENTITY_NAME") or "Citrello.Citrello"),
        publisher=quoteattr(os.environ.get("MSIX_PUBLISHER") or "CN=Citrello"),
        publisher_name=escape(os.environ.get("MSIX_PUBLISHER_NAME") or "Citrello"),
        version=quoteattr(version), description=quoteattr(DESCRIPTION))
    with open(os.path.join(layout, "AppxManifest.xml"), "w", encoding="utf-8") as f:
        f.write(manifest)
    out = os.path.join(dist, f"Citrello-{version}.msix")
    tool = find_makeappx()
    if not tool:
        print(f"Package folder ready in {layout} (makeappx.exe not found: install the Windows SDK to pack it).")
        return layout
    subprocess.run([tool, "pack", "/o", "/d", layout, "/p", out], check=True)
    print("Built", out)
    return out


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist"))
