# platforms/flashx.py
import json

def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def _dur(st, et):
    try:
        (h1, m1), (h2, m2) = (x.split(":") for x in (st, et))
        return (int(h2)*60+int(m2)) - (int(h1)*60+int(m1))
    except (ValueError, AttributeError):
        return None

def parse_games(resp_text):
    try:
        data = json.loads(resp_text)
    except (ValueError, TypeError):
        return []
    if not data.get("success"):
        return []
    out = []
    for r in data.get("result", {}).get("records", []):
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
