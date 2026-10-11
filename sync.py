# sync.py —— 刷新编排：两平台抓取 → 归一化 → 入库 → 场馆归类 → 补坐标/通勤
import json, urllib.error
from datetime import date, timedelta
import creds as creds_mod, venues, commute
from platforms import zhongyulian, flashx

_COLS = ("source", "source_id", "title", "date", "start_time", "end_time", "duration_min",
         "venue_name", "venue_key", "canonical_id", "price", "applied_num", "total_num",
         "lat", "lng", "raw_text", "tags", "organizer", "club_name", "detail_ref")

def upsert_games(conn, games):
    q = (f"INSERT OR REPLACE INTO games({','.join(_COLS)}) "
         f"VALUES ({','.join('?' * len(_COLS))})")
    conn.executemany(q, [tuple(g.get(c) for c in _COLS) for g in games])
    conn.execute("DELETE FROM games WHERE date < ?", (date.today().isoformat(),))
    conn.commit()

def enrich_venue(conn, game, canonical_list, origins_ll):
    c = venues.classify(game["venue_name"], game["venue_key"], canonical_list)
    if c:
        game["canonical_id"] = c["id"]; game["lat"], game["lng"] = c["lat"], c["lng"]
    elif game["lat"] is None:  # 中羽联未归类，需地理编码
        row = conn.execute("SELECT lat,lng FROM venues WHERE venue_key=?",
                           (game["venue_key"],)).fetchone()
        if row and row["lat"] is not None:
            game["lat"], game["lng"] = row["lat"], row["lng"]
        else:
            ll = commute.geocode(game["venue_name"] or "")
            if ll:
                game["lat"], game["lng"] = ll
                conn.execute("INSERT OR REPLACE INTO venues(venue_key,name,lat,lng,geocoded_at)"
                             " VALUES(?,?,?,?,datetime('now'))",
                             (game["venue_key"], game["venue_name"], ll[0], ll[1]))
    if game["lat"] is None:
        return  # 坐标仍缺，通勤跳过
    pk = commute.place_key(game.get("canonical_id"), game["venue_key"])
    dest = commute.amap_coord(game["lat"], game["lng"])
    for origin, oll in origins_ll.items():
        if conn.execute("SELECT 1 FROM commute WHERE origin=? AND place_key=?",
                        (origin, pk)).fetchone():
            continue
        m = commute.route_minutes(oll, dest)
        conn.execute("INSERT OR REPLACE INTO commute(origin,place_key,drive_min,transit_min,computed_at)"
                     " VALUES(?,?,?,?,datetime('now'))",
                     (origin, pk, m["drive_min"], m["transit_min"]))
    conn.commit()

def _call(source, fn, *args):
    """执行 fetch；返回 (文本, 是否鉴权失败)。HTTP 错误取状态码+响应体判断。"""
    try:
        text = fn(*args)
        return text, creds_mod.is_auth_failure(source, text, 200)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        return body, creds_mod.is_auth_failure(source, body, e.code)

def _fetch_zhongyulian(cr, today):
    games = []
    for day in range(4):  # 0/1/2 + 3(「两天以后」大桶，日期按 today+3 近似)
        text, bad = _call("zhongyulian", zhongyulian.fetch, cr, day)
        if bad:
            return games, True
        games += zhongyulian.parse_games(text, day, today)
    return games, False

def _fetch_flashx(cr, today, days):
    games = []
    for i in range(days):
        d = (today + timedelta(days=i)).isoformat()
        page, pages = 1, 1
        while page <= pages:
            text, bad = _call("flashx", flashx.fetch, cr, d, page)
            if bad:
                return games, True
            games += flashx.parse_games(text)
            try:
                pages = int(json.loads(text)["result"]["pages"])
            except (ValueError, KeyError, TypeError):
                pages = 0
            page += 1
    return games, False

def refresh(conn, creds, config):
    today = date.today()
    canonical = config.get("canonical_venues") or []
    origins_ll = {r["key"]: f"{r['lng']},{r['lat']}"
                  for r in conn.execute("SELECT key,lat,lng FROM origins")
                  if r["lat"] is not None and r["lng"] is not None}
    games, auth_failed = [], []
    for source, run in (("zhongyulian", lambda c: _fetch_zhongyulian(c, today)),
                        ("flashx", lambda c: _fetch_flashx(c, today, config.get("flashx_days", 7)))):
        if source not in creds:
            continue  # 未抓到凭证的平台直接跳过
        try:
            got, bad = run(creds[source])
        except Exception:  # 网络异常不拖垮另一平台
            got, bad = [], False
        games += got
        if bad:
            auth_failed.append(source)
    for g in games:
        enrich_venue(conn, g, canonical, origins_ll)
    upsert_games(conn, games)
    return {"ok": not auth_failed, "count": len(games), "auth_failed": auth_failed}
