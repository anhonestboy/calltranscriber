#!/usr/bin/env python3
"""Genera un'icona PNG 512×512 per CallTranscriber."""
import sys
from PIL import Image, ImageDraw

size = 1024  # retina
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Sfondo: cerchio gradiente blu (approssimato con cerchi concentrici)
center = size // 2
for i in range(center - 20, 0, -1):
    r = int(25 + (200 - 25) * (i / (center - 20)))
    g = int(60 + (180 - 60) * (i / (center - 20)))
    b = int(170 + (255 - 170) * (i / (center - 20)))
    alpha = 255
    draw.ellipse(
        [center - i, center - i, center + i, center + i],
        fill=(r, g, b, alpha),
    )

# Microfono bianco
m = size // 64  # multiplier
cx = center
cy = center - 3 * m

# Corpo microfono
body_w = 14 * m
body_h = 24 * m
draw.rounded_rectangle(
    [cx - body_w, cy - body_h, cx + body_w, cy + body_h],
    fill=(255, 255, 255, 255),
    radius=3 * m,
)

# Capsula arrotondata in cima
draw.rounded_rectangle(
    [cx - body_w - 2 * m, cy - body_h - 8 * m, cx + body_w + 2 * m, cy - body_h],
    fill=(255, 255, 255, 255),
    radius=5 * m,
)

# Stand
stand_w = 5 * m
stand_h = 14 * m
draw.rectangle(
    [cx - stand_w, cy + body_h, cx + stand_w, cy + body_h + stand_h],
    fill=(255, 255, 255, 255),
)

# Arco base
base_y = cy + body_h + stand_h
draw.arc(
    [cx - 20 * m, base_y - 8 * m, cx + 20 * m, base_y + 8 * m],
    -60, 240,
    fill=(255, 255, 255, 255),
    width=5 * m,
)

# Salva
out = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
img.save(out, "PNG")
print(f"Icona salvata: {out}")
