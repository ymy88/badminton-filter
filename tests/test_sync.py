import json
import db, sync

def _g(**kw):
    base = dict(source="flashx", source_id="1", title="t", date="2999-01-01",  # 远期，避开 upsert 清理过期
                start_time="19:00", end_time="21:00", duration_min=120,
                venue_name="馆", venue_key="flashx:1",
                price=30.0, applied_num=0, total_num=12, lat=31.1, lng=121.5,
                raw_text="t", tags="[]", organizer="o", club_name="c", detail_ref="1")
    base.update(kw); return base

def test_upsert_and_dedup():
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(applied_num=0)])
    sync.upsert_games(conn, [_g(applied_num=5)])  # 同 id，更新报名数
    rows = conn.execute("SELECT applied_num FROM games").fetchall()
    assert len(rows) == 1 and rows[0]["applied_num"] == 5

def test_upsert_purges_past():
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(source_id="old", date="2000-01-01"),
                             _g(source_id="new", date="2999-01-01")])
    ids = {r["source_id"] for r in conn.execute("SELECT source_id FROM games")}
    assert ids == {"new"}

def test_same_source_id_different_source():
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(source="zhongyulian", source_id="1"), _g(source="flashx", source_id="1")])
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2

def test_refresh_no_creds_reports_auth_failed():
    # 没凭证时不能静默返回 0，要把平台计入 auth_failed，界面才会提示去抓取
    conn = db.connect(":memory:"); db.init_schema(conn)
    r = sync.refresh(conn, {}, {})
    assert set(r["auth_failed"]) == {"zhongyulian", "flashx"}
    assert r["ok"] is False and r["count"] == 0

def test_refresh_stores_games_without_computing_commute(monkeypatch):
    # 刷新只入库、秒回，不在请求里算通勤（通勤交给 compute_pending 后台）
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync, "_fetch_flashx", lambda cr, today, progress: ([_g(source_id="1", lat=31.1, lng=121.5)], False))
    monkeypatch.setattr(sync, "_fetch_zhongyulian", lambda cr, today, progress: ([], False))
    r = sync.refresh(conn, {"flashx": {"x": 1}, "zhongyulian": {"x": 1}}, {})
    assert r["count"] == 1 and r["ok"] is True
    assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM commute").fetchone()[0] == 0

def test_refresh_progress_per_request(monkeypatch):
    # 每发完一个请求报一次进度：第几天 + 已翻页数
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.flashx, "fetch", lambda cr, d, page: '{"result":{"pages":2}}')
    monkeypatch.setattr(sync.flashx, "parse_games", lambda t: [])
    monkeypatch.setattr(sync.zhongyulian, "_post", lambda cr, day, skip: '{"ok":1,"list":{"other_club_movement_list":'
                        + ('[{}]' if skip else '[' + ','.join(['{}'] * 10) + ']') + '}}')  # 首页满 10 条 → 再翻一页
    monkeypatch.setattr(sync.zhongyulian, "parse_games", lambda t, d, today: [])
    monkeypatch.setattr(sync.creds_mod, "is_auth_failure", lambda *a: False)
    prog = []
    sync.refresh(conn, {"flashx": {"x": 1}, "zhongyulian": {"x": 1}}, {}, progress=prog.append)
    assert prog[:2] == ["中羽联 第 1/3 天 · 已翻 1 页", "中羽联 第 1/3 天 · 已翻 2 页"]
    assert len(prog) == 12 and prog[-1] == "闪动 第 3/3 天 · 已翻 2 页"

def test_ensure_commute_retries_incomplete_and_merges(monkeypatch):
    # 部分路线上次失败（行在但字段缺）→ 下次重算补齐，已成功的字段保留；三项齐全后不再调
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','v:x',12,NULL,NULL)")
    conn.commit()
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": None, "transit_min": 40, "walk_m": None})
    sync._ensure_commute(conn, "v:x", "121,31", {"home": "121.5,31.2"})
    r = conn.execute("SELECT drive_min,transit_min,walk_m FROM commute WHERE place_key='v:x'").fetchone()
    assert r["drive_min"] == 12 and r["transit_min"] == 40 and r["walk_m"] is None  # 旧值保留+补公交；步行仍缺
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 15, "transit_min": 42, "walk_m": 600})
    sync._ensure_commute(conn, "v:x", "121,31", {"home": "121.5,31.2"})
    r = conn.execute("SELECT drive_min,transit_min,walk_m FROM commute WHERE place_key='v:x'").fetchone()
    assert (r["drive_min"], r["transit_min"], r["walk_m"]) == (15, 42, 600)
    monkeypatch.setattr(sync.commute, "route_minutes",
                        lambda o, d: (_ for _ in ()).throw(AssertionError("齐全不该再算")))
    sync._ensure_commute(conn, "v:x", "121,31", {"home": "121.5,31.2"})  # 齐全 → 不调用

# ---- 馆名对应表 / 归类 / 通勤场馆 ----

POI = {"amap_id": "B1", "name": "徐家汇体育公园", "lat": 31.18, "lng": 121.43, "district": "徐汇区", "address": ""}

def _alias(conn, norm):
    return conn.execute("SELECT * FROM venue_aliases WHERE platform_name_norm=?", (norm,)).fetchone()

def test_upsert_records_norm_and_pending_alias():
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(venue_name="堂颂羽毛球馆（淞南店）"), _g(source_id="2", venue_name="")])
    assert conn.execute("SELECT platform_name_norm FROM games WHERE source_id='1'").fetchone()[0] == "堂颂羽毛球馆(淞南店)"
    a = _alias(conn, "堂颂羽毛球馆(淞南店)")
    assert a["origin"] == "pending" and a["canonical_id"] is None and a["updated_at"] is None  # 未搜过
    assert conn.execute("SELECT COUNT(*) FROM venue_aliases").fetchone()[0] == 1  # 空馆名不记

def test_upsert_keeps_existing_alias_and_overwrites_coords():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(5,'馆','B5',30.0,120.0)")
    conn.execute("INSERT INTO venue_aliases(platform_name_norm,canonical_id,origin,updated_at) VALUES('馆',5,'manual','2026-01-01')")
    sync.upsert_games(conn, [_g(lat=31.1, lng=121.5)])
    sync.upsert_games(conn, [_g(lat=31.2, lng=121.6)])  # 再刷新：平台坐标变了 → 用新值
    assert dict(_alias(conn, "馆"))["origin"] == "manual"
    assert tuple(conn.execute("SELECT lat,lng FROM games").fetchone()) == (31.2, 121.6)

def test_classify_pending_found_creates_auto_and_searches_once_per_name(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(source_id="1", venue_name="万体汇"), _g(source_id="2", venue_name="万体汇 ")])
    calls = []
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: calls.append(n) or [POI])
    prog = []
    sync.classify_pending(conn, prog.append)
    a = _alias(conn, "万体汇")
    assert a["origin"] == "auto" and a["updated_at"] is not None
    assert conn.execute("SELECT amap_name FROM canonical_venues WHERE id=?", (a["canonical_id"],)).fetchone()[0] == "徐家汇体育公园"
    assert len(calls) == 1 and prog == ["归类场馆 1/1"]

def test_classify_pending_miss_marks_empty_and_not_retried(monkeypatch):
    # 高德正常返回但没结果 → last_search=empty，不再自动搜（留给管理页手动处理）
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g()])
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: [])
    sync.classify_pending(conn)
    a = _alias(conn, "馆")
    assert a["origin"] == "pending" and a["canonical_id"] is None and a["last_search"] == "empty"
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: (_ for _ in ()).throw(AssertionError("不该再搜")))
    sync.classify_pending(conn)

def test_classify_pending_amap_error_marks_error_and_retries(monkeypatch):
    # 高德接口出错 → last_search=error，下次重试；成功后清掉
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g()])
    monkeypatch.setattr(sync.commute, "poi_search",
                        lambda n, limit=1: (_ for _ in ()).throw(sync.commute.AmapError("x")))
    sync.classify_pending(conn)
    assert _alias(conn, "馆")["last_search"] == "error"
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: [POI])
    sync.classify_pending(conn)
    a = _alias(conn, "馆")
    assert a["origin"] == "auto" and a["last_search"] is None

def test_classify_reuses_existing_amap_id(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(7,'徐家汇体育公园','B1',31.18,121.43)")
    sync.upsert_games(conn, [_g(venue_name="徐家汇体育公园综合馆")])
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: [POI])
    sync.classify_pending(conn)
    assert _alias(conn, "徐家汇体育公园综合馆")["canonical_id"] == 7
    assert conn.execute("SELECT COUNT(*) FROM canonical_venues").fetchone()[0] == 1

def test_classify_skips_non_pending(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(source_id=str(i), venue_name=n) for i, n in enumerate(["甲", "乙", "丙"])])
    conn.execute("UPDATE venue_aliases SET origin='none'")
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: (_ for _ in ()).throw(AssertionError("不该搜")))
    sync.classify_pending(conn)

def test_classify_does_not_override_manual_set_meanwhile(monkeypatch):
    # 归类进行中用户在管理页改成了 manual → 自动结果不得覆盖
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(9,'手选馆','B9',31.0,121.0)")
    sync.upsert_games(conn, [_g()])
    def search(n, limit=1):
        conn.execute("UPDATE venue_aliases SET origin='manual', canonical_id=9")
        return [POI]
    monkeypatch.setattr(sync.commute, "poi_search", search)
    sync.classify_pending(conn)
    a = _alias(conn, "馆")
    assert a["origin"] == "manual" and a["canonical_id"] == 9

def test_classify_duplicate_amap_name_stays_pending(monkeypatch):
    # 新 POI 与已有统一球场同名不同 id（唯一约束冲突）→ 留在待处理
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(amap_name,amap_id,lat,lng) VALUES('徐家汇体育公园','B0',31.0,121.0)")
    sync.upsert_games(conn, [_g()])
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: [POI])
    sync.classify_pending(conn)
    a = _alias(conn, "馆")
    assert a["origin"] == "pending" and a["last_search"] == "dup"

def test_commute_places_uses_amap_coords_flashx_fallback_skips_zyl():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(3,'甲','B3',31.0,121.0)")
    sync.upsert_games(conn, [_g(source_id="1", venue_name="甲", lat=31.9, lng=121.9),       # 已归类：用高德坐标
                             _g(source_id="2", venue_name="乙", venue_key="flashx:2", lat=31.5, lng=121.5),  # 闪动未归类
                             _g(source="zhongyulian", source_id="3", venue_name="丙", venue_key="zhongyulian:3", lat=31.7, lng=121.7)])  # 中羽联坐标即使有也不用
    conn.execute("UPDATE venue_aliases SET origin='auto', canonical_id=3 WHERE platform_name_norm='甲'")
    assert sync.commute_places(conn) == {"c:3": "121.0,31.0", "v:flashx:2": "121.5,31.5"}

def test_compute_pending_classifies_then_fills_commute(tmp_path, monkeypatch):
    p = str(tmp_path / "t.db")
    conn = db.connect(p); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','家',31.2,121.4)")
    sync.upsert_games(conn, [_g()])
    conn.close()
    monkeypatch.setattr(sync.commute, "poi_search", lambda n, limit=1: [POI])
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 18, "transit_min": 30, "walk_m": 900})
    prog = []
    sync.compute_pending(p, {}, progress=prog.append)
    conn = db.connect(p)
    cid = _alias(conn, "馆")["canonical_id"]
    assert conn.execute("SELECT drive_min FROM commute WHERE place_key=?", (f"c:{cid}",)).fetchone()[0] == 18
    assert prog == ["归类场馆 1/1", "算各场馆通勤 1/1"]

def test_compute_pending_progress_counts_only_missing_commute(tmp_path, monkeypatch):
    # 进度分母只数缺通勤的场馆：家+公司都齐的跳过；只齐了一个出发地的仍要算
    p = str(tmp_path / "t.db")
    conn = db.connect(p); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','家',31.2,121.4),('company','司',31.1,121.3)")
    sync.upsert_games(conn, [_g(source_id=str(i), venue_name=f"馆{i}", venue_key=f"flashx:{i}") for i in (1, 2, 3)])
    conn.execute("UPDATE venue_aliases SET origin='none'")
    conn.executemany("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES(?,?,10,20,500)",
                     [("home", "v:flashx:1"), ("company", "v:flashx:1"), ("home", "v:flashx:2")])
    conn.commit(); conn.close()
    calls = []
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: calls.append(o) or {"drive_min": 1, "transit_min": 2, "walk_m": 3})
    prog = []
    sync.compute_pending(p, {}, progress=prog.append)
    assert prog == ["算各场馆通勤 1/2", "算各场馆通勤 2/2"]
    assert len(calls) == 3  # 馆2 补公司、馆3 家+公司

def test_pending_count_counts_pending_names_and_missing_commute():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','x',31.0,121.0)")
    sync.upsert_games(conn, [_g(source="zhongyulian", source_id="1", venue_name="未归类", lat=None, lng=None),  # pending
                             _g(source_id="2", venue_name="缺通勤", venue_key="flashx:2"),                     # 闪动未归类、无通勤
                             _g(source_id="3", venue_name="齐全", venue_key="flashx:3")])
    conn.execute("UPDATE venue_aliases SET origin='none' WHERE platform_name_norm IN ('缺通勤','齐全')")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','v:flashx:3',10,20,500)")
    assert sync.pending_count(conn) == 2  # 未归类 + 缺通勤；齐全不计
    # 高德没搜到/重名的不靠重试，不计入待重试，计入 unmatched；接口出错的照样计入待重试
    conn.execute("UPDATE venue_aliases SET last_search='empty' WHERE platform_name_norm='未归类'")
    assert sync.pending_count(conn) == 1 and sync.unmatched_count(conn) == 1
    conn.execute("UPDATE venue_aliases SET last_search='error' WHERE platform_name_norm='未归类'")
    assert sync.pending_count(conn) == 2 and sync.unmatched_count(conn) == 0

