# sync.py —— 刷新编排：两平台抓取 → 归一化 → 入库；场馆归类（馆名 → 高德 POI）与通勤补算
import json, sqlite3, statistics, urllib.error
from collections import Counter
from datetime import date, timedelta
import db, creds as creds_mod, venues, commute
from platforms import zhongyulian, flashx

_COLS = ("source", "source_id", "title", "date", "start_time", "end_time", "duration_min",
         "venue_name", "platform_name_norm", "venue_key", "price", "applied_num", "total_num",
         "lat", "lng", "raw_text", "tags", "organizer", "club_name", "detail_ref")

def upsert_games(conn, games):
    # 按 (source, source_id) 整行写入（lat/lng 只存平台原始坐标，新值覆盖即正确）；
    # 新出现的馆名在 venue_aliases 记一行 pending，等「算通勤」归类
    rows = [tuple(venues.norm_name(g.get("venue_name")) if c == "platform_name_norm" else g.get(c)
                  for c in _COLS) for g in games]
    conn.executemany(f"INSERT OR REPLACE INTO games({','.join(_COLS)}) "
                     f"VALUES ({','.join('?' * len(_COLS))})", rows)
    norms = {(r[_COLS.index("platform_name_norm")],) for r in rows}
    conn.executemany("INSERT OR IGNORE INTO venue_aliases(platform_name_norm, origin) VALUES(?, 'pending')",
                     [n for n in norms if n[0]])
    conn.execute("DELETE FROM games WHERE date < ?", (date.today().isoformat(),))
    conn.commit()

def canonical_for_poi(conn, poi):
    # 高德 POI → 统一球场 id：amap_id 已有则复用，否则新建（手动坐标 amap_id=None，总是新建）
    # （amap_name 与已有统一球场重名时抛 sqlite3.IntegrityError，由调用方处理）
    row = conn.execute("SELECT id FROM canonical_venues WHERE amap_id=?", (poi["amap_id"],)).fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO canonical_venues(amap_name, amap_id, lat, lng) VALUES(?,?,?,?)",
                        (poi["name"], poi["amap_id"], poi["lat"], poi["lng"])).lastrowid

def delete_orphan_canonicals(conn):
    # 删掉没有任何馆名关联的统一球场（改归、撤销后留下的），连同它们的通勤缓存；返回删除个数。
    # 通勤缓存必须一起删：id 可能被新建的统一球场复用，不删会让新球场继承旧坐标的通勤
    n = conn.execute("DELETE FROM canonical_venues WHERE id NOT IN"
                     " (SELECT canonical_id FROM venue_aliases WHERE canonical_id IS NOT NULL)").rowcount
    conn.execute("DELETE FROM commute WHERE place_key LIKE 'c:%'"
                 " AND place_key NOT IN (SELECT 'c:' || id FROM canonical_venues)")
    conn.commit()
    return n

# 待自动搜索的馆名：pending 且未搜过或上次高德接口出错
_RETRYABLE = "a.origin = 'pending' AND (a.last_search IS NULL OR a.last_search = 'error')"

def classify_pending(conn, progress=lambda msg: None):
    # 当前球局涉及的 pending 馆名逐个搜高德 POI（按馆名，不按球局），取第一条。
    # 只搜未搜过的和上次接口出错的；结果记进 last_search：
    # error 接口出错（下次重试）/ empty 没搜到、dup 与已有统一球场重名（都不再自动搜，留给管理页手动处理）
    # UPDATE 都带 origin='pending'：搜索期间用户若在管理页改过，不覆盖
    names = conn.execute(
        "SELECT a.platform_name_norm n, MIN(g.venue_name) raw FROM venue_aliases a"
        " JOIN games g ON g.platform_name_norm = a.platform_name_norm"
        f" WHERE {_RETRYABLE} GROUP BY a.platform_name_norm").fetchall()
    for i, r in enumerate(names, 1):
        cid, result = None, "empty"
        try:
            pois = commute.poi_search(r["raw"])
        except commute.AmapError:
            pois, result = [], "error"
        if pois:
            try:
                cid = canonical_for_poi(conn, pois[0])
            except sqlite3.IntegrityError:  # 高德名与已有统一球场重名：留给用户手动处理
                result = "dup"
        if cid:
            conn.execute("UPDATE venue_aliases SET canonical_id=?, origin='auto', last_search=NULL,"
                         " updated_at=datetime('now') WHERE platform_name_norm=? AND origin='pending'", (cid, r["n"]))
        else:
            conn.execute("UPDATE venue_aliases SET last_search=?, updated_at=datetime('now')"
                         " WHERE platform_name_norm=? AND origin='pending'", (result, r["n"]))
        conn.commit()
        progress(f"归类场馆 {i}/{len(names)}")

def commute_places(conn):
    # 需要通勤的场馆 → 高德坐标串 "lng,lat"：
    # 已归类 → 统一球场的高德坐标（c:<id>）；未归类的闪动场馆 → 闪动坐标（v:<venue_key>）；
    # 中羽联未归类不算（中羽联坐标不可信，games.lat 里即使有遗留值也不用）
    places = {}
    for r in conn.execute(
            "SELECT DISTINCT c.id, c.lat, c.lng FROM games g"
            " JOIN venue_aliases a ON a.platform_name_norm = g.platform_name_norm"
            " JOIN canonical_venues c ON c.id = a.canonical_id"):
        places[commute.place_key(r["id"], None)] = commute.amap_coord(r["lat"], r["lng"])
    for r in conn.execute(
            "SELECT g.venue_key, g.lat, g.lng FROM games g"
            " LEFT JOIN venue_aliases a ON a.platform_name_norm = g.platform_name_norm"
            " WHERE a.canonical_id IS NULL AND g.source = 'flashx' AND g.lat IS NOT NULL AND g.lng IS NOT NULL"):
        places.setdefault(commute.place_key(None, r["venue_key"]), commute.amap_coord(r["lat"], r["lng"]))
    return places

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

def _fetch_zhongyulian(cr, today, progress):
    games = []
    for day in range(3):  # 今天/明天/后天
        text, bad = _call("zhongyulian", zhongyulian.fetch, cr, day,
                          lambda n: progress(f"中羽联 第 {day + 1}/3 天 · 已翻 {n} 页"))
        if bad:
            return games, True
        games += zhongyulian.parse_games(text, day, today)
    return games, False

def _fetch_flashx(cr, today, progress):
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
            progress(f"闪动 第 {i + 1}/3 天 · 已翻 {page} 页")  # 首页的 pages 是封顶假值(100)，不报总数
            page += 1
    return games, False

def refresh(conn, creds, config, progress=lambda msg: None):
    # 只做「抓取 + 入库」，秒级返回；通勤/POI 归类交给 compute_pending 后台跑
    # progress(文字)：每发完一个请求报一次（两平台都给不出可信的总页数，只报已翻页数）
    today = date.today()
    games, auth_failed, errors = [], [], []
    for source, run in (("zhongyulian", lambda c: _fetch_zhongyulian(c, today, progress)),
                        ("flashx", lambda c: _fetch_flashx(c, today, progress))):
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

def compute_pending(db_path, config, progress=lambda msg: None):
    # 后台线程用：自开连接。先归类 pending 馆名，再给缺通勤的场馆补算（按场馆缓存，可重复调）
    # progress(文字)：「归类场馆 i/n」→「算各场馆通勤 i/n」
    conn = db.connect(db_path)
    try:
        classify_pending(conn, progress)
        origins_ll = _origins_ll(conn)
        places = commute_places(conn)
        for i, (pk, dest) in enumerate(places.items(), 1):
            try:
                _ensure_commute(conn, pk, dest, origins_ll)
            except Exception:  # 单个场馆的高德异常只跳过该场馆
                pass
            progress(f"算各场馆通勤 {i}/{len(places)}")
    finally:
        conn.close()

def pending_count(conn):
    # 还能靠重试补上的场馆数：当前球局涉及的待自动搜索馆名 + 通勤三项未算齐的场馆；用于提示用户重试
    origins = [r["key"] for r in conn.execute("SELECT key FROM origins WHERE lat IS NOT NULL")]
    if not origins:
        return 0
    n = conn.execute("SELECT COUNT(DISTINCT a.platform_name_norm) c FROM venue_aliases a"
                     " JOIN games g ON g.platform_name_norm = a.platform_name_norm"
                     f" WHERE {_RETRYABLE}").fetchone()["c"]
    for pk in commute_places(conn):
        complete = conn.execute(
            "SELECT COUNT(*) c FROM commute WHERE place_key=? AND drive_min IS NOT NULL"
            " AND transit_min IS NOT NULL AND walk_m IS NOT NULL", (pk,)).fetchone()["c"]
        if complete < len(origins):
            n += 1
    return n

def unmatched_count(conn):
    # 高德自动搜不到（没搜到/重名）、要去管理页手动处理的馆名数（只算还有球局的）
    return conn.execute("SELECT COUNT(DISTINCT a.platform_name_norm) c FROM venue_aliases a"
                        " JOIN games g ON g.platform_name_norm = a.platform_name_norm"
                        " WHERE a.origin = 'pending' AND a.last_search IN ('empty','dup')").fetchone()["c"]

def venue_rows(conn):
    # 球馆管理页数据：每个馆名一行，附平台馆名示例、来源、球局数、统一球场、可疑原因、是否已有通勤
    stats = {}
    for r in conn.execute("SELECT platform_name_norm n, venue_name, source, lat, lng FROM games"
                          " WHERE platform_name_norm != ''"):
        s = stats.setdefault(r["n"], {"names": Counter(), "sources": set(), "count": 0, "flashx": []})
        s["names"][r["venue_name"].strip()] += 1
        s["sources"].add(r["source"])
        s["count"] += 1
        if r["source"] == "flashx" and r["lat"] is not None and r["lng"] is not None:
            s["flashx"].append((r["lat"], r["lng"]))
    with_commute = {r[0] for r in conn.execute("SELECT DISTINCT place_key FROM commute")}
    out = []
    for a in conn.execute("SELECT a.*, c.amap_name, c.amap_id, c.lat, c.lng FROM venue_aliases a"
                          " LEFT JOIN canonical_venues c ON c.id = a.canonical_id"):
        s = stats.get(a["platform_name_norm"])
        name = s["names"].most_common(1)[0][0] if s else a["platform_name_norm"]
        canon = ({"id": a["canonical_id"], "amap_name": a["amap_name"], "lat": a["lat"], "lng": a["lng"],
                  "manual_coord": a["amap_id"] is None}
                 if a["canonical_id"] else None)
        suspect = []
        if a["origin"] == "auto" and canon:  # 只审自动归类；用户看过的(confirmed/manual)不再判
            fx = s and s["flashx"]
            fx_ll = (statistics.median(p[0] for p in fx), statistics.median(p[1] for p in fx)) if fx else None
            suspect = venues.suspect_reasons(name, canon["amap_name"], fx_ll, (canon["lat"], canon["lng"]))
        out.append({"platform_name_norm": a["platform_name_norm"], "name": name,
                    "sources": sorted(s["sources"]) if s else [], "game_count": s["count"] if s else 0,
                    "origin": a["origin"], "last_search": a["last_search"], "updated_at": a["updated_at"], "canonical": canon,
                    "suspect": suspect,
                    "has_commute": bool(canon) and commute.place_key(canon["id"], None) in with_commute})
    out.sort(key=lambda v: -v["game_count"])
    return out
