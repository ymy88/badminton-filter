# sync.py —— 刷新编排：两平台抓取 → 归一化 → 入库 → 场馆归类 → 补坐标/通勤
import json, urllib.error
from datetime import date, timedelta
import db, creds as creds_mod, venues, commute
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
    _ensure_commute(conn, pk, commute.amap_coord(game["lat"], game["lng"]), origins_ll)

def _ensure_commute(conn, pk, dest, origins_ll):
    # 给场馆 pk 补各出发地通勤(驾车/公交/步行)：三项齐全才跳过；
    # 部分缺失(某路线上次失败)会重算，已成功的字段保留，逐次补齐；全失败不写，留待重试
    for origin, oll in origins_ll.items():
        row = conn.execute("SELECT drive_min,transit_min,walk_m FROM commute WHERE origin=? AND place_key=?",
                           (origin, pk)).fetchone()
        old = (row["drive_min"], row["transit_min"], row["walk_m"]) if row else (None, None, None)
        if all(v is not None for v in old):
            continue  # 三项齐全
        m = commute.route_minutes(oll, dest)
        # 新值优先，失败的字段保留旧值
        drive = m.get("drive_min") if m.get("drive_min") is not None else old[0]
        transit = m.get("transit_min") if m.get("transit_min") is not None else old[1]
        walk = m.get("walk_m") if m.get("walk_m") is not None else old[2]
        if drive is None and transit is None and walk is None:
            continue
        conn.execute("INSERT OR REPLACE INTO commute(origin,place_key,drive_min,transit_min,walk_m,computed_at)"
                     " VALUES(?,?,?,?,?,datetime('now'))",
                     (origin, pk, drive, transit, walk))
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
    # 只做「抓取 + 入库」，秒级返回；通勤/POI 归类交给 compute_pending 后台跑
    today = date.today()
    seed_canonical_venues(conn, config)
    games, auth_failed, errors = [], [], []
    for source, run in (("zhongyulian", lambda c: _fetch_zhongyulian(c, today)),
                        ("flashx", lambda c: _fetch_flashx(c, today))):
        if not creds.get(source):
            auth_failed.append(source)  # 没凭证 → 当作凭证失效，界面提示去「抓取凭证」
            continue
        try:
            got, bad = run(creds[source])
        except Exception:  # 单平台网络异常不拖垮另一平台，记入 errors
            errors.append(source)
            continue
        games += got
        if bad:
            auth_failed.append(source)
    upsert_games(conn, games)
    return {"ok": not auth_failed and not errors, "count": len(games),
            "auth_failed": auth_failed, "errors": errors}

def _origins_ll(conn):
    return {r["key"]: f"{r['lng']},{r['lat']}"
            for r in conn.execute("SELECT key,lat,lng FROM origins")
            if r["lat"] is not None and r["lng"] is not None}

def compute_pending(db_path, config):
    # 后台线程用：自开连接。1) 给未归类/无坐标的球局补 规范馆+坐标+通勤；
    # 2) 给已归类但缺通勤的场馆补算（如刚换了出发地）。按场馆缓存，可重复调。
    conn = db.connect(db_path)
    try:
        canonical = load_canonical_list(conn)
        origins_ll = _origins_ll(conn)
        pending = [dict(r) for r in conn.execute(
            "SELECT * FROM games WHERE canonical_id IS NULL OR lat IS NULL")]
        done = []
        for g in pending:
            try:
                enrich_venue(conn, g, canonical, origins_ll)
                done.append(g)
            except Exception:  # 单个场馆的高德/编码异常只跳过该场馆
                pass
        if done:
            upsert_games(conn, done)  # 写回 enrich 补上的 canonical_id/坐标
        _fill_missing_commute(conn, origins_ll)
    finally:
        conn.close()

def _fill_missing_commute(conn, origins_ll):
    # 按场馆(place_key)去重，给所有有坐标的场馆补缺失的通勤（换出发地后缓存被清会重算）
    places = {}
    for r in conn.execute("SELECT canonical_id, venue_key, lat, lng FROM games WHERE lat IS NOT NULL"):
        pk = commute.place_key(r["canonical_id"], r["venue_key"])
        places.setdefault(pk, commute.amap_coord(r["lat"], r["lng"]))
    for pk, dest in places.items():
        try:
            _ensure_commute(conn, pk, dest, origins_ll)
        except Exception:
            pass

def pending_count(conn):
    # 还需补数据的场馆数：无坐标(POI/geocode 失败) + 有坐标但缺某出发地通勤
    # 高德失败不缓存，故这些会在下次刷新/改地址时自动重试；此数用于提示用户重试
    origins = [r["key"] for r in conn.execute("SELECT key FROM origins WHERE lat IS NOT NULL")]
    if not origins:
        return 0
    pending, places = set(), set()  # 先按场馆去重，避免对每条球局都查一次
    for r in conn.execute("SELECT DISTINCT canonical_id, venue_key, lat FROM games"):
        if r["lat"] is None:
            pending.add(("v", r["venue_key"]))
        else:
            places.add(commute.place_key(r["canonical_id"], r["venue_key"]))
    for pk in places:
        # 三项齐全的出发地才算"完成"（部分缺失会重算）
        complete = conn.execute(
            "SELECT COUNT(*) c FROM commute WHERE place_key=? AND drive_min IS NOT NULL"
            " AND transit_min IS NOT NULL AND walk_m IS NOT NULL", (pk,)).fetchone()["c"]
        if complete < len(origins):
            pending.add(("pk", pk))
    return len(pending)
