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
Named POIs (shops, eateries, clinics, public facilities ...) are saved separately as "pois" (name, category, position only) after a
privacy filter (see extract_pois); dropped names are never saved, only counts.
"""
import json, sys, time, math, re, collections, urllib.request, xml.etree.ElementTree as ET
from pathlib import Path

UA = "soka-map-mvp/0.2"
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


# ---- named POIs (shops, restaurants, public facilities ...) for the on-map name tags -----------------------------
# PRIVACY: only the display name, a coarse category and the position of a PUBLIC place (shop / eatery / clinic / school /
# shrine ...) are kept.  Anything that could be a private person's name or home is dropped here, before saving; the
# dropped names are never written anywhere (only counts, see _meta.poi_filter).
# category id -> (display name, tag colour, default priority)
POI_CATS = [
    ("convenience", "コンビニ", "#2f9e44"), ("shop", "買い物", "#e8590c"), ("food", "飲食", "#d6336c"),
    ("cafe", "カフェ", "#8d5524"), ("finance", "金融・郵便", "#1c7ed6"), ("health", "医療・薬局", "#0ca678"),
    ("public", "公共・学校", "#5f3dc4"), ("sacred", "寺社・公園・史跡", "#3b8f3b"), ("service", "サービス・娯楽", "#7048e8"),
]
SHOP_CAT = {"convenience": "convenience", "supermarket": "shop", "department_store": "shop", "mall": "shop", "variety_store": "shop",
            "coffee": "cafe", "tea": "cafe", "pastry": "cafe", "confectionery": "cafe", "bakery": "cafe", "ice_cream": "cafe",
            "hairdresser": "service", "beauty": "service", "massage": "service", "dry_cleaning": "service", "laundry": "service",
            "mobile_phone": "shop", "optician": "shop", "chemist": "health", "pharmacy": "health"}
AMENITY_CAT = {"restaurant": "food", "fast_food": "food", "pub": "food", "bar": "food", "food_court": "food", "izakaya": "food",
               "cafe": "cafe", "ice_cream": "cafe",
               "bank": "finance", "post_office": "finance", "doctors": "health", "clinic": "health", "dentist": "health", "pharmacy": "health",
               "hospital": "health", "veterinary": "health", "nursing_home": "public", "social_facility": "public",
               "school": "public", "kindergarten": "public", "college": "public", "university": "public", "townhall": "public",
               "library": "public", "police": "public", "fire_station": "public", "community_centre": "public", "childcare": "public",
               "place_of_worship": "sacred", "karaoke_box": "service", "nightclub": "service", "cinema": "service", "theatre": "service",
               "public_bath": "service", "arts_centre": "service"}
HIGH_PRI = {"department_store", "mall", "townhall", "school", "library", "police", "post_office", "hospital", "place_of_worship", "museum", "park"}
RESIDENCE_BUILDING = {"house", "residential", "detached", "semidetached_house", "terrace", "apartments", "dormitory", "bungalow", "farm"}
# bare family names / given-name endings: a shop or a pub that is just "○○" cannot be told apart from a private person
SURNAMES = set("佐藤 鈴木 高橋 伊藤 渡辺 山本 中村 小林 加藤 吉田 山田 佐々木 山口 松本 井上 木村 林 斎藤 清水 山崎 森 池田 橋本 阿部 石川 山下 中島 石井 小川 前田 岡田 長谷川 藤田 後藤 近藤 村上 遠藤 青木 坂本 斉藤 福田 太田 西村 藤井 金子 岡本 藤原 三浦 中野 原田 松田 竹内 小野 田村 中山 和田 石田 森田 上田 原 柴田 酒井 工藤 横山 宮崎 宮本 内田 高木 安藤 谷口 大野 丸山 今井 河野 藤本 村田 武田 上野 杉山 増田 平野 大塚 千葉 久保 松井 小島 岩崎 桜井 木下 野口 松尾 菊地 野村 新井 浅井 浅古 藤城 小沢 会田 田口 寺田 川端 萩原 青山 関 星野 荒井".split())
GIVEN_END = re.compile(r"^[\u4e00-\u9fff]{2,3}$")
GIVEN_SUFFIX = tuple("郎 男 吉 助 夫 彦 雄 蔵 太".split())
BIZ_WORDS = ("店", "屋", "軒", "亭", "堂", "館", "庵", "苑", "園", "院", "科", "薬局", "銀行", "金庫", "郵便", "書店", "食堂", "酒場", "酒家", "寿司", "鮨", "整骨", "接骨", "神社", "寺", "公園", "学校", "幼稚園")
ADDRESS_RE = re.compile(r"(\d+\s*[-\u2010-\u2015ー]\s*\d+)|([町丁村字区]\s*[0-9０-９]+$)|^[0-9０-９\s-]+$")

def poi_pri(sub, cat):
    return 4 if sub in HIGH_PRI else (2 if cat == "convenience" else 1)

def poi_category(t):
    """-> (cat, sub) or a drop reason string"""
    if t.get("access") in ("private", "no") or t.get("disused") or t.get("abandoned") or t.get("demolished"): return "private_or_closed"
    b = t.get("building")
    if b in RESIDENCE_BUILDING or t.get("landuse") == "residential": return "residence"
    sh, am, le, to, hi, mm = t.get("shop"), t.get("amenity"), t.get("leisure"), t.get("tourism"), t.get("historic"), t.get("man_made")
    if sh in ("funeral_directors", "security") or t.get("craft") or t.get("office") == "lawyer": return "sensitive_or_craft"
    if am in ("parking", "bicycle_parking", "bicycle_rental", "vending_machine", "atm", "post_box", "bench", "toilets", "waste_basket", "parking_space") or hi == "locomotive":
        return "low_value_utility"      # parking lots, bike racks, vending machines, ATMs: not worth a name tag
    if sh == "department_store" or sh == "mall": return ("shop", sh)
    if sh and sh in SHOP_CAT: return (SHOP_CAT[sh], sh)
    if am in AMENITY_CAT: return (AMENITY_CAT[am], am)
    if sh and sh != "vacant": return ("shop", sh)
    if t.get("office") in ("estate_agent", "government", "telecommunication", "insurance"): return ("service" if t.get("office") == "estate_agent" else "public", t["office"])
    if le == "park" or le == "garden": return ("sacred", "park")
    if le == "playground" and any(k in t.get("name", "") for k in ("公園", "遊園", "広場")): return ("sacred", "park")
    if le in ("amusement_arcade", "adult_gaming_centre", "fitness_centre", "sports_centre", "pitch", "swimming_pool"): return ("service", le)
    if to in ("museum", "hotel", "hostel", "attraction", "gallery"): return ("public" if to in ("museum", "attraction", "gallery") else "service", to)
    if hi in ("wayside_shrine", "memorial", "monument", "ruins", "shrine") or mm == "ceremonial_gate": return ("sacred", "historic")
    return "not_a_public_poi_tag"

def person_like(name):
    n = name.strip()
    if re.search(r"(家|邸|宅)(の.*)?$|家住宅|様", n): return True      # "〇〇家の地蔵堂", "〇〇家住宅", "〇〇邸"
    if n in SURNAMES: return True
    if GIVEN_END.match(n) and n.endswith(GIVEN_SUFFIX) and not any(w in n for w in BIZ_WORDS): return True
    if re.match(r"^[\u4e00-\u9fff]{2,3}(ビル|荘|ハイツ|マンション)$", n): return True     # building named after its owner
    return False

def way_centroid(coords):
    n = len(coords); return sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n

def extract_pois(nodes, ways):
    pois, drop = [], collections.Counter()
    def consider(t, lat, lon, bbox):
        nm = (t.get("name:ja") or t.get("name") or "").strip()
        if not nm and not t.get("name"): return
        if t.get("name:ja") and not re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", nm): nm = t.get("name", nm).strip()
        r = poi_category(t)
        if isinstance(r, str): drop[r] += 1; return
        if len(nm) < 2 or len(nm) > 30: drop["bad_length"] += 1; return
        if ADDRESS_RE.search(nm): drop["address_like"] += 1; return
        if person_like(nm): drop["person_like"] += 1; return
        cat, sub = r
        pois.append({"name": nm, "cat": cat, "sub": sub, "pri": poi_pri(sub, cat), "lat": round(lat, 6), "lon": round(lon, 6), "bbox": bbox})
    for nid, (la, lo, t) in nodes.items():
        if t.get("name") or t.get("name:ja"): consider(t, la, lo, None)
    for wid, (refs, t) in ways.items():
        if not (t.get("name") or t.get("name:ja")): continue
        pts = [(nodes[r][0], nodes[r][1]) for r in refs if r in nodes]
        if len(pts) < 3: drop["no_geometry"] += 1; continue
        la, lo = way_centroid(pts)
        consider(t, la, lo, [round(min(p[0] for p in pts), 6), round(min(p[1] for p in pts), 6), round(max(p[0] for p in pts), 6), round(max(p[1] for p in pts), 6)])
    # the same shop is often mapped twice (a node and its building): keep one per (name, 30 m)
    pois.sort(key=lambda p: (p["bbox"] is None, p["name"], p["lat"], p["lon"]))
    keep = []
    for p in pois:
        dup = [q for q in keep if q["name"] == p["name"] and abs(q["lat"] - p["lat"]) < 0.00027 and abs(q["lon"] - p["lon"]) < 0.00033]
        if dup:
            drop["duplicate"] += 1
            if dup[0]["bbox"] is None and p["bbox"] is not None: dup[0]["bbox"] = p["bbox"]
            continue
        keep.append(p)
    return keep, drop

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
    pois, drop = extract_pois(nodes, ways)
    print("pois kept: %d, dropped: %s" % (len(pois), dict(drop)))
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
                   "fetched": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "license": "ODbL 1.0, (c) OpenStreetMap contributors",
                   "poi_filter": {"kept": len(pois), "dropped_by_reason": dict(drop)}},
         "poi_categories": [{"id": c, "name": n, "color": col} for c, n, col in POI_CATS], "pois": pois}
    out = DATA / "osm_raw.json"
    out.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")))
    print("wrote", out, len(els), "elements,", out.stat().st_size // 1024, "KB")

if __name__ == "__main__":
    main()
