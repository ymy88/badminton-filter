# commute.py
import os, time, json, urllib.parse, urllib.request

BASE = "https://restapi.amap.com"
CITY = "上海"

def _key():
    return os.environ["GAO_DE_MAP_API_KEY"]

def amap_coord(lat, lng):
    return f"{lng},{lat}"

def place_key(canonical_id, venue_key):
    return f"c:{canonical_id}" if canonical_id else f"v:{venue_key}"

def sec_to_min(sec):
    try:
        return round(int(sec) / 60)
    except (ValueError, TypeError):
        return None

def _amap_get(url, params):
    params = {**params, "key": _key()}
    q = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=10) as r:
            data = json.loads(r.read().decode())
        return data if data.get("status") == "1" else None
    except Exception:
        return None

def geocode(address):
    d = _amap_get(f"{BASE}/v3/geocode/geo", {"address": address, "city": CITY})
    if not d or not d.get("geocodes"):
        return None
    try:
        lng, lat = d["geocodes"][0]["location"].split(",")
        return float(lat), float(lng)
    except (ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None

def poi_search(name):
    # 按名字搜高德 POI，取第一条：统一球场名 + 坐标 + POI id（跨平台去重键）
    d = _amap_get(f"{BASE}/v3/place/text",
                  {"keywords": name, "city": CITY, "citylimit": "true", "offset": 1})
    if not d or not d.get("pois"):
        return None
    try:
        p = d["pois"][0]
        lng, lat = p["location"].split(",")
        return {"amap_id": p["id"], "name": p["name"], "lat": float(lat), "lng": float(lng)}
    except (ValueError, KeyError, IndexError, AttributeError, TypeError):
        return None

def _duration_min(resp, key):
    # 防御式取值：结构缺失或异常一律返回 None，不抛
    try:
        items = ((resp or {}).get("route") or {}).get(key) or []
        return sec_to_min(items[0].get("duration")) if items else None
    except (AttributeError, IndexError, KeyError, TypeError):
        return None

def route_minutes(origin_ll, dest_ll):
    drive = _amap_get(f"{BASE}/v3/direction/driving",
                      {"origin": origin_ll, "destination": dest_ll})
    transit = _amap_get(f"{BASE}/v3/direction/transit/integrated",
                        {"origin": origin_ll, "destination": dest_ll,
                         "city": CITY, "cityd": CITY})
    dm = _duration_min(drive, "paths")
    tm = _duration_min(transit, "transits")
    time.sleep(0.3)  # 节流，避开 QPS
    return {"drive_min": dm, "transit_min": tm}
