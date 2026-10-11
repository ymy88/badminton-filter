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

def auto_canonical(conn, game, canonical_list):
    # 未匹配任何已有规范馆 → 高德 POI 搜索，用搜出的名字自动建/复用规范馆
    # 跨平台去重：两平台不同叫法若搜到同一 POI(amap_id) 则归并到同一规范馆
    poi = commute.poi_search(game["venue_name"] or "")
    if not poi:
        return None
    name = game["venue_name"] or ""
    row = conn.execute("SELECT * FROM canonical_venues WHERE amap_id=?", (poi["amap_id"],)).fetchone()
    if row:  # 同一 POI 的另一种叫法 → 复用，并记进 name_patterns 省得下次再搜
        pats = json.loads(row["name_patterns"] or "[]")
        if name and name not in pats:
            pats.append(name)
            conn.execute("UPDATE canonical_venues SET name_patterns=? WHERE id=?",
                         (json.dumps(pats, ensure_ascii=False), row["id"]))
        c = {"id": row["id"], "canonical_name": row["canonical_name"], "lat": row["lat"],
             "lng": row["lng"], "name_patterns": pats,
             "platform_ids": json.loads(row["platform_ids"] or "[]"), "amap_id": row["amap_id"]}
    else:  # 新馆：高德结果名即统一球场名
        cur = conn.execute(
            "INSERT INTO canonical_venues(canonical_name,lat,lng,name_patterns,platform_ids,amap_id)"
            " VALUES(?,?,?,?,?,?)",
            (poi["name"], poi["lat"], poi["lng"], json.dumps([name], ensure_ascii=False), "[]", poi["amap_id"]))
        c = {"id": cur.lastrowid, "canonical_name": poi["name"], "lat": poi["lat"], "lng": poi["lng"],
             "name_patterns": [name], "platform_ids": [], "amap_id": poi["amap_id"]}
    conn.commit()
    canonical_list.append(c)  # 同批后续球局可经 classify 命中，无需再搜
    return c

def enrich_venue(conn, game, canonical_list, origins_ll):
    c = venues.classify(game["venue_name"], game["venue_key"], canonical_list)
    if c is None:  # 没匹配上 → 自动按高德 POI 建规范馆（去人工维护 config）
        c = auto_canonical(conn, game, canonical_list)
    if c:
        game["canonical_id"] = c["id"]
        if c["lat"] is not None:  # 规范馆有坐标才覆盖；否则保留球局自带坐标（闪动带经纬度）
            game["lat"], game["lng"] = c["lat"], c["lng"]
    if game["lat"] is None:  # 仍缺坐标（中羽联未归类，或归到无坐标规范馆且球局自带也无）→ 地理编码
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
        if m["drive_min"] is None and m["transit_min"] is None:
            continue  # 完全失败不缓存，下次刷新重算
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
    for day in range(3):  # 今天/明天/后天
        text, bad = _call("zhongyulian", zhongyulian.fetch, cr, day)
        if bad:
            return games, True
        games += zhongyulian.parse_games(text, day, today)
    return games, False

def _fetch_flashx(cr, today):
    games = []
    for i in range(3):  # 今天/明天/后天
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

def seed_canonical_venues(conn, config):
    # config → db：按 canonical_name 幂等写入，已存在则更新并保留原 id
    for c in config.get("canonical_venues") or []:
        vals = (c.get("lat"), c.get("lng"),
                json.dumps(c.get("name_patterns") or [], ensure_ascii=False),
                json.dumps(c.get("platform_ids") or [], ensure_ascii=False))
        row = conn.execute("SELECT id FROM canonical_venues WHERE canonical_name=?",
                           (c["canonical_name"],)).fetchone()
        if row:
            conn.execute("UPDATE canonical_venues SET lat=?,lng=?,name_patterns=?,platform_ids=?"
                         " WHERE id=?", vals + (row["id"],))
        else:
            conn.execute("INSERT INTO canonical_venues(lat,lng,name_patterns,platform_ids,"
                         "canonical_name) VALUES(?,?,?,?,?)", vals + (c["canonical_name"],))
    conn.commit()

def load_canonical_list(conn):
    return [{"id": r["id"], "canonical_name": r["canonical_name"],
             "lat": r["lat"], "lng": r["lng"],
             "name_patterns": json.loads(r["name_patterns"] or "[]"),
             "platform_ids": json.loads(r["platform_ids"] or "[]"), "amap_id": r["amap_id"]}
            for r in conn.execute("SELECT id, canonical_name, lat, lng, name_patterns,"
                                  " platform_ids, amap_id FROM canonical_venues")]

def refresh(conn, creds, config):
    today = date.today()
    seed_canonical_venues(conn, config)
    canonical = load_canonical_list(conn)
    origins_ll = {r["key"]: f"{r['lng']},{r['lat']}"
                  for r in conn.execute("SELECT key,lat,lng FROM origins")
                  if r["lat"] is not None and r["lng"] is not None}
    games, auth_failed, errors = [], [], []
    for source, run in (("zhongyulian", lambda c: _fetch_zhongyulian(c, today)),
                        ("flashx", lambda c: _fetch_flashx(c, today))):
        if source not in creds:
            continue  # 未抓到凭证的平台直接跳过
        try:
            got, bad = run(creds[source])
        except Exception:  # 单平台网络异常不拖垮另一平台，记入 errors
            errors.append(source)
            continue
        games += got
        if bad:
            auth_failed.append(source)
    upsert_games(conn, games)  # 先入库，保证 enrich 异常不丢球局
    for g in games:
        try:
            enrich_venue(conn, g, canonical, origins_ll)
        except Exception:  # 单个场馆的高德/编码异常只跳过该场馆
            pass
    upsert_games(conn, games)  # 写回 enrich 补上的 canonical_id/坐标
    return {"ok": not auth_failed and not errors, "count": len(games),
            "auth_failed": auth_failed, "errors": errors}
