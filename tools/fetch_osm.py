#!/usr/bin/env python3
"""Fetch OpenStreetMap data around Soka Station (about 0.9 km x 1.05 km) and save it under data/osm_raw.json.

The area is split into small bboxes and fetched from the OSM API 0.6 /map (each request is limited to
0.25 sq deg and 50000 nodes), then merged and de-duplicated by id and converted to an Overpass-like
"out geom" JSON, which tools/build_map.py reads.  (Overpass was tried first in the 1st version, but its public
mirrors are often unavailable, so the API route is the default.)

Station centre: node railway=station (the OSM name is "草加", not "草加駅"), id 7481965874.

Usage: python3 tools/fetch_osm.py [--cache DIR]
  --cache DIR  keep the raw API XML there and reuse it (default /tmp/soka_osm_xml, OUTSIDE the repo:
               the raw XML contains OSM user names / changesets, which must never be stored in the repo).
Tags are sanitised before saving (whitelist of keys; names only for the station, streets and public landmarks).
"""
import json, sys, time, math, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

UA = "soka-map-mvp/0.2 (local build; contact shingo.tanaka@mail.grokbot.com)"
DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)

STATION_NODE_ID = 7481965874          # railway=station, name=草加, ref TS16
STATION_FALLBACK = (35.8282516, 139.8033621)
# fetch rectangle in metres from the station (x east, y south); the playable map is a bit smaller (see build_map.py)
FETCH_X = (-470, 480)
FETCH_Y = (-540, 540)
STEP_DEG = 0.0027                     # tile size of one API request (about 240 m x 300 m)

def m2deg(lat0, x, y):
    return (lat0 - y / 111320.0, STATION_FALLBACK[1] + x / (111320.0 * math.cos(math.radians(lat0))))

def http(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def tile_boxes():
    lat0 = STATION_FALLBACK[0]
    s, w = m2deg(lat0, FETCH_X[0], FETCH_Y[1])   # south-west corner
    n, e = m2deg(lat0, FETCH_X[1], FETCH_Y[0])   # north-east corner
    nlat = max(1, math.ceil((n - s) / STEP_DEG)); nlon = max(1, math.ceil((e - w) / STEP_DEG))
    for i in range(nlat):
        for j in range(nlon):
            yield (i, j, (w + (e - w) * j / nlon, s + (n - s) * i / nlat, w + (e - w) * (j + 1) / nlon, s + (n - s) * (i + 1) / nlat)), (s, w, n, e)

def fetch_tiles(cache):
    cache.mkdir(parents=True, exist_ok=True)
    blobs = []
    for (i, j, b), _ in tile_boxes():
        fn = cache / f"t_{i}_{j}.xml"
        if fn.exists() and fn.stat().st_size > 0:
            blobs.append(fn.read_bytes()); continue
        url = "https://api.openstreetmap.org/api/0.6/map?bbox=%.6f,%.6f,%.6f,%.6f" % b
        print("OSM API:", url, flush=True)
        for k in range(4):
            try:
                raw = http(url); break
            except Exception as e:
                print("  failed:", repr(e)[:120], flush=True); time.sleep(5 * (k + 1)); raw = None
        if raw is None: raise SystemExit("OSM API failed")
        fn.write_bytes(raw); blobs.append(raw); time.sleep(1)
    return blobs

# tags that matter for the map (everything else is dropped); see sanitize_tags for names
KEEP_KEYS = {
    "highway", "railway", "bridge", "layer", "tunnel", "indoor", "level", "building", "building:levels",
    "landuse", "leisure", "natural", "water", "waterway", "amenity", "barrier", "service", "width", "lanes",
    "oneway", "covered", "area", "public_transport", "footway", "crossing", "shop", "tourism", "entrance",
    "surface", "electrified", "ref", "bus", "type", "bicycle_parking", "capacity", "usage", "man_made",
}
WAY_KEYS = ("highway", "area:highway", "railway", "building", "landuse", "leisure", "natural", "waterway", "water")
def way_wanted(t):
    return (any(k in t for k in WAY_KEYS) or t.get("amenity") in ("parking", "bicycle_parking", "fountain", "shelter")
            or t.get("public_transport") == "platform" or "shop" in t or t.get("amenity") in ("restaurant", "cafe", "fast_food", "bar", "pub", "bank", "townhall", "school", "police", "library"))
NODE_AMENITY = {"bench", "bicycle_parking", "restaurant", "cafe", "fast_food", "bar", "pub", "bank", "post_box", "vending_machine",
                "police", "townhall", "library", "school", "fountain", "pharmacy", "clothes", "bicycle_rental"}
def node_wanted(t):
    return (t.get("natural") == "tree" or t.get("highway") in ("street_lamp", "bus_stop", "traffic_signals", "crossing")
            or t.get("amenity") in NODE_AMENITY or "shop" in t or t.get("railway") in ("station", "level_crossing", "train_station_entrance", "stop", "subway_entrance")
            or t.get("tourism") == "information" or t.get("entrance") is not None or t.get("man_made") in ("street_lamp",))

# ---- names: kept ONLY for the station, streets (public street names) and public landmarks. Never shops, never people.
PUBLIC_NAMES = {"草加", "草加駅", "東武伊勢崎線", "草加停車場線", "マルイ", "ヨーカドー", "ダイエー", "VARIE",
                "草加駅西口", "草加駅東口", "草加駅南"}
LANDMARK_AMENITY = {"townhall", "library", "school", "police", "post_office", "community_centre", "fire_station", "place_of_worship", "kindergarten"}
SHOP_LANDMARK = {"department_store", "mall"}
def keep_name(t):
    nm = t.get("name")
    if not nm: return False
    if nm in PUBLIC_NAMES: return True
    if t.get("highway") in ("primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "pedestrian", "service", "footway", "path", "trunk"):
        return True                                   # public street / shopping street name
    if t.get("railway") in ("rail", "station"): return True
    if t.get("amenity") in LANDMARK_AMENITY or t.get("leisure") == "park" or t.get("shop") in SHOP_LANDMARK:
        return True
    return False

def sanitize_tags(tags):
    out = {k: v for k, v in tags.items() if k in KEEP_KEYS}
    if "ref" in out and not (tags.get("railway") in ("station", "stop")): del out["ref"]
    if keep_name(tags):
        out["name"] = tags["name"]
        # a place name that is clearly a person's / a business: drop anything with a digit-address look
        if any(ch.isdigit() for ch in out["name"]) and tags.get("highway") is None and tags.get("railway") is None:
            del out["name"]
    return out

def merge_xml(blobs):
    nodes, ways, rels = {}, {}, {}
    def tags(e): return {t.get("k"): t.get("v") for t in e.findall("tag")}
    for raw in blobs:
        for e in ET.fromstring(raw):          # NOTE: user names / changesets in the XML are never read or stored
            if e.tag == "node": nodes[e.get("id")] = (float(e.get("lat")), float(e.get("lon")), tags(e))
            elif e.tag == "way": ways[e.get("id")] = ([n.get("ref") for n in e.findall("nd")], tags(e))
            elif e.tag == "relation": rels[e.get("id")] = ([(m.get("type"), m.get("ref"), m.get("role")) for m in e.findall("member")], tags(e))
    return nodes, ways, rels

def to_json(nodes, ways, rels):
    def geom(wid): return [{"lat": nodes[r][0], "lon": nodes[r][1]} for r in ways[wid][0] if r in nodes]
    out = []
    for wid, (refs, t) in ways.items():
        if not t or not way_wanted(t): continue
        g = geom(wid)
        if len(g) < 2: continue
        out.append({"type": "way", "id": int(wid), "tags": sanitize_tags(t), "geometry": g})
    for nid, (la, lo, t) in nodes.items():
        if t and node_wanted(t):
            out.append({"type": "node", "id": int(nid), "lat": la, "lon": lo, "tags": sanitize_tags(t)})
    for rid, (mem, t) in rels.items():
        if t.get("type") not in ("multipolygon",): continue
        if not (any(k in t for k in ("building", "landuse", "leisure", "natural", "water")) or t.get("amenity") == "parking"): continue
        members = []
        for ty, ref, role in mem:
            if ty == "way" and ref in ways and role in ("outer", "inner"):
                g = geom(ref)
                if len(g) >= 4: members.append({"type": "way", "ref": int(ref), "role": role, "geometry": g})
        if members: out.append({"type": "relation", "id": int(rid), "members": members, "tags": sanitize_tags(t)})
    return out

def main():
    cache = Path("/tmp/soka_osm_xml")
    if "--cache" in sys.argv: cache = Path(sys.argv[sys.argv.index("--cache") + 1])
    blobs = fetch_tiles(cache)
    nodes, ways, rels = merge_xml(blobs)
    print("merged: %d nodes, %d ways, %d relations (after de-duplication by id)" % (len(nodes), len(ways), len(rels)))
    els = to_json(nodes, ways, rels)
    st = None
    for e in els:
        if e["type"] == "node" and e["id"] == STATION_NODE_ID: st = e
    if st is None:
        for e in els:
            if e["type"] == "node" and e["tags"].get("railway") == "station": st = e
    print("station node:", st and (st["lat"], st["lon"], st["tags"].get("name")))
    lat0 = STATION_FALLBACK[0]
    s, w = m2deg(lat0, FETCH_X[0], FETCH_Y[1]); n, e_ = m2deg(lat0, FETCH_X[1], FETCH_Y[0])
    j = {"version": 0.6, "generator": "merged OSM API 0.6 /map tiles", "elements": els,
         "_source": "api.openstreetmap.org /api/0.6/map (tiled)",
         "_meta": {"center": [st["lat"], st["lon"]] if st else list(STATION_FALLBACK),
                   "bbox_s_w_n_e": [s, w, n, e_], "fetch_extent_m_x": FETCH_X, "fetch_extent_m_y": FETCH_Y,
                   "fetched": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "license": "ODbL 1.0, (c) OpenStreetMap contributors"}}
    out = DATA / "osm_raw.json"
    out.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")))
    print("wrote", out, len(els), "elements,", out.stat().st_size // 1024, "KB")

if __name__ == "__main__":
    main()
