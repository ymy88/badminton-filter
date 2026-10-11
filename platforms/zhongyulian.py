# platforms/zhongyulian.py
import json
from datetime import timedelta

def _split_time(t):
    if not t or "~" not in t:
        return None, None, None
    a, b = (x.strip() for x in t.split("~", 1))
    try:
        dur = (_mins(b) - _mins(a))
        return a, b, dur if dur >= 0 else None
    except ValueError:
        return a, b, None

def _mins(hhmm):
    h, m = hhmm.split(":"); return int(h) * 60 + int(m)

def _split_join(s):
    try:
        a, b = s.split("/"); return int(a), int(b)
    except (ValueError, AttributeError):
        return None, None

def parse_games(resp_text, day, today):
    try:
        data = json.loads(resp_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict) or data.get("ok") != 1:
        return []
    lst = data.get("list") or {}
    items = (lst.get("my_club_movement_list") or []) + \
            (lst.get("other_club_movement_list") or [])
    d = (today + timedelta(days=day)).isoformat()
    out, seen = [], set()
    for it in items:
        sid = it.get("activity_id")
        if sid in seen:
            continue
        seen.add(sid)
        st, et, dur = _split_time(it.get("time"))
        ap, tot = _split_join(it.get("joinstate"))
        out.append({
            "source": "zhongyulian", "source_id": sid,
            "title": it.get("name"), "date": d,
            "start_time": st, "end_time": et, "duration_min": dur,
            "venue_name": it.get("address"),
            "venue_key": f"zhongyulian:{it.get('address_id')}",
            "price": it.get("price"), "applied_num": ap, "total_num": tot,
            "lat": None, "lng": None, "raw_text": it.get("name") or "",
            "tags": "[]", "organizer": it.get("organizer"),
            "club_name": it.get("club_name"), "detail_ref": sid,
        })
    return out
