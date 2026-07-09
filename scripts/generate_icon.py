#!/usr/bin/env python3
"""Genera le icone per CallTranscriber — idle e processing."""
from PIL import Image, ImageDraw
import sys, os

OUT = sys.argv[1] if len(sys.argv) > 1 else "."

def make_icon(filename: str, badge: tuple | None = None):
    s = 128  # retina size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    m = s / 64

    # Cerchio sfondo blu
    cx, cy = s // 2, s // 2
    r = s // 2 - 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(59, 130, 246, 255))

    # Microfono bianco
    mic_w = 7 * m
    mic_h = 11 * m
    hx, hy = cx, cy - 2 * m

    # Corpo
    draw.rounded_rectangle(
        [hx - mic_w, hy - mic_h, hx + mic_w, hy + mic_h],
        fill=(255, 255, 255, 255),
        radius=1.5 * m,
    )
    # Capsula top
    cap_w = mic_w + 1.5 * m
    draw.rounded_rectangle(
        [hx - cap_w, hy - mic_h - 4 * m, hx + cap_w, hy - mic_h + 1 * m],
        fill=(255, 255, 255, 255),
        radius=2.5 * m,
    )
    # Stand
    sw = 2.5 * m
    draw.rectangle(
        [hx - sw, hy + mic_h, hx + sw, hy + mic_h + 6 * m],
        fill=(255, 255, 255, 255),
    )
    # Arco base
    draw.arc(
        [hx - 10 * m, hy + mic_h, hx + 10 * m, hy + mic_h + 12 * m],
        -60, 240,
        fill=(255, 255, 255, 255),
        width=int(2.5 * m),
    )

    # Badge
    if badge:
        bx, by = s - 18, 18
        draw.ellipse([bx - 10, by - 10, bx + 10, by + 10], fill=badge)
        draw.ellipse([bx - 10, by - 10, bx + 10, by + 10], outline=(255, 255, 255, 255), width=2)

    img.save(os.path.join(OUT, filename), "PNG")
    print(f"  ✓ {filename}")

make_icon("icon.png")
make_icon("icon_processing.png", badge=(251, 146, 60))  # arancione
print("Done.")
