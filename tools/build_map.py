#!/usr/bin/env python3
"""OSM (data/osm_raw.json) -> tile grid + object list -> assets/map.json

Scale: 3 m per tile, 16x16 px tiles.  The playable map is 270 x 320 tiles (810 m x 960 m): x from -390 m to +420 m
and y from -480 m (north) to +480 m (south) around the station node (x east, y south).
Everything is rasterised on a grid that is PAD tiles larger on every side (so walls / edges at the map border are right)
and then cropped.

Pipeline:  parse -> metres -> rasterise layers (land, paths, roads, rail, platforms, buildings, passages)
           -> rail crossings (roads/paths passing UNDER the elevated line) -> shopping-street detection
           -> autotile -> props / signs -> collision -> overrides (data/overrides.json)
           -> assets/map.json (+ data/build_report.json)

Usage: python3 tools/build_map.py
"""
import json, math, hashlib, collections, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_art
CELL = 3.0
CORE_X = (-390.0, 420.0)           # playable extent in metres from the station node (x east)
CORE_Y = (-480.0, 480.0)           # (y south)
CW = int(round((CORE_X[1] - CORE_X[0]) / CELL))   # 270 tiles
CH = int(round((CORE_Y[1] - CORE_Y[0]) / CELL))   # 320 tiles
PAD = 12                            # extra border tiles used while rasterising
OFF = PAD
FW, FH = CW + 2 * PAD, CH + 2 * PAD
GX0 = CORE_X[0] - PAD * CELL
GY0 = CORE_Y[0] - PAD * CELL
raw = json.loads((ROOT / "data/osm_raw.json").read_text())
LAT0, LON0 = raw["_meta"]["center"]
atlas = json.loads((ROOT / "assets/tiles.json").read_text())
TID = {n: i for i, n in enumerate(atlas["names"])}

def to_m(p):
    return ((p["lon"] - LON0) * 111320.0 * math.cos(math.radians(LAT0)), -(p["lat"] - LAT0) * 111320.0)

def h(*a):
    return int(hashlib.md5("|".join(map(str, a)).encode()).hexdigest()[:8], 16)

def gx(x): return int(math.floor((x - GX0) / CELL))
def gy(y): return int(math.floor((y - GY0) / CELL))
def inside(x, y): return 0 <= x < FW and 0 <= y < FH

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
    x0, x1 = max(0, gx(min(xs))), min(FW - 1, gx(max(xs)))
    y0, y1 = max(0, gy(min(ys))), min(FH - 1, gy(max(ys)))
    if x1 < x0 or y1 < y0: return set()
    sub = np.array([-1.0, 0.0, 1.0])
    gxs = np.arange(x0, x1 + 1); gys = np.arange(y0, y1 + 1)
    cx = (gxs + 0.5) * CELL + GX0; cy = (gys + 0.5) * CELL + GY0
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
                    fy = ((y - GY0) / CELL) - math.floor((y - GY0) / CELL)
                    ex = [(t[0], t[1] - 1 if fy < 0.5 else t[1] + 1)]
                    if n >= 3: ex = [(t[0], t[1] - 1), (t[0], t[1] + 1)]
                else:
                    fx = ((x - GX0) / CELL) - math.floor((x - GX0) / CELL)
                    ex = [(t[0] - 1 if fx < 0.5 else t[0] + 1, t[1])]
                    if n >= 3: ex = [(t[0] - 1, t[1]), (t[0] + 1, t[1])]
                for e2 in ex: res.append((e2, (dx, dy)))
            prev = t
    return res

# ---------------------------------------------------------------- grids
G = lambda v=None: [[v for _ in range(FW)] for _ in range(FH)]
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
rname = G(None)              # street name per road/path tile (public street names only)
under = G(False)             # road / path tiles that pass UNDER the elevated railway
ss = G(False)                # shopping-street tiles
ssname = G(None)
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
            if inside(x, y):
                path[y][x] = True
                if t.get("name"): rname[y][x] = t["name"]
    elif hw in ROADW:
        n = ROADW[hw]
        if t.get("lanes") and t["lanes"].isdigit() and int(t["lanes"]) >= 4: n = 3
        seq = walk_tiles(w["pts"], n)
        for (x, y), d in seq:
            if inside(x, y):
                road[y][x] = hw
                if t.get("name"): rname[y][x] = t["name"]
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
for y in range(FH):
    for x in range(FW):
        if stationpoly[y][x] or rail[y][x] or plat[y][x] or railaux[y][x]: region[y][x] = True
        if rail[y][x]: bld[y][x] = 0

# passages (building passages, indoor corridors) - walkable even inside buildings / the station deck
for w in passage_ways:
    for (x, y), _ in walk_tiles(w["pts"], 1):
        if inside(x, y): passage[y][x] = True
notes["passage_tiles"] = sum(sum(r) for r in passage)

# ---------------------------------------------------------------- rail crossings (roads / paths UNDER the elevated line)
# The whole Tobu line here is a viaduct (bridge=viaduct), and OSM has no level crossings, so a road or path whose
# centre line geometrically crosses a rail way (without sharing a node) runs underneath it.  Those tiles stay walkable.
def seg_cross(a, b, c, d):
    def o(p, q, r): return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0
rail_segs = []
for w in ways:
    t = w["tags"]
    if t.get("railway") in RAILS and t.get("tunnel") not in ("yes", "building_passage"):
        for a, b in zip(w["pts"], w["pts"][1:]):
            if min(a[0], b[0]) < CORE_X[1] + 60 and max(a[0], b[0]) > CORE_X[0] - 60 and min(a[1], b[1]) < CORE_Y[1] + 60 and max(a[1], b[1]) > CORE_Y[0] - 60:
                rail_segs.append((a, b))
cross_pts = []          # (x_m, y_m, way_id, highway, name)
cross_tiles = G(False)
for w in ways:
    t = w["tags"]; hw = t.get("highway")
    if not hw or t.get("tunnel") == "building_passage" or hw == "corridor" or t.get("indoor") == "yes": continue
    if hw not in ROADW and hw not in PATHS: continue
    if hw == "footway" and t.get("footway") == "crossing": continue
    hit = []
    for a, b in zip(w["pts"], w["pts"][1:]):
        for c, d in rail_segs:
            if max(a[0], b[0]) < min(c[0], d[0]) - 1 or min(a[0], b[0]) > max(c[0], d[0]) + 1 or max(a[1], b[1]) < min(c[1], d[1]) - 1 or min(a[1], b[1]) > max(c[1], d[1]) + 1: continue
            if seg_cross(a, b, c, d):
                # intersection point
                den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
                u = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den
                hit.append((a[0] + u * (b[0] - a[0]), a[1] + u * (b[1] - a[1])))
    if not hit: continue
    n = ROADW.get(hw, 1) if hw in ROADW else 1
    for (x, y), _ in walk_tiles(w["pts"], n):
        if inside(x, y) and any(math.hypot(gx(px_) - x, gy(py_) - y) <= 9 for px_, py_ in hit): cross_tiles[y][x] = True
    for px_, py_ in hit:
        cross_pts.append((px_, py_, w["id"], hw, t.get("name")))
for y in range(FH):
    for x in range(FW):
        if cross_tiles[y][x] and (road[y][x] or path[y][x]) and (rail[y][x] or railaux[y][x] or plat[y][x]) and not stationpoly[y][x] and not passage[y][x]:
            under[y][x] = True
notes["rail_crossings_ways"] = len(set(c[2] for c in cross_pts))
notes["under_tiles"] = sum(sum(r) for r in under)

# ---------------------------------------------------------------- stations / entrances / spawn
station = [n for n in nodes if n["tags"].get("railway") == "station"][0]
entrances = [n for n in nodes if n["tags"].get("railway") == "train_station_entrance" and math.hypot(*n["xy"]) < 200]
entrances.sort(key=lambda n: n["xy"][0])
west, east = entrances[0], entrances[-1]

def free_ground(x, y, allow_path=False):
    return (inside(x, y) and not road[y][x] and not bld[y][x] and not region[y][x] and not passage[y][x]
            and land[y][x] in ("ground", "grass", "sand") and (allow_path or not path[y][x]) and not canopy[y][x])

# ---------------------------------------------------------------- shopping streets (商店街)
# OSM has no street tagged 商店街 / pedestrian mall near the station, so they are detected from the data:
# a local street or footpath tile (not a through road, not inside the station building) belongs to a "shopping street"
# when at least SS_MIN shop / restaurant / cafe / bank POIs lie within SS_R tiles (15 m); connected runs of
# at least SS_LEN tiles are kept.  Shop names are NOT stored - only the positions are used.
SHOP_AMENITY = {"restaurant", "cafe", "fast_food", "bar", "pub", "bank", "pharmacy"}
SS_R, SS_MIN, SS_LEN = 5, 2, 14
shop_pts = []
def is_shop(t): return ("shop" in t and t["shop"] not in ("department_store", "mall")) or t.get("amenity") in SHOP_AMENITY
for n in nodes:
    if is_shop(n["tags"]): shop_pts.append((gx(n["xy"][0]), gy(n["xy"][1])))
for w in ways:
    if is_shop(w["tags"]):
        c = centroid(w["pts"]); shop_pts.append((gx(c[0]), gy(c[1])))
shop_pts = [(x, y) for x, y in shop_pts if inside(x, y) and not stationpoly[y][x] and not region[y][x]]
dens = np.zeros((FH, FW), dtype=np.int16)
yy, xx = np.mgrid[-SS_R:SS_R + 1, -SS_R:SS_R + 1]
disk = (xx * xx + yy * yy) <= SS_R * SS_R
for x, y in shop_pts:
    for dy in range(-SS_R, SS_R + 1):
        for dx in range(-SS_R, SS_R + 1):
            if disk[dy + SS_R, dx + SS_R] and inside(x + dx, y + dy): dens[y + dy, x + dx] += 1
LOCAL = {"unclassified", "residential", "service", "living_street", "pedestrian", "road"}
cand = G(False)
for y in range(FH):
    for x in range(FW):
        if road[y][x] in LOCAL or (path[y][x] and not road[y][x]):
            if dens[y, x] >= SS_MIN and not region[y][x] and not passage[y][x] and land[y][x] != "water" and not under[y][x]:
                cand[y][x] = True
# close 1-tile gaps along a street, then connected components
for _ in range(2):
    add_ = []
    for y in range(1, FH - 1):
        for x in range(1, FW - 1):
            if not cand[y][x] and (road[y][x] in LOCAL or path[y][x]) and not region[y][x] and dens[y, x] >= 1:
                if (cand[y][x - 1] and cand[y][x + 1]) or (cand[y - 1][x] and cand[y + 1][x]): add_.append((x, y))
    for x, y in add_: cand[y][x] = True
seen_c = set(); ss_comps = []
for y in range(FH):
    for x in range(FW):
        if cand[y][x] and (x, y) not in seen_c:
            comp = [(x, y)]; seen_c.add((x, y)); q = collections.deque([(x, y)])
            while q:
                cx_, cy_ = q.popleft()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        a, b = cx_ + dx, cy_ + dy
                        if (dx or dy) and inside(a, b) and cand[b][a] and (a, b) not in seen_c:
                            seen_c.add((a, b)); comp.append((a, b)); q.append((a, b))
            ss_comps.append(comp)
ss_info = []
for comp in sorted(ss_comps, key=len, reverse=True):
    if len(comp) < SS_LEN: continue
    names = collections.Counter(rname[y][x] for x, y in comp if rname[y][x])
    nm = names.most_common(1)[0][0] if names else None
    cid = len(ss_info)
    for x, y in comp: ss[y][x] = True; ssname[y][x] = cid
    xs_ = [p[0] for p in comp]; ys_ = [p[1] for p in comp]
    nshop = sum(1 for sx, sy in shop_pts if any(abs(sx - x) <= SS_R and abs(sy - y) <= SS_R for x, y in comp[::3]))
    ss_info.append({"name": nm, "tiles": len(comp), "bbox_core_tiles": [min(xs_) - OFF, min(ys_) - OFF, max(xs_) - OFF, max(ys_) - OFF],
                    "centre_m": [round((sum(xs_) / len(xs_) + 0.5) * CELL + GX0), round((sum(ys_) / len(ys_) + 0.5) * CELL + GY0)], "shops_nearby": nshop})
notes["shopping_street_tiles"] = sum(sum(r) for r in ss)
print("shopping street components:", json.dumps(ss_info, ensure_ascii=False))

# ---------------------------------------------------------------- compose layers
base = G(None); deco = G(None); over = G(None); solid = G(False)
def T_(name): return TID[name]

near_road = G(False)
for y in range(FH):
    for x in range(FW):
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

for y in range(FH):
    for x in range(FW):
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
        if ss[y][x]: b = f"shopst_{r % 3}"
        base[y][x] = b
        if l == "water" or (bld[y][x] and not passage[y][x]): solid[y][x] = True

base0 = [row[:] for row in base]
for sx_, sy_ in shop_pts:
    if bld[sy_][sx_]: binfo[bld[sy_][sx_]]['has_shop'] = True

# houses and mid-rises (roof rows over wall rows)
same_b = lambda i: (lambda x, y: inside(x, y) and bld[y][x] == i)
for y in range(FH):
    for x in range(FW):
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
            if inf.get("has_shop") or (inside(x, y + 1) and ss[y + 1][x]) or (inside(x, y + 2) and ss[y + 2][x]):
                # shop front: coloured awning over a display window (one colour per building)
                c = h("awn", i) % 6
                kind = "door" if (x == door_x and L >= 2) else ("sign" if (x - x0) % 4 == 3 else "shop")
                base[y][x] = f"sf{c}_{kind}_{m}"
                continue
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
for y in range(FH):
    for x in range(FW):
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
for y in range(FH):
    for x in range(FW):
        if passage[y][x]:
            inside_building = region[y][x] or bld[y][x]
            solid[y][x] = False
            if inside_building:
                base[y][x] = f"passage_{h('p', x, y) % 2}"
                over[y][x] = "shade"
            else:
                base[y][x] = f"sidewalk_{h('s', x, y) % 2}"

# roads / paths passing under the elevated line: walkable, shaded, drawn under the viaduct
for y in range(FH):
    for x in range(FW):
        if under[y][x]:
            solid[y][x] = False
            base[y][x] = base0[y][x] if base0[y][x].startswith(("road", "zebra", "sidewalk", "plaza", "shopst")) else f"road_{h('u', x, y) % 2}"
            over[y][x] = f"under_{h('u2', x, y) % 2}"

# canopies (walkable, drawn above the player)
for y in range(FH):
    for x in range(FW):
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
for y in range(FH):
    for x in range(FW):
        if (x % 6 == 2) and (y % 6 == 3) and base[y][x].startswith(("plaza", "sidewalk")) and free_ground(x, y) and not props[y][x]:
            nb = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
            if all(inside(a, b) and not solid[b][a] and not (road[b][a] is None and False) for a, b in nb) and not passage[y][x]:
                props[y][x] = "lamp"; deco[y][x] = "lamp"; solid[y][x] = True; lamps += 1
notes["lamps_generated"] = lamps

# ---------------------------------------------------------------- shopping-street furniture
def walkable(x, y): return inside(x, y) and not solid[y][x]
def deco_ok(x, y):
    """free tile for street furniture, none of the 4 neighbours blocked by it (keeps lanes open)"""
    if props[y][x] or deco[y][x] or passage[y][x] or region[y][x]: return False
    if solid[y][x]: return False
    if not (base[y][x].startswith(("shopst", "sidewalk", "plaza", "ground")) ): return False
    return True
decor_kinds = ["lamp_shop", "flag_0", "bikes", "lamp_shop", "flag_1", "flag_2"]
decor_n = collections.Counter(); k_ = 0
placed_decor = []
for y in range(FH):
    for x in range(FW):
        if not ss[y][x] or not deco_ok(x, y): continue
        # candidate: an edge tile of a street that is at least 2 wide, so one lane stays open
        nb = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        free_nb = [p for p in nb if walkable(*p) and ss[p[1]][p[0]]]
        blocked_nb = [p for p in nb if not walkable(*p) or not ss[p[1]][p[0]]]
        if len(free_nb) < 3 or not blocked_nb: continue
        if any(math.hypot(x - a, y - b) < 4.5 for a, b in placed_decor[-12:]): continue
        if any(math.hypot(x - a, y - b) < 4.5 for a, b in placed_decor if abs(a - x) < 6 and abs(b - y) < 6): continue
        if h("decor", x, y) % 3 != 0: continue
        nm = decor_kinds[k_ % len(decor_kinds)]; k_ += 1
        props[y][x] = nm; deco[y][x] = nm; solid[y][x] = True; placed_decor.append((x, y)); decor_n[nm] += 1
# ground tiles right in front of shop fronts (sidewalk strip): bike racks and flags
for y in range(FH):
    for x in range(FW):
        if ss[y][x] or not deco_ok(x, y) or not base[y][x].startswith(("sidewalk", "ground", "plaza")): continue
        if not any(inside(x + dx, y + dy) and ss[y + dy][x + dx] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))): continue
        if not all(walkable(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))): continue
        if any(math.hypot(x - a, y - b) < 4 for a, b in placed_decor if abs(a - x) < 6 and abs(b - y) < 6): continue
        if h("decor2", x, y) % 4 != 0: continue
        nm = ["bikes", "flag_0", "flag_2", "bikes"][k_ % 4]; k_ += 1
        props[y][x] = nm; deco[y][x] = nm; solid[y][x] = True; placed_decor.append((x, y)); decor_n[nm] += 1
notes["shopping_street_props"] = dict(decor_n)

# ---------------------------------------------------------------- signboards (objects, w x 2 tiles) and street-name plates
objects = []
sign_images = []      # (PIL image, w_tiles) packed into assets/signs.png
def sign_for(text, style):
    im = make_art.text_sign(text, style)
    sign_images.append(im)
    return len(sign_images) - 1
def place_block(idx, near, rmax, tile_w, avoid=None):
    nx, ny = near
    cands = []
    for sy in range(ny - rmax, ny + rmax + 1):
        for sx in range(nx - rmax - tile_w, nx + rmax + 1):
            tiles = [(sx + a, sy + b) for a in range(tile_w) for b in range(2)]
            if not all(inside(a, b) and free_ground(a, b) and not props[b][a] and not solid[b][a] for a, b in tiles): continue
            if avoid and any(abs(a - avoid[0]) <= 1 and abs(b - avoid[1]) <= 3 for a, b in tiles): continue
            below = [(sx + a, sy + 2) for a in range(tile_w)]
            if not any(walkable(a, b) and not passage[b][a] for a, b in below): continue
            cands.append((math.hypot(sx + tile_w / 2 - nx, sy + 1 - ny), sx, sy))
    if not cands: return None
    _, sx, sy = min(cands)
    for a in range(tile_w):
        for b in range(2): solid[sy + b][sx + a] = True
    objects.append({"sprite": "signs", "idx": idx, "x": sx, "y": sy, "w": tile_w, "h": 2})
    return (sx, sy)

def place_sign(ent, dirx):      # the two station signboards (3x2)
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

st_tile = tile_of(station)
street_signs = []
# shopping streets: one plate at the end nearest the station ("商店街" or the OSM name when it has one)
for cid, info in enumerate(ss_info):
    tiles_c = [(x, y) for y in range(FH) for x in range(FW) if ss[y][x] and ssname[y][x] == cid]
    if info["tiles"] < 18: continue
    near = min(tiles_c, key=lambda p: math.hypot(p[0] - st_tile[0], p[1] - st_tile[1]))
    txt = info["name"] or "商店街"
    wt = max(3, math.ceil((len(txt) * 12 + 10) / 16))
    p = place_block(sign_for(txt, "shop"), near, 5, wt)
    if p: street_signs.append((txt, p))
# main roads: a green plate for each public street name, on each side of the railway where the street exists
SIGN_STREETS = ["草加停車場線", "草加神社通り", "日光街道"]
for nm in SIGN_STREETS:
    tl = [(x, y) for y in range(OFF, OFF + CH) for x in range(OFF, OFF + CW) if rname[y][x] == nm and road[y][x]]
    for side in (-1, 1):
        sub = [p for p in tl if (p[0] - st_tile[0]) * side > 6]
        if not sub: continue
        near = min(sub, key=lambda p: math.hypot(p[0] - st_tile[0], p[1] - st_tile[1]) + (0 if abs(p[1] - st_tile[1]) < 60 else 40))
        if math.hypot(near[0] - st_tile[0], near[1] - st_tile[1]) > 150: continue
        wt = max(3, math.ceil((len(nm) * 12 + 10) / 16))
        p = place_block(sign_for(nm, "road"), near, 6, wt)
        if p: street_signs.append((nm, p))
notes["street_signs"] = len(street_signs)
# pack the sign images into one sheet
if sign_images:
    sw = max(im.width for im in sign_images); sh_ = sum(im.height for im in sign_images)
    from PIL import Image as _I
    sheet = _I.new("RGBA", (sw, sh_), (0, 0, 0, 0)); yy_ = 0; sign_pos = []
    for im in sign_images:
        sheet.paste(im, (0, yy_)); sign_pos.append((0, yy_)); yy_ += im.height
    sheet.save(ROOT / "assets/signs.png")
    for o in objects:
        if o["sprite"] == "signs": o["sx"], o["sy"] = sign_pos[o["idx"]]; del o["idx"]
else:
    (ROOT / "assets/signs.png").unlink(missing_ok=True)

# ---------------------------------------------------------------- spawn
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

# ---------------------------------------------------------------- overrides (hand fixes)
ov_path = ROOT / "data/overrides.json"
overrides = json.loads(ov_path.read_text()) if ov_path.exists() else {}
for s_ in overrides.get("set", []):   # coordinates in map tile units (0..CW-1, 0..CH-1)
    x, y = s_["x"] + OFF, s_["y"] + OFF
    if "base" in s_: base[y][x] = s_["base"]
    if "deco" in s_: deco[y][x] = s_["deco"]
    if "over" in s_: over[y][x] = s_["over"]
    if "solid" in s_: solid[y][x] = bool(s_["solid"])

# ---------------------------------------------------------------- reachability (flood fill over the whole map)
core_ok = lambda x, y: OFF <= x < OFF + CW and OFF <= y < OFF + CH
def flood(start, allow=lambda x, y: True):
    seen_ = {start}; q_ = collections.deque([start])
    while q_:
        x, y = q_.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = x + dx, y + dy
            if core_ok(a, b) and walkable(a, b) and allow(a, b) and (a, b) not in seen_: seen_.add((a, b)); q_.append((a, b))
    return seen_
seen = flood(spawn)
core_walk = sum(1 for y in range(OFF, OFF + CH) for x in range(OFF, OFF + CW) if walkable(x, y))
# without the station passages: are the two sides still connected (by the under-viaduct crossings)?
no_pass = lambda x, y: not (passage[y][x] and (region[y][x] or bld[y][x]))
seen_nopass = flood(spawn, no_pass)
east_tile = tile_of(east); east_probe = None
for dist in range(3, 10):
    if walkable(east_tile[0] + dist, east_tile[1]): east_probe = (east_tile[0] + dist, east_tile[1]); break
# cut-off areas
rest = set((x, y) for y in range(OFF, OFF + CH) for x in range(OFF, OFF + CW) if walkable(x, y)) - seen
comps = []; left = set(rest)
while left:
    p0 = next(iter(left)); comp = flood(p0); comp &= left; left -= comp
    if len(comp) < 6: continue
    xs_ = [p[0] for p in comp]; ys_ = [p[1] for p in comp]
    cnt = collections.Counter(base[y][x].split("_")[0] for x, y in comp)
    comps.append({"tiles": len(comp), "centre_m": [round((sum(xs_) / len(xs_) + .5) * CELL + GX0), round((sum(ys_) / len(ys_) + .5) * CELL + GY0)],
                  "bbox_tiles": [min(xs_) - OFF, min(ys_) - OFF, max(xs_) - OFF, max(ys_) - OFF], "ground": cnt.most_common(3)})
comps.sort(key=lambda c: -c["tiles"])
cross_report = []
for px_, py_, wid, hw, nm in sorted(set(cross_pts), key=lambda c: c[1]):
    cross_report.append({"way": wid, "kind": hw, "name": nm, "at_m": [round(px_), round(py_)]})
# one entry per way
byway = {}
for c in cross_report: byway.setdefault(c["way"], c)
cross_report = sorted(byway.values(), key=lambda c: c["at_m"][1])
for c in cross_report:
    gxx, gyy = gx(c["at_m"][0]), gy(c["at_m"][1])
    c["under_tiles_near"] = sum(1 for dy in range(-6, 7) for dx in range(-6, 7) if inside(gxx + dx, gyy + dy) and under[gyy + dy][gxx + dx])
    c["in_map"] = core_ok(gxx, gyy)
    c["reachable_from_spawn"] = any((gxx + dx, gyy + dy) in seen for dy in range(-6, 7) for dx in range(-6, 7) if under[gyy + dy][gxx + dx])
    c["reachable_without_station_passage"] = any((gxx + dx, gyy + dy) in seen_nopass for dy in range(-6, 7) for dx in range(-6, 7) if under[gyy + dy][gxx + dx])

# ---------------------------------------------------------------- labels (a label id per tile, run-length encoded)
def tile_core(n): return (gx(n["xy"][0]) - OFF, gy(n["xy"][1]) - OFF)
lab_names = []; lab_idx = {}
def lid(name):
    if name not in lab_idx: lab_idx[name] = len(lab_names) + 1; lab_names.append(name)
    return lab_idx[name]
labid = np.zeros((FH, FW), dtype=np.uint8); labpri = np.zeros((FH, FW), dtype=np.float32)
def paint(x, y, p, name):
    if inside(x, y) and p >= labpri[y, x]: labid[y, x] = lid(name); labpri[y, x] = p
def disc(cx, cy, r, name, p_core, p_out, core=0.55):
    for yy in range(int(cy - r) - 1, int(cy + r) + 2):
        for xx in range(int(cx - r) - 1, int(cx + r) + 2):
            d = math.hypot(xx - cx, yy - cy)
            if d <= r: paint(xx, yy, p_core if d / r <= core else p_out, name)
def dilate_paint(tiles, p, name, r):
    for (x, y) in tiles:
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1): paint(x + dx, y + dy, p, name)
wxx, wyy = tile_of(west); exx_, exy_ = tile_of(east); stx, sty = tile_of(station)
disc(wxx - 4, wyy + 2, 11, "草加駅 西口", 6, 3)
disc(exx_ + 4, exy_ + 2, 11, "草加駅 東口", 6, 3)
disc(stx, sty, 7, "草加駅", 6, 3)
LAND_AM = {"townhall", "library", "school", "police", "post_office", "community_centre", "fire_station", "place_of_worship", "kindergarten"}
n_land = 0
for t, i, ring, holes in polys:
    nm = t.get("name")
    if not nm: continue
    if nm in ("マルイ", "ヨーカドー", "ダイエー", "VARIE"):
        c = centroid(ring); disc(gx(c[0]), gy(c[1]), 9 if nm != "VARIE" else 6, nm, 5, 2, core=0.5)
    elif t.get("amenity") in LAND_AM or t.get("leisure") == "park":
        tl = raster_poly(ring, holes, thresh=2)
        if not tl:
            c = centroid(ring); tl = {(gx(c[0]), gy(c[1]))}
        if len(tl) > 4000: continue
        tl = [(x, y) for (x, y) in tl if inside(x, y)]
        for (x, y) in tl: paint(x, y, 5, nm)
        dilate_paint(tl, 1, nm, 2); n_land += 1
for n_ in nodes:
    t = n_["tags"]
    if t.get("name") and t.get("amenity") in LAND_AM:
        x, y = tile_of(n_); disc(x, y, 4, t["name"], 5, 1)
# streets
named = collections.defaultdict(list)
for y in range(FH):
    for x in range(FW):
        if rname[y][x] and (road[y][x] or path[y][x]): named[rname[y][x]].append((x, y))
for nm, tl in named.items():
    for (x, y) in tl: paint(x, y, 4, nm)
    dilate_paint(tl, 2.5, nm, 2)
for cid, info in enumerate(ss_info):
    tl = [(x, y) for y in range(FH) for x in range(FW) if ss[y][x] and ssname[y][x] == cid]
    nm = info["name"] or "商店街"
    for (x, y) in tl: paint(x, y, 4.5, nm)
    dilate_paint(tl, 2.6, nm, 2)
# under-viaduct crossings
for y in range(FH):
    for x in range(FW):
        if under[y][x] and labpri[y, x] < 4.2 and rname[y][x]: paint(x, y, 4.2, rname[y][x])

# ---------------------------------------------------------------- crop + write
def rle(flat):
    out_ = []; prev = None; n = 0
    for v in flat:
        if v == prev: n += 1
        else:
            if prev is not None: out_ += [prev, n]
            prev, n = v, 1
    out_ += [prev, n]
    return out_
def crop_flat(g, f=lambda v: v):
    return [f(g[y + OFF][x + OFF]) for y in range(CH) for x in range(CW)]
idn = lambda v: TID[v] if v else -1
lab_flat = [int(labid[y + OFF, x + OFF]) for y in range(CH) for x in range(CW)]
used = sorted(set(lab_flat) - {0}); remap = {0: 0}
for k_, u in enumerate(used, 1): remap[u] = k_
lab_names = [lab_names[u - 1] for u in used]
lab_flat = [remap[v] for v in lab_flat]
out = {
    "version": 2, "tile": 16, "cellMeters": CELL, "w": CW, "h": CH,
    "extentMeters": {"x": list(CORE_X), "y": list(CORE_Y)},
    "center": {"lat": LAT0, "lon": LON0},
    "attribution": "地図データ © OpenStreetMap contributors (ODbL)",
    "tileset": "tiles.png", "names": atlas["names"],
    "encoding": "row-major run-length [value,count,...]",
    "layers": {"base": rle(crop_flat(base, idn)), "deco": rle(crop_flat(deco, idn)), "over": rle(crop_flat(over, idn))},
    "solid": rle(crop_flat(solid, lambda v: 1 if v else 0)),
    "labelNames": lab_names, "labelGrid": rle(lab_flat),
    "objects": [dict(o, x=o["x"] - OFF, y=o["y"] - OFF) for o in objects],
    "station": {"x": stx - OFF, "y": sty - OFF},
    "spawn": {"x": spawn[0] - OFF, "y": spawn[1] - OFF, "dir": "right"},
    "source": raw.get("_source"), "fetched": raw["_meta"].get("fetched"),
}
(ROOT / "assets/map.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
report = {
    "map_tiles": [CW, CH], "map_meters": [CW * CELL, CH * CELL], "extent_m": {"x": list(CORE_X), "y": list(CORE_Y)}, "raster_grid": [FW, FH],
    "spawn_tile": out["spawn"], "walkable_tiles": core_walk, "reachable_from_spawn": len(seen),
    "reachable_ratio": round(len(seen) / core_walk, 4),
    "reachable_ratio_without_station_passages": round(len(seen_nopass) / core_walk, 4),
    "east_side_reachable_without_station_passages": bool(east_probe and east_probe in seen_nopass),
    "cut_off_areas": comps[:15], "cut_off_total_tiles": len(rest),
    "rail_crossings": cross_report,
    "shopping_streets": ss_info,
    "street_signs": [[a, [p[0] - OFF, p[1] - OFF]] for a, p in street_signs],
    "entrances_core_tiles": {"west": list(tile_core(west)), "east": list(tile_core(east))}, "station_core_tile": list(tile_core(station)),
    "signs": [[s_[0] - OFF, s_[1] - OFF] if s_ else None for s_ in signs], "counts": dict(notes), "labels": lab_names,
    "viaduct": "the whole line is railway=rail bridge=viaduct (OSM). Solid; crossed via building_passage/corridor tiles in the station and by roads/paths passing under (see rail_crossings)",
}
(ROOT / "data/build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
print(json.dumps({k: v for k, v in report.items() if k not in ("labels",)}, ensure_ascii=False, indent=1))
