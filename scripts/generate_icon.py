#!/usr/bin/env python3
"""Genera icone minimali per CallTranscriber — solo cerchio."""
from PIL import Image, ImageDraw
import sys, os

OUT = sys.argv[1] if len(sys.argv) > 1 else "."

def make_circle(filename: str, badge: bool = False):
    s = 128
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx, cy = s // 2, s // 2
    r = s // 2 - 4

    # Cerchio nero (bordo)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(0, 0, 0, 255))

    # Interno trasparente per effetto bucato
    inner = r - 5
    draw.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=(0, 0, 0, 0))

    if badge:
        bx, by = s - 16, 16
        draw.ellipse([bx - 10, by - 10, bx + 10, by + 10], fill=(251, 146, 60))
        draw.ellipse([bx - 10, by - 10, bx + 10, by + 10], outline=(0, 0, 0, 255), width=2)

    img.save(os.path.join(OUT, filename), "PNG")
    print(f"  ✓ {filename}")

make_circle("icon.png")
make_circle("icon_processing.png", badge=True)
print("Done.")
