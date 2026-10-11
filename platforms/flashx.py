# platforms/flashx.py
import json
import urllib.parse, urllib.request

def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def _dur(st, et):
    try:
        (h1, m1), (h2, m2) = (x.split(":") for x in (st, et))
        d = (int(h2)*60+int(m2)) - (int(h1)*60+int(m1))
        # 跨午夜或负时长与中羽联侧对齐，置 None
        return d if d >= 0 else None
    except (ValueError, AttributeError):
        return None

def parse_games(resp_text):
    try:
        data = json.loads(resp_text)
    except (ValueError, TypeError):
        return []
    # 非 dict（如 "[]"、"null"）直接返回空，与 Task 2 同类防护对齐
    if not isinstance(data, dict) or not data.get("success"):
        return []
    out = []
    for r in (data.get("result") or {}).get("records") or []:
        d = r.get("data", {})
        st, et = d.get("startTime"), d.get("endTime")
        out.append({
            "source": "flashx", "source_id": str(d.get("id")),
            "title": d.get("activityTitle"), "date": d.get("activityDate"),
            "start_time": st, "end_time": et, "duration_min": _dur(st, et),
            "venue_name": d.get("activityAddress"),
            "venue_key": f"flashx:{d.get('placeId')}",
            "price": _to_float(d.get("showCost")),
            "applied_num": d.get("applyMemberNum"), "total_num": d.get("needMemberNum"),
            "lat": d.get("latitude"), "lng": d.get("longitude"),
            "raw_text": d.get("activityTitle") or "",
            "tags": d.get("displayTags") or "[]",
            "organizer": d.get("organizerName"), "club_name": d.get("organizerName"),
            "detail_ref": str(d.get("id")),
        })
    return out


# ---- 网络抓取（手测）----

_URL = "https://flash.flashxsport.com/flash/activity/list"

def fetch(creds, date_str, page):
    """取某天第 page 页（pageNo 从 1 起）；返回响应文本。HTTP 错误（如 401）向上抛 HTTPError。"""
    q = urllib.parse.urlencode({
        "pageNo": page, "pageSize": 10, "sortRule": 0,
        "latitude": "31.2304", "longitude": "121.4737", "city": "上海市",
        "area": "", "nearby": "", "date": date_str,
        "startTimeHour": "", "endTimeHour": "",
        "levelLower": 1, "levelUpper": 9, "sportId": 1, "isQueryFreePlayAct": 0})
    headers = {**(creds.get("headers") or {}), "x-access-token": creds["token"]}
    with urllib.request.urlopen(urllib.request.Request(f"{_URL}?{q}", headers=headers),
                                timeout=20) as r:
        return r.read().decode()
