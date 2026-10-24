#!/usr/bin/env python3
"""Render the whole map (assets/map.json + tiles.png) to one PNG for review.
Usage: python3 tools/render_overview.py out.png [scale] [--solid]"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parent.parent
m = json.loads((ROOT / "assets/map.json").read_text())
atlas = Image.open(ROOT / "assets/tiles.png").convert("RGBA")
cols = 20; T = 16
def tile(i): return atlas.crop(((i % cols) * T, (i // cols) * T, (i % cols) * T + T, (i // cols) * T + T))
W, H = m["w"], m["h"]
img = Image.new("RGBA", (W * T, H * T))
for y in range(H):
    for x in range(W):
        b = m["layers"]["base"][y][x]
        if b >= 0: img.alpha_composite(tile(b), (x * T, y * T))
for y in range(H):
    for x in range(W):
        d = m["layers"]["deco"][y][x]
        if d >= 0: img.alpha_composite(tile(d), (x * T, y * T))
for o in m["objects"]:
    spr = Image.open(ROOT / "assets" / (o["sprite"] + ".png")).convert("RGBA")
    img.alpha_composite(spr, (o["x"] * T, o["y"] * T))
for y in range(H):
    for x in range(W):
        o = m["layers"]["over"][y][x]
        if o >= 0: img.alpha_composite(tile(o), (x * T, y * T))
if "--solid" in sys.argv:
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    for y in range(H):
        for x in range(W):
            if m["solid"][y][x] == "1": d.rectangle((x * T, y * T, x * T + T - 1, y * T + T - 1), fill=(255, 0, 0, 70))
    img.alpha_composite(ov)
s = m["spawn"]; d = ImageDraw.Draw(img)
d.rectangle((s["x"] * T + 2, s["y"] * T + 2, s["x"] * T + 13, s["y"] * T + 13), outline=(255, 0, 255, 255), width=2)
scale = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1
img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
img.convert("RGB").save(sys.argv[1])
print(img.size)
