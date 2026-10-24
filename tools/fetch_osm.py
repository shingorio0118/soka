#!/usr/bin/env python3
"""Fetch OpenStreetMap data around Soka Station and save it under data/.

Order of attempts:
  1. Overpass API (several public mirrors, short timeouts)
  2. fallback: OSM API 0.6 /map (same OSM data, XML) converted to the same
     Overpass-like "out geom" JSON shape, so tools/build_map.py reads either.

Station centre: node railway=station (the OSM name is "草加", not "草加駅"),
found via Overpass/OSM API; falls back to the pinned coordinates below
(which were taken from that node).

Usage: python3 tools/fetch_osm.py [--source auto|api]   (api = skip Overpass attempts)
Tags are sanitised before saving (whitelist of keys; names only for the station/public landmarks).
"""
import json, sys, time, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from pathlib import Path

UA = "soka-map-mvp/0.1 (local build; contact shingo.tanaka@mail.grokbot.com)"
DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)

STATION_NODE_ID = 7481965874          # railway=station, name=草加, ref TS16
STATION_FALLBACK = (35.8282516, 139.8033621)
HALF_M = 165                          # fetch radius (map itself uses ~125 m)
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

def http(url, data=None, timeout=45):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def bbox_for(lat, lon, half_m):
    import math
    dlat = half_m / 111320.0
    dlon = half_m / (111320.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)

def overpass_query(b):
    bb = "%.7f,%.7f,%.7f,%.7f" % b
    return f"""[out:json][timeout:60];
(
  way["highway"]({bb});
  way["area:highway"]({bb});
  way["railway"]({bb});
  way["building"]({bb});
  way["landuse"]({bb});
  way["leisure"]({bb});
  way["natural"]({bb});
  way["waterway"]({bb});
  way["amenity"="parking"]({bb});
  way["barrier"]({bb});
  way["public_transport"="platform"]({bb});
  rel["building"]({bb});
  rel["landuse"]({bb});
  rel["leisure"]({bb});
  rel["natural"]({bb});
  node["natural"="tree"]({bb});
  node["highway"~"street_lamp|bus_stop|traffic_signals"]({bb});
  node["amenity"~"bench|bicycle_parking"]({bb});
  node["railway"~"station|level_crossing"]({bb});
  node["tourism"="information"]({bb});
);
out geom;"""

def try_overpass(b):
    q = urllib.parse.urlencode({"data": overpass_query(b)}).encode()
    for url in OVERPASS:
        try:
            print("Overpass:", url, flush=True)
            raw = http(url, q, timeout=70)
            j = json.loads(raw)
            if j.get("elements"):
                j["_source"] = url
                return j
        except Exception as e:
            print("  failed:", repr(e)[:120], flush=True)
    return None

def osm_xml_to_json(xml_bytes, b):
    root = ET.fromstring(xml_bytes)
    nodes, ways, rels = {}, {}, []
    def tags(e): return {t.get("k"): t.get("v") for t in e.findall("tag")}
    for e in root:
        if e.tag == "node":
            nodes[e.get("id")] = (float(e.get("lat")), float(e.get("lon")), tags(e))
        elif e.tag == "way":
            ways[e.get("id")] = ([n.get("ref") for n in e.findall("nd")], tags(e))
        elif e.tag == "relation":
            rels.append((e.get("id"), [(m.get("type"), m.get("ref"), m.get("role")) for m in e.findall("member")], tags(e)))
    def geom(wid):
        refs, _ = ways[wid]
        return [{"lat": nodes[r][0], "lon": nodes[r][1]} for r in refs if r in nodes]
    out = []
    for wid, (refs, t) in ways.items():
        if not t: continue
        g = geom(wid)
        out.append({"type": "way", "id": int(wid), "nodes": [int(r) for r in refs], "tags": t, "geometry": g})
    for nid, (la, lo, t) in nodes.items():
        if t:
            out.append({"type": "node", "id": int(nid), "lat": la, "lon": lo, "tags": t})
    for rid, mem, t in rels:
        if t.get("type") not in ("multipolygon", "boundary") : continue
        members = []
        for ty, ref, role in mem:
            if ty == "way" and ref in ways:
                members.append({"type": "way", "ref": int(ref), "role": role, "geometry": geom(ref)})
        if members:
            out.append({"type": "relation", "id": int(rid), "members": members, "tags": t})
    return {"version": 0.6, "generator": "converted from OSM API 0.6 /map", "elements": out}

def try_osm_api(b):
    # bbox order: left,bottom,right,top
    url = "https://api.openstreetmap.org/api/0.6/map?bbox=%.7f,%.7f,%.7f,%.7f" % (b[1], b[0], b[3], b[2])
    print("OSM API:", url, flush=True)
    raw = http(url, timeout=90)
    # NOTE: the raw XML contains OSM user names / changesets, so it is converted in memory and never stored.
    j = osm_xml_to_json(raw, b)
    j["_source"] = "api.openstreetmap.org /api/0.6/map"
    return j

KEEP_KEYS = {
    "highway", "railway", "bridge", "layer", "tunnel", "indoor", "level", "building", "building:levels",
    "landuse", "leisure", "natural", "water", "waterway", "amenity", "barrier", "service", "width", "lanes",
    "oneway", "covered", "area", "public_transport", "footway", "crossing", "shop", "tourism", "entrance",
    "surface", "electrified", "ref", "bus", "type", "bicycle_parking", "capacity",
}
# names are kept ONLY for the station itself and clearly public landmarks / public transport
PUBLIC_NAMES = {"草加", "草加駅", "東武伊勢崎線", "草加停車場線", "マルイ", "ヨーカドー", "ダイエー", "VARIE",
                "草加駅西口", "草加駅東口", "草加駅南"}

def sanitize_tags(tags):
    out = {k: v for k, v in tags.items() if k in KEEP_KEYS}
    # 'ref' only for the station code (TS16); never keep house numbers etc.
    if "ref" in out and not (tags.get("railway") == "station" or tags.get("railway") == "stop"):
        del out["ref"]
    if tags.get("name") in PUBLIC_NAMES:
        out["name"] = tags["name"]
    # drop anything that looks like an address / occupant / contact detail (already excluded by the whitelist)
    return out

def sanitize(j):
    for e in j["elements"]:
        e["tags"] = sanitize_tags(e.get("tags", {}))
    j["elements"] = [e for e in j["elements"] if e["tags"] or e["type"] == "relation"]
    return j

def main():
    src = "auto"
    if "--source" in sys.argv: src = sys.argv[sys.argv.index("--source") + 1]
    lat, lon = STATION_FALLBACK
    b = bbox_for(lat, lon, HALF_M)
    j = None if src == "api" else try_overpass(b)
    if j is None:
        print("Overpass unavailable -> OSM API fallback", flush=True)
        j = try_osm_api(b)
    # sanity: station node
    st = None
    for e in j["elements"]:
        if e["type"] == "node" and e.get("id") == STATION_NODE_ID: st = e
    if st is None:
        for e in j["elements"]:
            if e["type"] == "node" and e.get("tags", {}).get("railway") == "station": st = e
    print("station node:", st and (st["lat"], st["lon"], st["tags"].get("name")))
    j["_meta"] = {
        "center": [st["lat"], st["lon"]] if st else [lat, lon],
        "bbox_s_w_n_e": b,
        "fetched": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "license": "ODbL 1.0, (c) OpenStreetMap contributors",
    }
    j = sanitize(j)
    out = DATA / "osm_raw.json"
    out.write_text(json.dumps(j, ensure_ascii=False))
    print("wrote", out, len(j["elements"]), "elements, source:", j["_source"])

if __name__ == "__main__":
    main()
