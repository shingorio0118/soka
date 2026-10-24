#!/usr/bin/env python3
"""Render the whole map (assets/map.json + tiles.png) to one PNG for review.
Usage: python3 tools/render_overview.py out.png [scale] [--solid] [--crop x0,y0,x1,y1]   (crop in tile units)"""
import json, sys
from pathlib import Path
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parent.parent
m = json.loads((ROOT / "assets/map.json").read_text())
atlas = Image.open(ROOT / "assets/tiles.png").convert("RGBA")
cols = 20; T = 16
def tile(i): return atlas.crop(((i % cols) * T, (i // cols) * T, (i % cols) * T + T, (i // cols) * T + T))
def unrle(a):
    out = []
    for i in range(0, len(a), 2): out.extend([a[i]] * a[i + 1])
    return out
W, H = m["w"], m["h"]
base, deco, over = (unrle(m["layers"][k]) for k in ("base", "deco", "over")); solid = unrle(m["solid"])
x0, y0, x1, y1 = 0, 0, W, H
if "--crop" in sys.argv:
    x0, y0, x1, y1 = [int(v) for v in sys.argv[sys.argv.index("--crop") + 1].split(",")]
cache = {}
def T_(i):
    if i not in cache: cache[i] = tile(i)
    return cache[i]
img = Image.new("RGBA", ((x1 - x0) * T, (y1 - y0) * T))
def layer(arr):
    for y in range(y0, y1):
        for x in range(x0, x1):
            v = arr[y * W + x]
            if v >= 0: img.alpha_composite(T_(v), ((x - x0) * T, (y - y0) * T))
layer(base); layer(deco)
signs = Image.open(ROOT / "assets/signs.png").convert("RGBA") if (ROOT / "assets/signs.png").exists() else None
soka = Image.open(ROOT / "assets/sign_soka.png").convert("RGBA")
for o in m["objects"]:
    spr = soka if o["sprite"] == "sign_soka" else signs.crop((o["sx"], o["sy"], o["sx"] + o["w"] * T, o["sy"] + o["h"] * T))
    img.alpha_composite(spr, ((o["x"] - x0) * T, (o["y"] - y0) * T))
layer(over)
if "--solid" in sys.argv:
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    for y in range(y0, y1):
        for x in range(x0, x1):
            if solid[y * W + x] == 1: d.rectangle(((x - x0) * T, (y - y0) * T, (x - x0) * T + T - 1, (y - y0) * T + T - 1), fill=(255, 0, 0, 255))
    img = ov if "--solid-only" in sys.argv else (img.alpha_composite(ov) or img)
s = m["spawn"]; d = ImageDraw.Draw(img)
d.rectangle(((s["x"] - x0) * T + 2, (s["y"] - y0) * T + 2, (s["x"] - x0) * T + 13, (s["y"] - y0) * T + 13), outline=(255, 0, 255, 255), width=2)
scale = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 1
img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
img.convert("RGB").save(sys.argv[1])
print(img.size)
