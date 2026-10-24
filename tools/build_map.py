#!/usr/bin/env python3
"""OSM (data/osm_raw.json) -> tile grid + object list -> assets/map.json

Scale: 3 m per tile, 16x16 px tiles. The window is 80 x 80 tiles (240 m) centred on the station node.
Everything is rasterised on a larger 110 x 110 grid first (so walls / edges at the window border are right)
and then cropped.

Pipeline:  parse -> metres (x east, y south from the station node) -> rasterise layers
           (land, paths, roads, rail, platforms, buildings, passages) -> autotile -> props -> collision
           -> overrides (data/overrides.json) -> assets/map.json (+ data/build_report.json)

Usage: python3 tools/build_map.py
"""
import json, math, hashlib, collections
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CELL = 3.0
HALF = 165.0                       # data half-size in metres (matches fetch_osm.py)
FULL = int(round(2 * HALF / CELL))  # 110
CORE = 80
OFF = (FULL - CORE) // 2            # 15
raw = json.loads((ROOT / "data/osm_raw.json").read_text())
LAT0, LON0 = raw["_meta"]["center"]
atlas = json.loads((ROOT / "assets/tiles.json").read_text())
TID = {n: i for i, n in enumerate(atlas["names"])}

def to_m(p):
    return ((p["lon"] - LON0) * 111320.0 * math.cos(math.radians(LAT0)), -(p["lat"] - LAT0) * 111320.0)

def h(*a):
    return int(hashlib.md5("|".join(map(str, a)).encode()).hexdigest()[:8], 16)

def gx(x): return int(math.floor((x + HALF) / CELL))
def gy(y): return int(math.floor((y + HALF) / CELL))
def inside(x, y): return 0 <= x < FULL and 0 <= y < FULL

# ---------------------------------------------------------------- parse
ways, nodes, rels = [], [], []
for e in raw["elements"]:
    t = e.get("tags", {})
    if e["type"] == "way" and len(e.get("geometry", [])) >= 2:
        ways.append({"id": e["id"], "tags": t, "pts": [to_m(p) for p in e["geometry"]]})
    elif e["type"] == "node":
        nodes.append({"id": e["id"], "tags": t, "xy": to_m(e)})
    elif e["type"] == "relation":
        rels.append(e)

def is_closed(pts): return len(pts) >= 4 and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 0.01

def poly_area(pts):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2

def centroid(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

# polygons: (tags, id, outer_ring, [holes])
polys = []
for w in ways:
    t = w["tags"]
    if is_closed(w["pts"]) and (any(k in t for k in ("building", "landuse", "leisure", "natural", "water", "amenity")) or t.get("railway") == "platform" or t.get("public_transport") == "platform"):
        polys.append((t, w["id"], w["pts"][:-1], []))
for r in rels:
    outers = [[to_m(p) for p in m["geometry"]][:-1] for m in r["members"] if m.get("role") == "outer" and len(m.get("geometry", [])) > 3]
    inners = [[to_m(p) for p in m["geometry"]][:-1] for m in r["members"] if m.get("role") == "inner" and len(m.get("geometry", [])) > 3]
    for o in outers: polys.append((r.get("tags", {}), r["id"], o, inners))

def raster_poly(ring, holes=(), thresh=4):
    """tiles whose 3x3 sample points are inside the polygon at least `thresh` times"""
    xs = [p[0] for p in ring]; ys = [p[1] for p in ring]
    x0, x1 = max(0, gx(min(xs))), min(FULL - 1, gx(max(xs)))
    y0, y1 = max(0, gy(min(ys))), min(FULL - 1, gy(max(ys)))
    if x1 < x0 or y1 < y0: return set()
    sub = np.array([-1.0, 0.0, 1.0])
    gxs = np.arange(x0, x1 + 1); gys = np.arange(y0, y1 + 1)
    cx = (gxs + 0.5) * CELL - HALF; cy = (gys + 0.5) * CELL - HALF
    PX = (cx[None, :, None] + sub[None, None, :] * CELL / 3.0)  # (1, nx, 3)
    PY = (cy[:, None, None] + sub[None, None, :] * CELL / 3.0)  # (ny, 1, 3)
    PXf = np.broadcast_to(PX[:, :, :, None], (len(gys), len(gxs), 3, 3))
    PYf = np.broadcast_to(PY[:, :, None, :], (len(gys), len(gxs), 3, 3))
    def inpoly(pr):
        c = np.zeros(PXf.shape, dtype=bool)
        n = len(pr)
        for i in range(n):
            ax, ay = pr[i]; bx, by = pr[(i + 1) % n]
            if ay == by: continue
            cond = ((ay > PYf) != (by > PYf))
            xint = (bx - ax) * (PYf - ay) / (by - ay) + ax
            c ^= cond & (PXf < xint)
        return c
    m = inpoly(ring)
    for hr in holes: m &= ~inpoly(hr)
    cnt = m.reshape(len(gys), len(gxs), 9).sum(axis=2)
    out = set()
    for j, yy in enumerate(gys):
        for i, xx in enumerate(gxs):
            if cnt[j, i] >= thresh: out.add((int(xx), int(yy)))
    return out

def walk_tiles(pts, n=1, step=0.75):
    """Tiles along a polyline, 4-connected, widened to n tiles (n=2/3 by adding neighbour tiles on the dominant-perpendicular axis)."""
    res = []   # list of (tile, (dx,dy) direction)
    prev = None
    for a, b in zip(pts, pts[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L == 0: continue
        dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        k = max(1, int(L / step))
        for i in range(k + 1):
            x = a[0] + dx * L * i / k; y = a[1] + dy * L * i / k
            t = (gx(x), gy(y))
            horiz = abs(dx) >= abs(dy)
            if prev is not None and prev != t and prev[0] != t[0] and prev[1] != t[1]:
                res.append(((prev[0], t[1]), (dx, dy)) if horiz else ((t[0], prev[1]), (dx, dy)))
            res.append((t, (dx, dy)))
            if n >= 2:
                if horiz:
                    fy = ((y + HALF) / CELL) - math.floor((y + HALF) / CELL)
                    ex = [(t[0], t[1] - 1 if fy < 0.5 else t[1] + 1)]
                    if n >= 3: ex = [(t[0], t[1] - 1), (t[0], t[1] + 1)]
                else:
                    fx = ((x + HALF) / CELL) - math.floor((x + HALF) / CELL)
                    ex = [(t[0] - 1 if fx < 0.5 else t[0] + 1, t[1])]
                    if n >= 3: ex = [(t[0] - 1, t[1]), (t[0] + 1, t[1])]
                for e2 in ex: res.append((e2, (dx, dy)))
            prev = t
    return res

# ---------------------------------------------------------------- grids
G = lambda v=None: [[v for _ in range(FULL)] for _ in range(FULL)]
land = G("ground")           # ground / grass / parking / sand / water
path = G(False)              # footways and paths
road = G(None)               # None or road class
roadmark = G(None)           # ("dash", horiz)
zebra = G(None)              # horiz bars True/False
rail = G(None)               # "v"/"h" (main tracks, drawn)
railaux = G(False)           # sidings / yard / crossover tracks: solid viaduct deck, not drawn as rails
plat = G(False)
bld = G(0)                   # building id (non-station)
binfo = {}
stationpoly = G(False)       # VARIE + transportation building footprint
passage = G(False)
canopy = G(False)
props = G(None)              # prop name
notes = collections.Counter()

# land use
for t, i, ring, holes in sorted(polys, key=lambda p: -poly_area(p[2])):
    kind = None
    if t.get("landuse") in ("grass", "meadow", "village_green", "recreation_ground", "flowerbed") or t.get("leisure") in ("park", "garden", "pitch") or t.get("natural") in ("grassland", "scrub", "wood"):
        kind = "grass"
    elif t.get("leisure") == "playground": kind = "sand"
    elif t.get("amenity") == "parking" and "building" not in t: kind = "parking"
    elif t.get("amenity") == "fountain" or t.get("natural") == "water" or t.get("water") or "waterway" in t: kind = "water"
    if kind:
        for (x, y) in raster_poly(ring, holes, thresh=3):
            if inside(x, y): land[y][x] = kind
        notes["land_" + kind] += 1

# paths / roads
ROADW = {"motorway": 3, "trunk": 3, "primary": 3, "secondary": 3, "tertiary": 2, "unclassified": 2, "residential": 2,
         "living_street": 2, "service": 1, "road": 2}
PATHS = {"footway", "path", "steps", "pedestrian", "cycleway", "track", "corridor", "bridleway"}
crossing_ways = []
passage_ways = []
for w in sorted(ways, key=lambda w: w["tags"].get("layer", "0")):
    t = w["tags"]; hw = t.get("highway")
    if not hw: continue
    if t.get("tunnel") == "building_passage" or hw == "corridor" or t.get("indoor") == "yes":
        passage_ways.append(w); continue
    if hw == "footway" and t.get("footway") == "crossing":
        crossing_ways.append(w); continue
    if hw in PATHS:
        for (x, y), _ in walk_tiles(w["pts"], 1):
            if inside(x, y): path[y][x] = True
    elif hw in ROADW:
        n = ROADW[hw]
        if t.get("lanes") and t["lanes"].isdigit() and int(t["lanes"]) >= 4: n = 3
        seq = walk_tiles(w["pts"], n)
        for (x, y), d in seq:
            if inside(x, y): road[y][x] = hw
        if n >= 2:
            for (x, y), d in walk_tiles(w["pts"], 1, step=1.5):
                if inside(x, y) and road[y][x] and (x + y) % 2 == 0:
                    roadmark[y][x] = abs(d[0]) >= abs(d[1])

# crossings: zebra on road tiles around crossing nodes, perpendicular to the road
def road_dir_at(x, y):
    """dominant road axis around a tile: True if the road runs horizontally"""
    hz = sum(1 for dx in (-2, -1, 1, 2) if inside(x + dx, y) and road[y][x + dx])
    vt = sum(1 for dy in (-2, -1, 1, 2) if inside(x, y + dy) and road[y + dy][x])
    return hz >= vt
for nd in nodes:
    if nd["tags"].get("highway") == "crossing" or nd["tags"].get("railway") == "level_crossing":
        x, y = gx(nd["xy"][0]), gy(nd["xy"][1])
        if not inside(x, y): continue
        # snap to nearest road tile
        best = None
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                if inside(x + dx, y + dy) and road[y + dy][x + dx] and (best is None or abs(dx) + abs(dy) < best[0]):
                    best = (abs(dx) + abs(dy), x + dx, y + dy)
        if not best: continue
        _, rx, ry = best
        horiz = road_dir_at(rx, ry)
        # zebra bars lie parallel to the road direction; they cover the road width (perpendicular)
        for k in range(-3, 4):
            tx, ty = (rx, ry + k) if horiz else (rx + k, ry)
            if inside(tx, ty) and road[ty][tx]: zebra[ty][tx] = horiz
        notes["zebra"] += 1
for w in crossing_ways:
    for (x, y), d in walk_tiles(w["pts"], 1):
        if inside(x, y) and road[y][x]: zebra[y][x] = road_dir_at(x, y)

# railway (elevated viaduct) + platforms
RAILS = {"rail", "light_rail", "narrow_gauge", "subway", "tram"}
for w in ways:
    t = w["tags"]
    if t.get("railway") in RAILS and t.get("tunnel") not in ("yes", "building_passage"):
        aux = t.get("service") in ("siding", "yard", "crossover", "spur")
        for (x, y), d in walk_tiles(w["pts"], 1, step=1.0):
            if inside(x, y):
                if aux: railaux[y][x] = True; continue
                o = "v" if abs(d[1]) >= abs(d[0]) else "h"
                rail[y][x] = "v" if (rail[y][x] == "v" or o == "v") else "h"
        notes["rail_ways"] += 1
        if t.get("bridge") == "viaduct": notes["rail_viaduct_ways"] += 1
for t, i, ring, holes in polys:
    if t.get("railway") == "platform" or t.get("public_transport") == "platform":
        for (x, y) in raster_poly(ring, holes, thresh=3):
            if inside(x, y): plat[y][x] = True

# buildings
PUBLIC_BUILDING_NAMES = {"VARIE", "マルイ", "ヨーカドー", "ダイエー"}
roof_polys = []
for t, i, ring, holes in sorted(polys, key=lambda p: -poly_area(p[2])):
    b = t.get("building")
    if not b: continue
    area = poly_area(ring)
    tiles = raster_poly(ring, holes, thresh=4)
    if not tiles and area >= 7:
        c = centroid(ring); tiles = {(gx(c[0]), gy(c[1]))}
    if t.get("name") == "VARIE" or b == "transportation":
        for (x, y) in tiles:
            if inside(x, y): stationpoly[y][x] = True
        notes["station_poly_tiles"] += len(tiles)
        continue
    if b == "roof" or t.get("amenity") == "shelter":
        roof_polys.append((ring, holes, tiles)); continue
    lv = t.get("building:levels"); lv = int(lv) if lv and lv.isdigit() else 0
    mid = area >= 220 or b in ("commercial", "retail", "apartments", "hotel", "office", "industrial", "public", "civic", "hospital", "school") or lv >= 3
    variant = h("b", i) % (4 if mid else 6)
    if t.get("name") == "マルイ": variant = 3
    binfo[i] = {"mid": mid, "variant": variant, "shop": b in ("retail", "commercial")}
    for (x, y) in tiles:
        if inside(x, y): bld[y][x] = i
    notes["buildings"] += 1

# canopies: building=roof polygons that are not on the station deck
for ring, holes, tiles in roof_polys:
    for (x, y) in tiles:
        if inside(x, y) and not stationpoly[y][x] and not bld[y][x] and not rail[y][x]:
            canopy[y][x] = True

# station region
region = G(False)
for y in range(FULL):
    for x in range(FULL):
        if stationpoly[y][x] or rail[y][x] or plat[y][x] or railaux[y][x]: region[y][x] = True
        if rail[y][x]: bld[y][x] = 0

# passages (building passages, indoor corridors) - walkable even inside buildings / the station deck
for w in passage_ways:
    for (x, y), _ in walk_tiles(w["pts"], 1):
        if inside(x, y): passage[y][x] = True
notes["passage_tiles"] = sum(sum(r) for r in passage)

# ---------------------------------------------------------------- stations / entrances / spawn
station = [n for n in nodes if n["tags"].get("railway") == "station"][0]
entrances = [n for n in nodes if n["tags"].get("railway") == "train_station_entrance"]
entrances.sort(key=lambda n: n["xy"][0])
west, east = entrances[0], entrances[-1]

def free_ground(x, y, allow_path=False):
    return (inside(x, y) and not road[y][x] and not bld[y][x] and not region[y][x] and not passage[y][x]
            and land[y][x] in ("ground", "grass", "sand") and (allow_path or not path[y][x]) and not canopy[y][x])

# ---------------------------------------------------------------- compose layers
base = G(None); deco = G(None); over = G(None); solid = G(False)
def T_(name): return TID[name]

near_road = G(False)
for y in range(FULL):
    for x in range(FULL):
        if road[y][x] in ("secondary", "tertiary"):
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if inside(x + dx, y + dy): near_road[y + dy][x + dx] = True
ent_px = [(gx(n["xy"][0]), gy(n["xy"][1])) for n in entrances]
def near_entrance(x, y, r=11):
    return any(math.hypot(x - ex, y - ey) <= r for ex, ey in ent_px)

def mask_NEW(grid_same, x, y):
    m = 0
    if not grid_same(x, y - 1): m |= 1
    if not grid_same(x + 1, y): m |= 2
    if not grid_same(x - 1, y): m |= 4
    return m
def mask_EW(grid_same, x, y):
    m = 0
    if not grid_same(x + 1, y): m |= 2
    if not grid_same(x - 1, y): m |= 4
    return {0: 0, 2: 1, 4: 2, 6: 3}[m]

for y in range(FULL):
    for x in range(FULL):
        # ground
        l = land[y][x]; r = h("g", x, y)
        if l == "grass": b = "grass_flowers" if r % 17 == 0 else f"grass_{r % 3}"
        elif l == "water": b = f"water_{r % 2}"
        elif l == "parking": b = f"parking_{r % 2}"
        elif l == "sand": b = "sand"
        else:
            if near_entrance(x, y) and not road[y][x]: b = f"plaza_{r % 2}"
            elif near_road[y][x]: b = f"sidewalk_{r % 2}"
            else: b = f"ground_{r % 3}"
        if path[y][x] and l not in ("water",): b = f"sidewalk_{r % 2}" if not near_entrance(x, y) else f"plaza_{r % 2}"
        if road[y][x]: b = f"road_{r % 2}"
        if road[y][x] and roadmark[y][x] is not None: b = "road_dash_h" if roadmark[y][x] else "road_dash_v"
        if road[y][x] and zebra[y][x] is not None: b = "zebra_hbars" if zebra[y][x] else "zebra_vbars"
        base[y][x] = b
        if l == "water" or (bld[y][x] and not passage[y][x]): solid[y][x] = True

# houses and mid-rises (roof rows over wall rows)
same_b = lambda i: (lambda x, y: inside(x, y) and bld[y][x] == i)
for y in range(FULL):
    for x in range(FULL):
        i = bld[y][x]
        if not i or region[y][x]: continue
        inf = binfo[i]; v = inf["variant"]; pre = "m" if inf["mid"] else "h"
        below = inside(x, y + 1) and bld[y + 1][x] == i
        above = inside(x, y - 1) and bld[y - 1][x] == i
        sm = same_b(i)
        if below:
            m = mask_NEW(sm, x, y)
            if inf["mid"]:
                base[y][x] = f"m{v}_roofv_{m}" if h("v", x, y) % 9 == 0 else f"m{v}_roof_{m}"
            else:
                base[y][x] = f"h{v}_roof_{m}"
        else:
            m = mask_EW(sm, x, y)
            # find the extent of this wall run to place the door
            x0 = x
            while inside(x0 - 1, y) and bld[y][x0 - 1] == i and not (inside(x0 - 1, y + 1) and bld[y + 1][x0 - 1] == i): x0 -= 1
            x1 = x
            while inside(x1 + 1, y) and bld[y][x1 + 1] == i and not (inside(x1 + 1, y + 1) and bld[y + 1][x1 + 1] == i): x1 += 1
            L = x1 - x0 + 1; door_x = x0 + (h("door", i, y) % L if L > 1 else 0)
            if inf["mid"]:
                kind = "door" if x == door_x else ("win" if (x - x0) % 3 != 2 else "plain")
                base[y][x] = f"m{v}_wall_{kind}_{m}"
            else:
                if x == door_x and L >= 2: kind = "door"
                elif inf["shop"] and x != door_x: kind = "shop"
                else: kind = "win" if (x - x0) % 2 == 0 else "plain"
                if kind == "shop": base[y][x] = f"h{v}_wall_shop_{m}"
                elif above:
                    base[y][x] = f"h{v}_wall_{kind}_{m}"
                else:
                    base[y][x] = f"h{v}_low_{kind}_{m}"

# station deck
same_r = lambda x, y: inside(x, y) and region[y][x]
for y in range(FULL):
    for x in range(FULL):
        if not region[y][x]: continue
        solid[y][x] = True
        if rail[y][x]:
            if rail[y][x] == "h": base[y][x] = "rail_h"
            else:
                em = mask_EW(same_r, x, y)
                base[y][x] = f"rail_ve_{em}" if em else ("rail_v2" if h("r", x, y) % 4 == 0 else "rail_v")
        elif plat[y][x]:
            wr = inside(x - 1, y) and rail[y][x - 1] is not None; er = inside(x + 1, y) and rail[y][x + 1] is not None
            base[y][x] = f"platform_{int(wr)}{int(er)}"
        elif inside(x, y + 1) and not region[y + 1][x]:
            base[y][x] = f"stwall_{mask_EW(same_r, x, y)}"
        else:
            m = mask_NEW(same_r, x, y)
            base[y][x] = f"deckv_{m}" if h("dv", x, y) % 14 == 0 else f"deck_{m}"

# passages
for y in range(FULL):
    for x in range(FULL):
        if passage[y][x]:
            inside_building = region[y][x] or bld[y][x]
            solid[y][x] = False
            if inside_building:
                base[y][x] = f"passage_{h('p', x, y) % 2}"
                over[y][x] = "shade"
            else:
                base[y][x] = f"sidewalk_{h('s', x, y) % 2}"

# canopies (walkable, drawn above the player)
for y in range(FULL):
    for x in range(FULL):
        if canopy[y][x]: over[y][x] = f"canopy_{h('c', x, y) % 2}"

# ---------------------------------------------------------------- props from nodes / ways
def put_prop(x, y, name, allow_path=True):
    if not inside(x, y) or props[y][x]: return False
    if not free_ground(x, y, allow_path=allow_path) and not (name == "busstop" and not road[y][x] and not bld[y][x] and not region[y][x]): return False
    props[y][x] = name; deco[y][x] = name; solid[y][x] = True
    return True

def tile_of(n): return gx(n["xy"][0]), gy(n["xy"][1])
tree_n = 0
for n in sorted(nodes, key=lambda n: n["id"]):
    t = n["tags"]; x, y = tile_of(n)
    if t.get("natural") == "tree":
        if put_prop(x, y, f"tree_{h('t', n['id']) % 2}"): tree_n += 1
    elif t.get("amenity") == "bench": put_prop(x, y, "bench")
    elif t.get("amenity") == "bicycle_parking": put_prop(x, y, "bikes")
    elif t.get("amenity") == "vending_machine": put_prop(x, y, "vending")
    elif t.get("amenity") == "post_box": put_prop(x, y, "postbox")
    elif t.get("highway") == "bus_stop":
        for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            if put_prop(x + dx, y + dy, "busstop"): break
    elif t.get("tourism") == "information": put_prop(x, y, "signpost")
for w in ways:
    t = w["tags"]
    if t.get("amenity") == "bicycle_parking" and is_closed(w["pts"]):
        c = centroid(w["pts"]); put_prop(gx(c[0]), gy(c[1]), "bikes")
notes["trees"] = tree_n
# fountain jets: centre of water polygons from amenity=fountain
for t, i, ring, holes in polys:
    if t.get("amenity") == "fountain":
        c = centroid(ring); x, y = gx(c[0]), gy(c[1])
        if inside(x, y) and land[y][x] == "water": deco[y][x] = "jet"; notes["fountain_jets"] += 1

# street lamps: generated (OSM has none here) on plaza/sidewalk tiles of a coarse lattice, only where all 4 neighbours stay walkable
lamps = 0
for y in range(FULL):
    for x in range(FULL):
        if (x % 6 == 2) and (y % 6 == 3) and base[y][x].startswith(("plaza", "sidewalk")) and free_ground(x, y) and not props[y][x]:
            nb = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            if all(inside(a, b) and not solid[b][a] and not (road[b][a] is None and False) for a, b in nb) and not passage[y][x]:
                props[y][x] = "lamp"; deco[y][x] = "lamp"; solid[y][x] = True; lamps += 1
notes["lamps_generated"] = lamps

# ---------------------------------------------------------------- station signboards (objects, 3x2 tiles)
objects = []
def place_sign(ent, dirx):
    ex, ey = tile_of(ent)
    cands = []
    for sy in range(ey - 9, ey + 3):
        for sx in range(ex - 12, ex + 10):
            tiles = [(sx + a, sy + b) for a in range(3) for b in range(2)]
            if all(free_ground(a, b) and not props[b][a] for a, b in tiles):
                # keep one free tile ring on the south side so the player can reach it, and keep passage mouth clear
                if any(abs(a - ex) <= 1 and abs(b - ey) <= 3 for a, b in tiles): continue
                cx, cy = sx + 1, sy + 1
                cands.append((math.hypot(cx - (ex + dirx * 4), cy - (ey - 5)), sx, sy))
    if not cands: return None
    _, sx, sy = min(cands)
    for a in range(3):
        for b in range(2): solid[sy + b][sx + a] = True
    objects.append({"sprite": "sign_soka", "x": sx, "y": sy, "w": 3, "h": 2})
    return (sx, sy)
signs = [place_sign(west, -1), place_sign(east, 1)]

# ---------------------------------------------------------------- spawn
def walkable(x, y): return inside(x, y) and not solid[y][x]
ex, ey = tile_of(west)
spawn = None
# in front of the west entrance: same row as the passage mouth, 2-4 tiles west, with free tiles around
for dist in (3, 2, 4, 5, 6):
    x, y = ex - dist, ey
    if walkable(x, y) and walkable(x - 1, y) and walkable(x + 1, y) and walkable(x, y - 1) and walkable(x, y + 1):
        spawn = (x, y); break
if spawn is None:
    for r in range(0, 12):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r: continue
                x, y = ex - 3 + dx, ey + dy
                if walkable(x, y) and walkable(x - 1, y) and walkable(x + 1, y) and not passage[y][x]:
                    spawn = (x, y); break
            if spawn: break
        if spawn: break

# flood fill from spawn over the core window
seen = {spawn}; q = collections.deque([spawn])
core_ok = lambda x, y: OFF <= x < OFF + CORE and OFF <= y < OFF + CORE
while q:
    x, y = q.popleft()
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        a, b = x + dx, y + dy
        if core_ok(a, b) and walkable(a, b) and (a, b) not in seen: seen.add((a, b)); q.append((a, b))
core_walk = sum(1 for y in range(OFF, OFF + CORE) for x in range(OFF, OFF + CORE) if walkable(x, y))

# ---------------------------------------------------------------- overrides (hand fixes)
ov_path = ROOT / "data/overrides.json"
overrides = json.loads(ov_path.read_text()) if ov_path.exists() else {}
for s in overrides.get("set", []):   # coordinates in CORE tile units (0..79)
    x, y = s["x"] + OFF, s["y"] + OFF
    if "base" in s: base[y][x] = s["base"]
    if "deco" in s: deco[y][x] = s["deco"]
    if "over" in s: over[y][x] = s["over"]
    if "solid" in s: solid[y][x] = bool(s["solid"])

# ---------------------------------------------------------------- labels
def tile_core(n): return (gx(n["xy"][0]) - OFF, gy(n["xy"][1]) - OFF)
labels = []
wx, wy = tile_core(west); exx, exy = tile_core(east); sx_, sy_ = tile_core(station)
labels.append({"name": "草加駅 西口", "x": wx - 4, "y": wy + 2, "r": 11})
labels.append({"name": "草加駅 東口", "x": exx + 4, "y": exy + 2, "r": 11})
labels.append({"name": "草加駅", "x": sx_, "y": sy_, "r": 7})
for t, i, ring, holes in polys:
    nm = t.get("name")
    if nm in ("マルイ", "ヨーカドー", "ダイエー", "VARIE"):
        c = centroid(ring)
        r = 9 if nm != "VARIE" else 6
        labels.append({"name": nm, "x": gx(c[0]) - OFF, "y": gy(c[1]) - OFF, "r": r})

# ---------------------------------------------------------------- crop + write
def crop(g, f=lambda v: v):
    return [[f(g[y + OFF][x + OFF]) for x in range(CORE)] for y in range(CORE)]
idn = lambda v: TID[v] if v else -1
out = {
    "version": 1, "tile": 16, "cellMeters": CELL, "w": CORE, "h": CORE,
    "center": {"lat": LAT0, "lon": LON0},
    "attribution": "地図データ © OpenStreetMap contributors (ODbL)",
    "tileset": "tiles.png", "names": atlas["names"],
    "layers": {"base": crop(base, idn), "deco": crop(deco, idn), "over": crop(over, idn)},
    "solid": ["".join("1" if solid[y + OFF][x + OFF] else "0" for x in range(CORE)) for y in range(CORE)],
    "objects": [dict(o, x=o["x"] - OFF, y=o["y"] - OFF) for o in objects],
    "labels": labels,
    "spawn": {"x": spawn[0] - OFF, "y": spawn[1] - OFF, "dir": "right"},
    "source": raw.get("_source"), "fetched": raw["_meta"].get("fetched"),
}
(ROOT / "assets/map.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
report = {
    "window_tiles": [CORE, CORE], "window_meters": [CORE * CELL] * 2, "full_grid": FULL,
    "spawn_tile": out["spawn"], "walkable_tiles_in_window": core_walk, "reachable_from_spawn": len(seen),
    "reachable_ratio": round(len(seen) / core_walk, 3),
    "entrances_core_tiles": {"west": [wx, wy], "east": [exx, exy]}, "station_core_tile": [sx_, sy_],
    "signs": [list(s) if s else None for s in signs], "counts": dict(notes),
    "viaduct": "railway=rail with bridge=viaduct layer 1/2; inside the VARIE footprint (station building). Solid; crossed only via building_passage/corridor tiles",
}
(ROOT / "data/build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps(report, ensure_ascii=False, indent=1))
