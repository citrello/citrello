"""
The Citrello icon: a lemon slice on a cobalt tile (the website's colours).

  python brand_icon.py citrello.ico          # Windows icon for the .exe files
  python brand_icon.py icon.png 512          # one PNG

Needs Pillow. Drawn 4x larger, then scaled down, for smooth edges.
"""
import io
import math
import sys

COBALT, LEMON, WHITE, LEAF = (29, 63, 143), (246, 214, 70), (255, 255, 255), (62, 123, 79)


def image(size, padding=0.0, rounded=True):
    """A square icon. padding: empty margin as a fraction of the size (the Store's small tiles look better with some)."""
    from PIL import Image, ImageDraw
    s = size * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = int(s * padding)
    box = (pad, pad, s - pad - 1, s - pad - 1)
    side = s - 2 * pad
    if rounded:
        d.rounded_rectangle(box, radius=int(side * 0.22), fill=COBALT)
    else:
        d.rectangle(box, fill=COBALT)
    cx = cy = s / 2
    r = side * 0.34
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=WHITE)              # rind
    r2 = r * 0.86
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), fill=LEMON)           # flesh
    w = max(2, int(side * 0.028))
    for k in range(8):                                                     # segments
        a = math.pi * k / 4
        d.line((cx, cy, cx + r2 * math.cos(a), cy + r2 * math.sin(a)), fill=WHITE, width=w)
    rc = r * 0.16
    d.ellipse((cx - rc, cy - rc, cx + rc, cy + rc), fill=LEAF)            # centre
    return im.resize((size, size), Image.LANCZOS)


def wide(width, height):
    """A wide tile: the icon centred on cobalt."""
    from PIL import Image
    im = Image.new("RGBA", (width, height), COBALT + (255,))
    ic = image(int(height * 0.8), rounded=False)
    im.alpha_composite(ic, ((width - ic.width) // 2, (height - ic.height) // 2))
    return im


def png_bytes(size, **kw):
    buf = io.BytesIO()
    image(size, **kw).save(buf, "PNG")
    return buf.getvalue()


def save_ico(path):
    image(256).save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "citrello.ico"
    if out.lower().endswith(".ico"):
        save_ico(out)
    else:
        image(int(sys.argv[2]) if len(sys.argv) > 2 else 512).save(out)
    print("Wrote", out)
