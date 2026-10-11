import json
import db, sync

def _g(**kw):
    base = dict(source="flashx", source_id="1", title="t", date="2026-10-11",
                start_time="19:00", end_time="21:00", duration_min=120,
                venue_name="馆", venue_key="flashx:1", canonical_id=None,
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

def _canon(**kw):
    base = dict(id=1, canonical_name="万体", lat=None, lng=None,
                name_patterns=["万体"], platform_ids=[]); base.update(kw); return [base]

def test_enrich_keeps_game_coords_when_canonical_has_no_coords(monkeypatch):
    # #2 回归：归到无坐标规范馆时，不能用 None 抹掉球局自带坐标，通勤要能算出
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 20, "transit_min": 35})
    g = _g(venue_name="万体汇", lat=31.1, lng=121.5)
    sync.enrich_venue(conn, g, _canon(), {"home": "121.4,31.2"})
    assert g["canonical_id"] == 1 and g["lat"] == 31.1  # 自带坐标保留
    pk = sync.commute.place_key(1, g["venue_key"])
    row = conn.execute("SELECT drive_min FROM commute WHERE origin='home' AND place_key=?", (pk,)).fetchone()
    assert row and row["drive_min"] == 20  # 通勤没被跳过

def test_enrich_uses_canonical_coords_when_present(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 1, "transit_min": 2})
    g = _g(venue_name="万体汇", lat=31.1, lng=121.5)
    sync.enrich_venue(conn, g, _canon(lat=30.0, lng=120.0), {"home": "121.4,31.2"})
    assert (g["lat"], g["lng"]) == (30.0, 120.0)  # 规范馆有坐标则用它

def test_enrich_route_fail_writes_no_commute(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "poi_search", lambda n: None)  # 用球局自带坐标，不走网络
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": None, "transit_min": None})
    sync.enrich_venue(conn, _g(lat=31.1, lng=121.5), [], {"home": "121.4,31.2"})
    assert conn.execute("SELECT COUNT(*) FROM commute").fetchone()[0] == 0  # 完全失败不缓存

def test_enrich_geocodes_unclassified_without_coords(monkeypatch):
    # 中羽联无自带坐标、未归类、且 POI 也搜不到 → 退回按馆名地理编码后算通勤
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "poi_search", lambda name: None)
    monkeypatch.setattr(sync.commute, "geocode", lambda name: (31.5, 121.5))
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 9, "transit_min": None})
    g = _g(source="zhongyulian", venue_key="zyl:5", venue_name="某馆", lat=None, lng=None)
    sync.enrich_venue(conn, g, [], {"home": "121.4,31.2"})
    assert g["lat"] == 31.5
    assert conn.execute("SELECT COUNT(*) FROM commute").fetchone()[0] == 1

def test_auto_canonical_creates_from_poi(monkeypatch):
    # 未匹配 → 高德 POI 搜索结果名作为统一球场名，自动建规范馆
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "poi_search",
                        lambda n: {"amap_id": "B1", "name": "徐家汇体育公园", "lat": 31.18, "lng": 121.43})
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 15, "transit_min": 25})
    g = _g(venue_name="万体汇", venue_key="flashx:9", lat=31.1, lng=121.5)
    sync.enrich_venue(conn, g, [], {"home": "121.4,31.2"})
    row = conn.execute("SELECT * FROM canonical_venues").fetchone()
    assert row["canonical_name"] == "徐家汇体育公园" and row["amap_id"] == "B1"
    assert g["canonical_id"] == row["id"] and (g["lat"], g["lng"]) == (31.18, 121.43)
    assert conn.execute("SELECT COUNT(*) FROM commute").fetchone()[0] == 1

def test_auto_canonical_dedups_cross_platform_by_amap_id(monkeypatch):
    # 两平台不同叫法搜到同一 POI → 归并到同一规范馆
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "poi_search",
                        lambda n: {"amap_id": "B1", "name": "徐家汇体育公园", "lat": 31.18, "lng": 121.43})
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 15, "transit_min": 25})
    canon = []
    g1 = _g(source="flashx", venue_name="万体汇", venue_key="flashx:9", lat=31.1, lng=121.5)
    g2 = _g(source="zhongyulian", venue_name="徐家汇体育公园综合馆", venue_key="zyl:3", lat=None, lng=None)
    sync.enrich_venue(conn, g1, canon, {"home": "121.4,31.2"})
    sync.enrich_venue(conn, g2, canon, {"home": "121.4,31.2"})
    assert conn.execute("SELECT COUNT(*) FROM canonical_venues").fetchone()[0] == 1
    assert g1["canonical_id"] == g2["canonical_id"]
    pats = json.loads(conn.execute("SELECT name_patterns FROM canonical_venues").fetchone()["name_patterns"])
    assert "万体汇" in pats and "徐家汇体育公园综合馆" in pats

def test_auto_canonical_poi_miss_falls_back_to_game_coords(monkeypatch):
    # POI 搜不到 → 不建规范馆，用球局自带坐标照常算通勤
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync.commute, "poi_search", lambda n: None)
    monkeypatch.setattr(sync.commute, "route_minutes", lambda o, d: {"drive_min": 7, "transit_min": None})
    g = _g(venue_name="野鸡馆", venue_key="flashx:77", lat=31.1, lng=121.5)
    sync.enrich_venue(conn, g, [], {"home": "121.4,31.2"})
    assert g["canonical_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM canonical_venues").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM commute").fetchone()[0] == 1

def test_seed_canonical_idempotent():
    conn = db.connect(":memory:"); db.init_schema(conn)
    cfg = {"canonical_venues": [{"canonical_name": "A馆", "lat": 1.0, "lng": 2.0,
                                 "name_patterns": ["A"], "platform_ids": []}]}
    sync.seed_canonical_venues(conn, cfg); sync.seed_canonical_venues(conn, cfg)
    rows = conn.execute("SELECT id FROM canonical_venues").fetchall()
    assert len(rows) == 1
    cfg["canonical_venues"][0]["lat"] = 9.0
    sync.seed_canonical_venues(conn, cfg)
    lst = sync.load_canonical_list(conn)
    assert len(lst) == 1 and lst[0]["id"] == rows[0]["id"] and lst[0]["lat"] == 9.0
    assert lst[0]["name_patterns"] == ["A"]
