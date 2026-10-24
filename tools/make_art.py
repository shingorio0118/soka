#!/usr/bin/env python3
"""Generate all pixel art for the Soka map by script (Pillow). Original art, no third-party assets.

Outputs (in assets/):
  tiles.png   16x16 tile atlas (20 columns)
  tiles.json  {"tile":16,"cols":20,"names":[...]}  index = position in the atlas
  player.png  4 directions (down,left,right,up) x 3 frames, cell 16x24
  signs.png   (written by tools/build_map.py through text_sign()) street-name plates
  sign_soka.png  3x2 tile signboard with the pixel text 草加駅 (rendered from Noto Sans CJK JP Bold, SIL OFL,
                 thresholded to 1-bit pixels; only the resulting bitmap is shipped)
Run: python3 tools/make_art.py
"""
import json, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
OUT.mkdir(exist_ok=True)
T = 16

def hexc(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)

def new(fill=None):
    return Image.new("RGBA", (T, T), hexc(fill) if fill else (0, 0, 0, 0))

def rect(im, x0, y0, x1, y1, c):
    ImageDraw.Draw(im).rectangle((x0, y0, x1, y1), fill=hexc(c) if isinstance(c, str) else c)

def px(im, x, y, c):
    if 0 <= x < im.width and 0 <= y < im.height:
        im.putpixel((x, y), hexc(c) if isinstance(c, str) else c)

def speckle(im, rng, colors, n):
    for _ in range(n):
        px(im, rng.randrange(T), rng.randrange(T), rng.choice(colors))

# ---------------------------------------------------------------- ground tiles
def t_ground(seed):
    r = random.Random(seed); im = new("#d1ccbb")
    speckle(im, r, ["#c6c1b0", "#dad5c6", "#c1bba9"], 22)
    if seed % 3 == 2:
        for _ in range(3):
            x, y = r.randrange(2, 14), r.randrange(2, 13)
            px(im, x, y, "#8fae6a"); px(im, x + 1, y - 1, "#7d9d5a"); px(im, x - 1, y - 1, "#7d9d5a")
    return im

def t_sidewalk(seed):
    r = random.Random(seed); im = new("#dcd7c8")
    # paver joints, staggered
    for y in range(0, T, 8):
        rect(im, 0, y, T - 1, y, "#c3beae")
        off = 0 if (y // 8) % 2 == 0 else 4
        for x in range(off, T, 8):
            rect(im, x, y, x, y + 7, "#c3beae")
    speckle(im, r, ["#d2cdbd", "#e4e0d3"], 8)
    return im

def t_plaza(seed):
    r = random.Random(seed); im = new("#e0d0b4")
    for y in range(0, T, 4):
        rect(im, 0, y, T - 1, y, "#cdb994")
        off = 0 if (y // 4) % 2 == 0 else 4
        for x in range(off, T, 8):
            rect(im, x, y, x, y + 3, "#cdb994")
    speckle(im, r, ["#e8d2b3", "#d6bb98"], 10)
    return im

def t_grass(seed, flowers=False):
    r = random.Random(seed); im = new("#8dbd68")
    speckle(im, r, ["#7eaf5b", "#9ccb76", "#76a655"], 30)
    for _ in range(5):
        x, y = r.randrange(1, 15), r.randrange(1, 14)
        px(im, x, y, "#6a9a4c"); px(im, x, y + 1, "#6a9a4c")
    if flowers:
        for _ in range(4):
            x, y = r.randrange(1, 15), r.randrange(1, 15)
            px(im, x, y, r.choice(["#f2e46a", "#f4f0e8", "#e8a0b4"]))
    return im

def t_sand():
    r = random.Random(7); im = new("#e2d3a4")
    speckle(im, r, ["#d4c493", "#ebdfb8"], 24)
    return im

def t_road(seed):
    r = random.Random(seed); im = new("#70747c")
    speckle(im, r, ["#686c74", "#787c84", "#6c7078"], 20)
    return im

def t_road_dash(horizontal):
    im = t_road(3)
    if horizontal:
        rect(im, 3, 7, 12, 8, "#e6e1cc")
    else:
        rect(im, 7, 3, 8, 12, "#e6e1cc")
    return im

def t_zebra(horizontal_bars):
    im = t_road(5)
    for i in range(0, T, 4):
        if horizontal_bars: rect(im, 1, i, 14, i + 1, "#e9e6da")
        else: rect(im, i, 1, i + 1, 14, "#e9e6da")
    return im

def t_parking(seed):
    r = random.Random(seed); im = new("#8a8e94")
    speckle(im, r, ["#82868c", "#92969c"], 14)
    rect(im, 0, 0, 0, T - 1, "#e4e0cf"); rect(im, 8, 0, 8, 10, "#e4e0cf")
    return im

def t_water(seed):
    r = random.Random(seed); im = new("#6aaad6")
    speckle(im, r, ["#5f9fcd", "#7bb8e0"], 14)
    for _ in range(3):
        x, y = r.randrange(1, 11), r.randrange(1, 15)
        rect(im, x, y, x + 3, y, "#a8d4ee")
    return im

def t_floor(seed):
    im = new("#cbd0d4")
    for y in range(0, T, 8):
        rect(im, 0, y, T - 1, y, "#b6bcc2")
    for x in range(0, T, 8):
        rect(im, x, 0, x, T - 1, "#b6bcc2")
    return im

# ---------------------------------------------------------------- station / rail tiles
def edge_mask(im, mask, rim_dark, rim_light, order="NEW"):
    """mask bits: N=1,E=2,W=4 -> parapet on that side"""
    if mask & 1:
        rect(im, 0, 0, T - 1, 0, rim_dark); rect(im, 0, 1, T - 1, 2, rim_light)
    if mask & 2:
        rect(im, T - 1, 0, T - 1, T - 1, rim_dark); rect(im, T - 3, 0, T - 2, T - 1, rim_light)
    if mask & 4:
        rect(im, 0, 0, 0, T - 1, rim_dark); rect(im, 1, 0, 2, T - 1, rim_light)
    if mask & 1 and mask & 2: px(im, T - 1, 0, rim_dark)

def t_deck(mask, vent=False):
    im = new("#bdbdb8")
    for y in range(7, T, 8): rect(im, 0, y, T - 1, y, "#adada8")
    for x in range(7, T, 8): rect(im, x, 0, x, T - 1, "#adada8")
    if vent:
        rect(im, 4, 4, 11, 10, "#9a9d9f"); rect(im, 4, 4, 11, 4, "#b0b3b5"); rect(im, 5, 6, 10, 6, "#868a8c"); rect(im, 5, 8, 10, 8, "#868a8c")
    edge_mask(im, mask, "#8d8e8a", "#d6d6d1")
    return im

def t_station_wall(maskEW):
    im = new("#5f7396")
    rect(im, 0, 0, T - 1, 1, "#3f4e6b")          # eave shadow
    rect(im, 0, 2, T - 1, 3, "#8c98ad")          # cornice
    for x in (2, 9):
        rect(im, x, 6, x + 4, 10, "#cfe0ee"); rect(im, x, 6, x + 4, 6, "#8aa2bb"); rect(im, x + 2, 6, x + 2, 10, "#8aa2bb")
    rect(im, 0, 13, T - 1, 15, "#47556f")
    rect(im, 0, 12, T - 1, 12, "#d8d8d2")
    if maskEW & 2: rect(im, T - 1, 0, T - 1, T - 1, "#33405a")
    if maskEW & 4: rect(im, 0, 0, 0, T - 1, "#33405a")
    return im

def t_rail(vertical, seed=1):
    im = new("#928e84")
    r = random.Random(seed)
    speckle(im, r, ["#88847a", "#9c988e"], 18)
    if vertical:
        for y in range(1, T, 4): rect(im, 2, y, 13, y, "#6b5d4c")
        rect(im, 4, 0, 4, T - 1, "#d4d8de"); rect(im, 5, 0, 5, T - 1, "#8d9198")
        rect(im, 10, 0, 10, T - 1, "#d4d8de"); rect(im, 11, 0, 11, T - 1, "#8d9198")
    else:
        for x in range(1, T, 4): rect(im, x, 2, x, 13, "#6b5d4c")
        rect(im, 0, 4, T - 1, 4, "#d4d8de"); rect(im, 0, 5, T - 1, 5, "#8d9198")
        rect(im, 0, 10, T - 1, 10, "#d4d8de"); rect(im, 0, 11, T - 1, 11, "#8d9198")
    return im

def t_rail_edge(m, seed=1):
    """vertical rails on the viaduct, with a concrete parapet on the east (m&1) and/or west (m&2) edge"""
    im = t_rail(True, seed)
    if m & 1:
        rect(im, 12, 0, 15, T - 1, "#bdbdb8"); rect(im, 12, 0, 12, T - 1, "#8d8e8a"); rect(im, 15, 0, 15, T - 1, "#8d8e8a"); rect(im, 13, 0, 13, T - 1, "#d6d6d1")
    if m & 2:
        rect(im, 0, 0, 3, T - 1, "#bdbdb8"); rect(im, 3, 0, 3, T - 1, "#8d8e8a"); rect(im, 0, 0, 0, T - 1, "#8d8e8a"); rect(im, 2, 0, 2, T - 1, "#d6d6d1")
    return im

def t_platform(wrail, erail):
    im = new("#dcd5c2")
    for y in range(0, T, 8): rect(im, 0, y, T - 1, y, "#cdc6b2")
    if wrail: rect(im, 0, 0, 1, T - 1, "#e8c640"); rect(im, 2, 0, 2, T - 1, "#b9a87c")
    if erail: rect(im, T - 2, 0, T - 1, T - 1, "#e8c640"); rect(im, T - 3, 0, T - 3, T - 1, "#b9a87c")
    return im

def t_passage(seed):
    im = new("#d9d6cc")
    for y in range(0, T, 8): rect(im, 0, y, T - 1, y, "#c4c0b4")
    for x in range(0, T, 8): rect(im, x, 0, x, T - 1, "#c4c0b4")
    return im

def t_shade():
    return Image.new("RGBA", (T, T), (24, 28, 52, 92))

def t_canopy(seed):
    im = Image.new("RGBA", (T, T), (186, 214, 228, 64))
    d = ImageDraw.Draw(im)
    for x in range(0, T, 8): d.line((x, 0, x, T - 1), fill=(120, 160, 186, 130))
    d.line((0, T - 1, T - 1, T - 1), fill=(110, 150, 178, 150))
    return im

# ---------------------------------------------------------------- buildings
HOUSE = [  # roof, dark, light, wall, wall_shade
    ("#b5604a", "#8f4a3a", "#cf7c63", "#eadfc4", "#cbbf9f"),
    ("#5e7ca3", "#476285", "#7b97bd", "#f0ede4", "#d0ccc0"),
    ("#6a9a6e", "#4e7a54", "#88b78a", "#e8dec6", "#cabf9f"),
    ("#8a7f78", "#6b625c", "#a39a92", "#dccdae", "#bfae8e"),
    ("#c79a4b", "#a27a33", "#ddb468", "#f0e8d2", "#d2c8aa"),
    ("#4a5675", "#363f58", "#66739a", "#dedad2", "#c0bcb2"),
]
MID = [  # roof, parapet_light, wall, wall_shade, window
    ("#b9bcc0", "#d8dadd", "#d7d5ce", "#bab8b0", "#7da0bd"),
    ("#c8bfae", "#e0d9cb", "#e5d8bb", "#c9bc9d", "#6f93b0"),
    ("#b7c2cc", "#d3dce4", "#cdd8e1", "#acb9c4", "#5f86a6"),
    ("#bdb3aa", "#d6cdc5", "#cf9a82", "#ad7560", "#6d8aa3"),
]

def house_roof(v, mask):
    roof, dark, light, wall, shade = HOUSE[v]
    im = new(roof)
    for y in range(3, T, 4): rect(im, 0, y, T - 1, y, dark)
    for y in range(0, T, 4):
        off = 4 if (y // 4) % 2 else 0
        for x in range(off, T, 8):
            rect(im, x, y, x, y + 2, dark)
    if mask & 1:
        rect(im, 0, 0, T - 1, 2, light); rect(im, 0, 3, T - 1, 3, dark)
    if mask & 2: rect(im, T - 1, 0, T - 1, T - 1, dark)
    if mask & 4: rect(im, 0, 0, 0, T - 1, dark)
    return im

def eave(im, v):
    roof, dark, light, wall, shade = HOUSE[v]
    rect(im, 0, 0, T - 1, 1, dark); rect(im, 0, 2, T - 1, 3, shade)

def house_wall(v, maskEW, kind, low=False, awning=None):
    roof, dark, light, wall, shade = HOUSE[v]
    im = new(wall)
    rect(im, 0, T - 2, T - 1, T - 1, shade)
    if low:
        rect(im, 0, 0, T - 1, 5, roof); rect(im, 0, 0, T - 1, 1, light); rect(im, 0, 4, T - 1, 5, dark)
    else:
        eave(im, v)
    if kind == "win":
        rect(im, 5, 6, 10, 11, "#6b5a4a"); rect(im, 6, 7, 9, 10, "#bfdcec")
        rect(im, 4, 12, 11, 12, "#8a7664")
    elif kind == "door":
        rect(im, 5, 5, 10, 15, "#6b4a35"); rect(im, 6, 6, 9, 14, "#8a6347"); px(im, 9, 11, "#e8d27a"); rect(im, 4, 4, 11, 4, dark)
    elif kind == "shop":
        for i, x in enumerate(range(0, T, 4)):
            rect(im, x, 5, x + 1, 8, "#d4574a" if i % 2 == 0 else "#f3efe6"); rect(im, x + 2, 5, x + 3, 8, "#f3efe6" if i % 2 == 0 else "#d4574a")
        rect(im, 1, 9, 14, 14, "#6b5a4a"); rect(im, 2, 10, 13, 13, "#bfdcec"); rect(im, 2, 10, 5, 10, "#e6f3f9")
    if maskEW & 2: rect(im, T - 1, 0, T - 1, T - 1, shade)
    if maskEW & 4: rect(im, 0, 0, 0, T - 1, shade)
    return im

def mid_roof(v, mask, vent=False):
    roof, plight, wall, shade, win = MID[v]
    im = new(roof)
    r = random.Random(v * 7 + 1)
    speckle(im, r, [plight, "#aeb0b4"], 10)
    if vent:
        rect(im, 4, 5, 11, 11, "#8e9296"); rect(im, 4, 5, 11, 5, "#b4b8bb"); rect(im, 5, 7, 10, 7, "#74777a"); rect(im, 5, 9, 10, 9, "#74777a")
    edge_mask(im, mask, "#80838a", plight)
    return im

def mid_wall(v, maskEW, kind):
    roof, plight, wall, shade, win = MID[v]
    im = new(wall)
    rect(im, 0, 0, T - 1, 1, shade); rect(im, 0, 2, T - 1, 2, plight)
    rect(im, 0, T - 2, T - 1, T - 1, shade)
    if kind == "win":
        for y in (4, 9):
            for x in (2, 9):
                rect(im, x, y, x + 4, y + 3, win); rect(im, x, y, x + 4, y, "#cfe3f0")
    elif kind == "door":
        rect(im, 2, 5, 13, 15, "#5b6877"); rect(im, 3, 6, 12, 15, "#bcd8e8"); rect(im, 7, 6, 8, 15, "#5b6877")
        rect(im, 1, 3, 14, 4, "#c0463e")
    elif kind == "plain":
        for x in range(0, T, 5): rect(im, x, 4, x, 12, shade)
    if maskEW & 2: rect(im, T - 1, 0, T - 1, T - 1, shade)
    if maskEW & 4: rect(im, 0, 0, 0, T - 1, shade)
    return im

# ---------------------------------------------------------------- props (transparent)
def p_tree(seed):
    r = random.Random(seed)
    im = new()
    rect(im, 7, 11, 8, 15, "#6e4b32")
    for (cx, cy, rad, col) in [(8, 7, 7, "#3f7a42"), (6, 8, 4, "#4e8d4f"), (10, 6, 4, "#4e8d4f")]:
        d = ImageDraw.Draw(im); d.ellipse((cx - rad, cy - rad + 1, cx + rad, cy + rad + 1), fill=hexc(col))
    for _ in range(14):
        x, y = r.randrange(2, 14), r.randrange(1, 12)
        if im.getpixel((x, y))[3]: px(im, x, y, r.choice(["#5ea35e", "#356c39"]))
    for x in range(5, 11): px(im, x, 14, (0, 0, 0, 50))
    return im

def p_bench():
    im = new()
    rect(im, 2, 8, 13, 9, "#8a5a38"); rect(im, 2, 10, 13, 11, "#a8764a"); rect(im, 3, 12, 4, 14, "#4d4d55"); rect(im, 11, 12, 12, 14, "#4d4d55")
    rect(im, 2, 6, 13, 7, "#7a4e30")
    return im

def p_bike():
    im = new()
    rect(im, 1, 10, 14, 11, "#6b7078")
    for x in (2, 6, 10):
        rect(im, x, 6, x + 2, 9, "#8d939b"); px(im, x + 1, 7, (0, 0, 0, 0)); px(im, x + 1, 8, "#3a3d44")
        rect(im, x, 10, x + 2, 12, "#2f3138")
    rect(im, 1, 13, 14, 13, "#a4a6a8")
    return im

def p_vending():
    im = new()
    rect(im, 3, 3, 12, 14, "#d24a3c"); rect(im, 4, 4, 9, 9, "#f2f0e6"); rect(im, 10, 5, 11, 12, "#8e2a20"); rect(im, 5, 11, 9, 12, "#3b3b3b")
    rect(im, 3, 3, 12, 3, "#e8695b")
    return im

def p_postbox():
    im = new()
    rect(im, 5, 4, 10, 14, "#c8372d"); rect(im, 5, 4, 10, 5, "#e2574c"); rect(im, 6, 7, 9, 7, "#2a1a18"); rect(im, 5, 14, 10, 14, "#7a1f19")
    return im

def p_busstop():
    im = new()
    rect(im, 7, 3, 8, 14, "#8b9097"); rect(im, 3, 2, 12, 7, "#2f6fb3"); rect(im, 4, 3, 11, 6, "#f0f4f8"); rect(im, 5, 4, 10, 5, "#2f6fb3")
    rect(im, 5, 14, 10, 14, "#6c7178")
    return im

def p_lamp():
    im = new()
    rect(im, 7, 5, 8, 14, "#4f5560"); rect(im, 5, 2, 10, 4, "#e9e4c0"); rect(im, 5, 1, 10, 1, "#4f5560"); rect(im, 6, 14, 9, 15, "#3d424b")
    return im

def p_jet():
    im = new()
    rect(im, 7, 4, 8, 11, "#e4f2fa"); rect(im, 6, 8, 9, 10, "#cfe6f3"); rect(im, 5, 11, 10, 12, "#e4f2fa")
    return im

def p_signpost():
    im = new()
    rect(im, 7, 4, 8, 14, "#7a7f86"); rect(im, 3, 2, 12, 6, "#e6c43a"); rect(im, 4, 3, 11, 5, "#fff6c9")
    return im


# ---------------------------------------------------------------- shopping street (商店街) art
AWN = ["#d4574a", "#3f6fb5", "#3f8f5f", "#e0a030", "#8a5ab0", "#2f9aa8"]
WALLS = ["#eadfc4", "#f0ede4", "#e8dec6", "#dccdae", "#f0e8d2", "#dedad2"]
def t_shopst(seed):
    r = random.Random(seed); im = new("#d8b494")
    # running-bond brick pavement, warm brick colour with a lighter border stripe on the odd variant
    for y in range(0, T, 4):
        rect(im, 0, y, T - 1, y, "#b98f72")
        off = 0 if (y // 4) % 2 == 0 else 4
        for x in range(off, T, 8):
            rect(im, x, y, x, y + 3, "#b98f72")
    speckle(im, r, ["#e2c2a4", "#cfa888"], 12)
    if seed % 3 == 1:
        for x in range(0, T, 2): px(im, x, 7, "#a67c60")
    return im

def t_under(seed):
    im = Image.new("RGBA", (T, T), (18, 22, 42, 118))
    d = ImageDraw.Draw(im)
    d.line((0, 7, T - 1, 7), fill=(8, 10, 24, 150)); d.line((0, 8, T - 1, 8), fill=(60, 66, 96, 70))
    if seed % 2: d.rectangle((6, 2, 9, 4), fill=(8, 10, 24, 120))
    return im

def t_shopfront(c, kind, maskEW):
    awn = AWN[c]; wall = WALLS[c]
    im = new(wall)
    rect(im, 0, 0, T - 1, 0, "#6b5a4a")                      # eave line
    rect(im, 0, T - 2, T - 1, T - 1, "#bfae8e")              # base
    # striped awning with scalloped edge
    for i, x in enumerate(range(0, T, 4)):
        col = awn if i % 2 == 0 else "#f3efe6"
        rect(im, x, 1, x + 3, 5, col)
        rect(im, x + 1, 6, x + 2, 6, col)
    rect(im, 0, 1, T - 1, 1, "#00000022" if False else awn)
    for x in range(0, T, 4): px(im, x, 6, awn if (x // 4) % 2 == 0 else "#f3efe6")
    rect(im, 0, 7, T - 1, 7, "#8a7664")                       # awning shadow
    if kind == "shop":
        rect(im, 1, 8, 14, 13, "#5a4a3c"); rect(im, 2, 9, 13, 12, "#bfdcec")
        goods = [("#d4574a", 3), ("#e8c640", 6), ("#7ab36a", 9), ("#f3efe6", 11)]
        for col, gxx in goods: rect(im, gxx, 11, gxx + 1, 12, col)
        rect(im, 2, 9, 13, 9, "#d8ecf6")
    elif kind == "door":
        rect(im, 4, 8, 11, 15, "#5a4a3c"); rect(im, 5, 9, 10, 15, "#8a6347"); rect(im, 5, 9, 10, 11, "#bfdcec"); px(im, 9, 13, "#e8d27a")
        rect(im, 1, 9, 3, 12, "#bfdcec"); rect(im, 12, 9, 14, 12, "#bfdcec")
    else:   # signboard
        rect(im, 1, 8, 14, 11, "#2f3a4f"); rect(im, 2, 9, 13, 10, "#f3efe6")
        for x in range(3, 13, 3): rect(im, x, 9, x + 1, 10, awn)
        rect(im, 3, 12, 12, 13, "#bfdcec")
    if maskEW & 2: rect(im, T - 1, 0, T - 1, T - 1, "#8a7664")
    if maskEW & 4: rect(im, 0, 0, 0, T - 1, "#8a7664")
    return im

def p_flag(v):
    im = new()
    cols = ["#d4574a", "#3f6fb5", "#e0a030"][v]
    rect(im, 4, 2, 4, 14, "#4f5560"); rect(im, 4, 2, 10, 2, "#4f5560")
    rect(im, 5, 3, 10, 12, cols); rect(im, 5, 3, 5, 12, "#00000000" if False else cols)
    rect(im, 7, 5, 8, 10, "#f3efe6")
    rect(im, 3, 14, 6, 15, "#3d424b")
    return im

def p_lamp_shop():
    im = new()
    rect(im, 7, 6, 8, 14, "#4f5560"); rect(im, 6, 14, 9, 15, "#3d424b")
    rect(im, 5, 1, 10, 5, "#d4574a"); rect(im, 6, 2, 9, 4, "#f2d27a"); rect(im, 5, 1, 10, 1, "#8f2f27"); rect(im, 5, 5, 10, 5, "#8f2f27")
    rect(im, 2, 6, 13, 6, "#4f5560")
    return im

def text_sign(text, style):
    """street-name plate: w x 2 tiles, pixel text from Noto Sans CJK JP Bold (SIL OFL, 1-bit bitmap only)"""
    n = len(text); W = max(3, -(-(n * 12 + 10) // 16)) * 16; H = 32
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    fill, rim, dark = {"shop": ("#9c2f2a", "#f2f2ec", "#6e1f1c"), "road": ("#1f6b4a", "#f2f2ec", "#154a33")}[style]
    d.rectangle((4, 22, 6, 31), fill=hexc("#6e747c")); d.rectangle((W - 7, 22, W - 5, 31), fill=hexc("#6e747c"))
    d.rectangle((0, 0, W - 1, 23), fill=hexc(fill)); d.rectangle((1, 1, W - 2, 22), outline=hexc(rim)); d.rectangle((0, 23, W - 1, 23), fill=hexc(dark))
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 12, index=0)
    g = Image.new("L", (W, 16), 0); gd = ImageDraw.Draw(g)
    for k, ch in enumerate(text): gd.text((k * 12, 0), ch, font=font, fill=255)
    g = g.point(lambda v: 255 if v > 110 else 0); bx = g.getbbox()
    if bx:
        ox = (W - (bx[2] - bx[0])) // 2 - bx[0]; oy = 2 + (20 - (bx[3] - bx[1])) // 2 - bx[1]
        for yy in range(g.height):
            for xx in range(g.width):
                if g.getpixel((xx, yy)): im.putpixel((xx + ox, yy + oy), hexc("#ffffff"))
    return im

# ---------------------------------------------------------------- sign with pixel text
def make_sign():
    W, H = 48, 32
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # posts
    d.rectangle((6, 22, 8, 31), fill=hexc("#6e747c")); d.rectangle((39, 22, 41, 31), fill=hexc("#6e747c"))
    d.rectangle((0, 0, 47, 23), fill=hexc("#1f3f73"))
    d.rectangle((0, 0, 47, 1), fill=hexc("#33589a"))
    d.rectangle((1, 2, 46, 21), outline=hexc("#f2f2ec"))
    d.rectangle((0, 23, 47, 23), fill=hexc("#162d55"))
    # text bitmap
    font = ImageFont.truetype("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 13, index=0)
    txt = "草加駅"
    g = Image.new("L", (44, 16), 0); gd = ImageDraw.Draw(g)
    x = 0
    for ch in txt:
        gd.text((x, 0), ch, font=font, fill=255); x += 14
    g = g.point(lambda v: 255 if v > 110 else 0)
    bx = g.getbbox()
    gw, gh = bx[2] - bx[0], bx[3] - bx[1]
    ox = (W - gw) // 2 - bx[0]
    oy = 2 + (20 - gh) // 2 - bx[1]
    for yy in range(g.height):
        for xx in range(g.width):
            if g.getpixel((xx, yy)):
                im.putpixel((xx + ox, yy + oy), hexc("#ffffff"))
    # tiny "TS16" tag (3x5 digits drawn by hand)
    return im

# ---------------------------------------------------------------- player
OUTLINE = "#2b2230"
def make_player():
    sheet = Image.new("RGBA", (16 * 3, 24 * 4), (0, 0, 0, 0))
    cap, capd, skin, hair = "#d4483c", "#a8342b", "#f2cca3", "#43302a"
    jacket, jackd, pants, shoe = "#3f86b8", "#2f6794", "#454b66", "#2a2a2e"

    def cell(direction, frame):
        im = Image.new("RGBA", (16, 24), (0, 0, 0, 0))
        bob = 1 if frame in (0, 2) else 0
        # legs (frame 0: left foot lifted, frame 2: right foot lifted)
        if direction in ("down", "up"):
            for (x0, x1, lift) in ((5, 7, frame == 0), (8, 10, frame == 2)):
                top = 18
                bot = 21 if lift else 22
                rect(im, x0, top, x1, bot, pants)
                rect(im, x0, bot, x1, bot + 1, shoe)
        else:
            rect(im, 6, 18, 9, 21, pants)
            sh = {0: -2, 1: 0, 2: 2}[frame]
            rect(im, 6 + min(sh, 0), 22, 9 + max(sh, 0), 23, shoe)
        y0 = -bob
        # body
        rect(im, 4, 12 + y0, 11, 18 + y0, jacket); rect(im, 4, 17 + y0, 11, 18 + y0, jackd)
        if direction == "down":
            rect(im, 3, 13 + y0, 3, 17 + y0, jackd); rect(im, 12, 13 + y0, 12, 17 + y0, jackd)
            px(im, 3, 18 + y0, skin); px(im, 12, 18 + y0, skin)
        elif direction == "up":
            rect(im, 3, 13 + y0, 3, 17 + y0, jackd); rect(im, 12, 13 + y0, 12, 17 + y0, jackd)
            rect(im, 5, 13 + y0, 10, 17 + y0, "#7a5a3a"); rect(im, 5, 13 + y0, 10, 13 + y0, "#8f6c47")
        else:
            rect(im, 6, 13 + y0, 9, 17 + y0, jackd); px(im, 7, 18 + y0, skin)
        # head
        rect(im, 4, 5 + y0, 11, 11 + y0, skin)
        if direction == "down":
            rect(im, 3, 2 + y0, 12, 5 + y0, cap); rect(im, 3, 5 + y0, 12, 6 + y0, capd); rect(im, 4, 7 + y0, 4, 8 + y0, hair); rect(im, 11, 7 + y0, 11, 8 + y0, hair)
            rect(im, 5, 8 + y0, 6, 9 + y0, "#2b2230"); rect(im, 9, 8 + y0, 10, 9 + y0, "#2b2230")
            px(im, 5, 8 + y0, "#ffffff"); px(im, 9, 8 + y0, "#ffffff")
        elif direction == "up":
            rect(im, 3, 2 + y0, 12, 8 + y0, cap); rect(im, 4, 9 + y0, 11, 11 + y0, hair); rect(im, 3, 7 + y0, 12, 8 + y0, capd)
        else:
            fx = 1 if direction == "left" else 0
            rect(im, 3, 2 + y0, 12, 5 + y0, cap)
            # brim toward facing side
            if direction == "left": rect(im, 1, 5 + y0, 9, 6 + y0, capd)
            else: rect(im, 6, 5 + y0, 14, 6 + y0, capd)
            rect(im, 4, 7 + y0, 11, 11 + y0, skin)
            if direction == "left":
                rect(im, 8, 6 + y0, 11, 11 + y0, hair); rect(im, 5, 8 + y0, 6, 9 + y0, "#2b2230")
            else:
                rect(im, 4, 6 + y0, 7, 11 + y0, hair); rect(im, 9, 8 + y0, 10, 9 + y0, "#2b2230")
        # outline
        out = im.copy(); o = hexc(OUTLINE)
        for y in range(24):
            for x in range(16):
                if im.getpixel((x, y))[3] == 0:
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < 16 and 0 <= ny < 24 and im.getpixel((nx, ny))[3]:
                            out.putpixel((x, y), o); break
        return out

    for row, dname in enumerate(["down", "left", "right", "up"]):
        for f in range(3):
            sheet.paste(cell(dname, f), (f * 16, row * 24))
    return sheet

# ---------------------------------------------------------------- atlas
def build():
    tiles = []
    def add(name, im): tiles.append((name, im))
    for i in range(3): add(f"ground_{i}", t_ground(10 + i))
    for i in range(2): add(f"sidewalk_{i}", t_sidewalk(20 + i))
    for i in range(2): add(f"plaza_{i}", t_plaza(30 + i))
    for i in range(3): add(f"grass_{i}", t_grass(40 + i))
    add("grass_flowers", t_grass(50, True)); add("sand", t_sand())
    for i in range(2): add(f"road_{i}", t_road(60 + i))
    add("road_dash_h", t_road_dash(True)); add("road_dash_v", t_road_dash(False))
    add("zebra_hbars", t_zebra(True)); add("zebra_vbars", t_zebra(False))
    for i in range(2): add(f"parking_{i}", t_parking(70 + i))
    for i in range(2): add(f"water_{i}", t_water(80 + i))
    add("floor_0", t_floor(1)); add("passage_0", t_passage(1)); add("passage_1", t_passage(2))
    # station
    for m in range(8):
        add(f"deck_{m}", t_deck(m)); add(f"deckv_{m}", t_deck(m, True))
    for m in range(4): add(f"stwall_{m}", t_station_wall([0, 2, 4, 6][m]))
    add("rail_v", t_rail(True)); add("rail_h", t_rail(False)); add("rail_v2", t_rail(True, 2))
    for m in range(1, 4): add(f"rail_ve_{m}", t_rail_edge(m))
    for wr in (0, 1):
        for er in (0, 1): add(f"platform_{wr}{er}", t_platform(wr, er))
    # houses
    for v in range(len(HOUSE)):
        for m in range(8): add(f"h{v}_roof_{m}", house_roof(v, m))
        for m, mm in enumerate([0, 2, 4, 6]):
            for kind in ("plain", "win", "door", "shop"):
                add(f"h{v}_wall_{kind}_{m}", house_wall(v, mm, kind))
                if kind != "shop": add(f"h{v}_low_{kind}_{m}", house_wall(v, mm, kind, low=True))
    for v in range(len(MID)):
        for m in range(8):
            add(f"m{v}_roof_{m}", mid_roof(v, m)); add(f"m{v}_roofv_{m}", mid_roof(v, m, True))
        for m, mm in enumerate([0, 2, 4, 6]):
            for kind in ("plain", "win", "door"): add(f"m{v}_wall_{kind}_{m}", mid_wall(v, mm, kind))
    # props
    for i in range(2): add(f"tree_{i}", p_tree(90 + i))
    add("bench", p_bench()); add("bikes", p_bike()); add("vending", p_vending()); add("postbox", p_postbox())
    add("busstop", p_busstop()); add("lamp", p_lamp()); add("jet", p_jet()); add("signpost", p_signpost())
    add("shade", t_shade()); add("canopy_0", t_canopy(1)); add("canopy_1", t_canopy(2))
    # shopping street, under-viaduct shade
    for i in range(3): add(f"shopst_{i}", t_shopst(100 + i))
    for i in range(2): add(f"under_{i}", t_under(i))
    for c in range(len(AWN)):
        for kind in ("shop", "door", "sign"):
            for m, mm in enumerate([0, 2, 4, 6]): add(f"sf{c}_{kind}_{m}", t_shopfront(c, kind, mm))
    for v in range(3): add(f"flag_{v}", p_flag(v))
    add("lamp_shop", p_lamp_shop())

    cols = 20
    rows = (len(tiles) + cols - 1) // cols
    atlas = Image.new("RGBA", (cols * T, rows * T), (0, 0, 0, 0))
    for i, (n, im) in enumerate(tiles):
        atlas.paste(im, ((i % cols) * T, (i // cols) * T))
    atlas.save(OUT / "tiles.png")
    (OUT / "tiles.json").write_text(json.dumps({"tile": T, "cols": cols, "names": [n for n, _ in tiles]}, ensure_ascii=False))
    make_player().save(OUT / "player.png")
    make_sign().save(OUT / "sign_soka.png")
    print("tiles:", len(tiles), "atlas", atlas.size)

if __name__ == "__main__":
    build()
