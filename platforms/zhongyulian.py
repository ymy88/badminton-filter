# platforms/zhongyulian.py
import json
import urllib.error, urllib.request
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


# ---- 网络抓取（手测）----

_URL = "https://service2.zhongyulian.com/wxapi.php"
_PAGE = 10
_MAX_PAGES = 50  # 翻页上限，防服务端异常时死循环

def _post(creds, day, skip):
    body = {"day": day, "latitude": "31.2304", "longitude": "121.4737", "city": "上海",
            "keyword": "", "limit": _PAGE, "order": 1, "skip": skip, "opt": 100,
            "cmd": "indexList", "skey": creds["skey"],
            "timestamp": 0, "sign": "0" * 32}  # sign/timestamp 服务端不校验
    req = urllib.request.Request(_URL, json.dumps(body).encode(),
                                 headers=creds.get("headers") or {}, method="POST")
    for attempt in (1, 2):  # 超时/断网重试一次；HTTP 错误(鉴权等)不重试，交调用方判断
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode()
        except urllib.error.HTTPError:
            raise
        except OSError:
            if attempt == 2:
                raise

def fetch(creds, day, on_page=lambda n: None):
    """返回合并后的响应文本：my 列表取首页（一次全返回），other 列表按 skip 翻完。
    首页不是 ok==1（鉴权失败/Fatal error HTML）时原样返回，交调用方判断。
    on_page(已翻页数)：每请求完一页回调一次（没有总条数，只能报已翻几页）。"""
    first = _post(creds, day, 0)
    on_page(1)
    try:
        data = json.loads(first)
    except ValueError:
        return first
    if not isinstance(data, dict) or data.get("ok") != 1:
        return first
    lst = data.setdefault("list", {})
    other = list(lst.get("other_club_movement_list") or [])
    got = len(other)
    for i in range(1, _MAX_PAGES):
        if got < _PAGE * i:  # 上一页不满，已到末页
            break
        try:
            d = json.loads(_post(creds, day, _PAGE * i))
            on_page(i + 1)
            page = ((d.get("list") or {}).get("other_club_movement_list") or [])
        except (ValueError, AttributeError, OSError):  # 翻页中途网络失败：保留已拿到的页
            break
        if not page:
            break
        other += page
        got += len(page)
    lst["other_club_movement_list"] = other
    return json.dumps(data, ensure_ascii=False)
