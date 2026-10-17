import db, commute, app as appmod

def _app():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,name,lat,lng,pos) VALUES('home','家',31.0,121.0,0),('company','公司',31.1,121.1,1)")
    conn.execute("INSERT INTO games(source,source_id,title,date,venue_key,platform_name_norm)"
                 " VALUES('flashx','1','t','2999-01-01','flashx:1','某馆')")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min)"
                 " VALUES('home',?,20,35)", (commute.place_key(None, "flashx:1"),))
    conn.commit()
    return appmod.create_app(conn, {}).test_client()

def test_games_endpoint_attaches_commute():
    g = _app().get("/api/games").get_json()["games"][0]
    assert g["source_id"] == "1"
    assert g["amap_name"] is None
    assert g["commute"]["home"]["drive_min"] == 20
    assert g["commute"]["home"]["transit_min"] == 35
    assert g["commute"]["company"] is None

def test_games_endpoint_joins_alias_for_amap_name_and_commute():
    # 归属查询时关联：改 venue_aliases 立即生效，通勤按 c:<canonical_id> 取
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(4,'徐家汇体育公园','B1',31.18,121.43)")
    conn.execute("INSERT INTO games(source,source_id,date,venue_key,platform_name_norm) VALUES('zhongyulian','1','2999-01-01','zhongyulian:9','万体汇')")
    conn.execute("INSERT INTO venue_aliases(platform_name_norm,canonical_id,origin,updated_at) VALUES('万体汇',4,'manual',NULL)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','c:4',12,30,800)")
    conn.execute("INSERT INTO origins(key,name) VALUES('home','家')")
    conn.commit()
    g = appmod.create_app(conn, {}).test_client().get("/api/games").get_json()["games"][0]
    assert g["amap_name"] == "徐家汇体育公园" and g["canonical_id"] == 4
    assert g["commute"]["home"]["drive_min"] == 12

def test_filters_js_is_served():
    # 页面 <script src="/static/filters.js"> 依赖此路由，缺了真机会 404 白屏
    r = _app().get("/static/filters.js")
    assert r.status_code == 200
    assert b"applyFilters" in r.data

def test_commute_status_shape():
    conn = db.connect(":memory:"); db.init_schema(conn)
    r = appmod.create_app(conn, {}).test_client().get("/api/commute/status").get_json()
    assert r["computing"] is False and r["commute_places"] == 0

def test_refresh_does_not_start_compute(monkeypatch, tmp_path):
    # 刷新球局与算通勤已拆开：POST /api/refresh 不再触发通勤
    import sync
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync, "refresh", lambda *a, **k: {"ok": True, "count": 0, "auth_failed": [], "errors": []})
    called = []
    monkeypatch.setattr(sync, "compute_pending", lambda p, c, **k: called.append(1))
    client = appmod.create_app(conn, {}, db_path=str(tmp_path / "x.db")).test_client()
    r = client.post("/api/refresh").get_json()
    assert "computing" not in r
    import time; time.sleep(0.05)
    assert called == []  # 刷新不算通勤

def test_refresh_runs_in_background(monkeypatch, tmp_path):
    # 拉取在后台线程跑：POST 立刻返回不阻塞，结果经 /api/refresh/status 取回
    import threading, time, sync
    def _wait(cond, t=2.0):
        end = time.time() + t
        while time.time() < end:
            if cond(): return True
            time.sleep(0.01)
        return False
    conn = db.connect(":memory:"); db.init_schema(conn)
    gate = threading.Event()
    def fake_refresh(c, cr, cfg, progress):
        progress("闪动 第 1/3 天 · 已翻 2 页")
        gate.wait(2)
        return {"ok": True, "count": 7, "auth_failed": [], "errors": []}
    monkeypatch.setattr(sync, "refresh", fake_refresh)
    client = appmod.create_app(conn, {}, db_path=str(tmp_path / "x.db")).test_client()
    r = client.post("/api/refresh").get_json()
    assert r["refreshing"] is True and r["result"] is None   # 立刻返回，不阻塞
    assert _wait(lambda: client.get("/api/refresh/status").get_json()["progress"] == "闪动 第 1/3 天 · 已翻 2 页")
    assert client.get("/api/refresh/status").get_json()["refreshing"] is True
    gate.set()
    assert _wait(lambda: client.get("/api/refresh/status").get_json()["refreshing"] is False)
    assert client.get("/api/refresh/status").get_json()["result"]["count"] == 7

def test_commute_single_flight_and_rerun(monkeypatch, tmp_path):
    # 连点「算通勤」：后台不并发（单飞），但期间的触发不丢（跑完补一轮）
    import threading, time, sync
    def _wait(cond, t=2.0):
        end = time.time() + t
        while time.time() < end:
            if cond(): return True
            time.sleep(0.01)
        return False
    conn = db.connect(":memory:"); db.init_schema(conn)
    calls, gate = [], threading.Event()
    monkeypatch.setattr(sync, "compute_pending", lambda p, c, **k: (calls.append(1), gate.wait(2)))
    client = appmod.create_app(conn, {}, db_path=str(tmp_path / "x.db")).test_client()
    assert client.post("/api/commute").get_json()["computing"] is True
    assert _wait(lambda: len(calls) == 1)
    client.post("/api/commute")                 # 占锁中 → 不并发，置 rerun
    time.sleep(0.05)
    assert len(calls) == 1
    gate.set()                                   # 放行 → 因 rerun 再补一轮
    assert _wait(lambda: len(calls) == 2)

def test_filters_save_and_load_roundtrip():
    # 整份筛选条件存一行：保存→读回原样；未保存过为 None
    conn = db.connect(":memory:"); db.init_schema(conn)
    client = appmod.create_app(conn, {}).test_client()
    assert client.get("/api/config").get_json()["filters"] is None
    f = {"date": "1", "onlyOpen": True, "venueInc": ["万体汇"], "kwInc": ["练球"], "kwExc": []}
    assert client.post("/api/filters", json=f).get_json()["ok"] is True
    assert client.get("/api/config").get_json()["filters"] == f
    assert client.post("/api/filters", json=["x"]).status_code == 400

def test_kw_history_is_append_only():
    # 筛选里用过的关键字进历史(永久)，从筛选里删掉不影响历史
    conn = db.connect(":memory:"); db.init_schema(conn)
    client = appmod.create_app(conn, {}).test_client()
    client.post("/api/filters", json={"kwInc": ["练球", ""], "kwExc": []})
    assert client.get("/api/config").get_json()["kw_history"] == ["练球"]
    client.post("/api/filters", json={"kwInc": [], "kwExc": []})  # 删掉筛选里的词
    cfg = client.get("/api/config").get_json()
    assert cfg["filters"]["kwInc"] == []           # 筛选清空
    assert cfg["kw_history"] == ["练球"]            # 历史仍在
    client.post("/api/filters", json={"kwInc": ["练球"], "kwExc": ["对抗"]})
    assert client.get("/api/config").get_json()["kw_history"] == ["练球", "对抗"]  # 累积去重

def test_pinyin():
    conn = db.connect(":memory:"); db.init_schema(conn)
    r = appmod.create_app(conn, {}).test_client().post("/api/pinyin", json={"texts": ["万体汇A馆"]}).get_json()
    assert r == {"万体汇A馆": ["wantihuiaguan", "wthag"]}

def _origins_app():
    # 两个已有出发地 + 各自一条通勤缓存
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,name,address,lat,lng,show,pos) VALUES"
                 "('home','家','旧址',31.0,121.0,1,0),('company','公司','司',31.1,121.1,1,1)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min) VALUES('home','v:x',20,30),('company','v:x',10,15)")
    conn.commit()
    return conn, appmod.create_app(conn, {}).test_client()

def _cnt(conn, origin):
    return conn.execute("SELECT COUNT(*) c FROM commute WHERE origin=?", (origin,)).fetchone()["c"]

HOME = {"key": "home", "name": "家", "show": True}
COMPANY = {"key": "company", "name": "公司", "show": True}
LOC = {"address": "宜山路700号（徐汇区 宜山路700号）", "lat": 31.17, "lng": 121.42}

def test_get_origins_list_ordered():
    conn, c = _origins_app()
    assert c.get("/api/origins").get_json() == [
        {"key": "home", "name": "家", "address": "旧址", "lat": 31.0, "lng": 121.0, "show": True},
        {"key": "company", "name": "公司", "address": "司", "lat": 31.1, "lng": 121.1, "show": True}]

def test_add_origin_gets_new_key_and_pos():
    conn, c = _origins_app()
    r = c.post("/api/origins", json={"origins": [HOME, COMPANY, {"name": "父母家", "show": False, "loc": LOC}]}).get_json()
    assert [x["ok"] for x in r] == [True, True, True]
    assert r[2]["changed"] is True and r[2]["key"].startswith("o") and len(r[2]["key"]) == 9
    row = conn.execute("SELECT * FROM origins WHERE key=?", (r[2]["key"],)).fetchone()
    assert (row["name"], row["address"], row["lat"], row["lng"], row["show"], row["pos"]) == ("父母家", LOC["address"], 31.17, 121.42, 0, 2)
    assert r[0]["changed"] is False and _cnt(conn, "home") == 1  # 没带 loc 的不动

def test_rename_and_hide_keep_commute():
    conn, c = _origins_app()
    c.post("/api/origins", json={"origins": [{"key": "home", "name": "老家", "show": False}, COMPANY]})
    row = conn.execute("SELECT name,show,lat FROM origins WHERE key='home'").fetchone()
    assert (row["name"], row["show"], row["lat"]) == ("老家", 0, 31.0)
    assert _cnt(conn, "home") == 1

def test_move_origin_clears_only_its_commute():
    conn, c = _origins_app()
    r = c.post("/api/origins", json={"origins": [{**HOME, "loc": LOC}, COMPANY]}).get_json()
    assert r[0] == {"ok": True, "changed": True, "key": "home"}
    row = conn.execute("SELECT address,lat,lng FROM origins WHERE key='home'").fetchone()
    assert (row["address"], row["lat"], row["lng"]) == (LOC["address"], 31.17, 121.42)
    assert _cnt(conn, "home") == 0 and _cnt(conn, "company") == 1

def test_delete_origin_removes_row_and_commute():
    conn, c = _origins_app()
    c.post("/api/origins", json={"origins": [HOME]})
    assert conn.execute("SELECT COUNT(*) c FROM origins WHERE key='company'").fetchone()["c"] == 0
    assert _cnt(conn, "company") == 0 and _cnt(conn, "home") == 1

def test_save_origins_item_errors_dont_block_others():
    conn, c = _origins_app()
    r = c.post("/api/origins", json={"origins": [
        {"key": "home", "name": "  ", "show": True},                                   # 名称空
        COMPANY,
        {"name": "外地", "show": True, "loc": {"address": "x", "lat": 39.9, "lng": 116.4}},  # 不在上海
        {"name": "没位置", "show": True},                                              # 新增缺 loc
        {"key": "nope", "name": "幽灵", "show": True}]}).get_json()                    # 不存在的 key
    assert [x["ok"] for x in r] == [False, True, False, False, False]
    assert r[0]["msg"] == "名称不能为空"
    assert r[2]["msg"] == "坐标不在上海范围内，检查一下是不是复制错了"
    assert r[3]["msg"] == "先选位置"
    assert conn.execute("SELECT name FROM origins WHERE key='home'").fetchone()["name"] == "家"  # 失败不改
    assert conn.execute("SELECT COUNT(*) c FROM origins").fetchone()["c"] == 2                 # 失败的新增不入库

def test_save_origins_bad_body():
    conn, c = _origins_app()
    assert c.post("/api/origins", json=["x"]).status_code == 400
    assert c.post("/api/origins", json={"origins": ["x"]}).status_code == 400

def test_save_origins_409_while_computing(monkeypatch, tmp_path):
    import threading, time, sync
    conn, _ = _origins_app()
    gate = threading.Event()
    monkeypatch.setattr(sync, "compute_pending", lambda p, cfg, **k: gate.wait(2))
    c = appmod.create_app(conn, {}, db_path=str(tmp_path / "x.db")).test_client()
    c.post("/api/commute")
    time.sleep(0.05)
    assert c.post("/api/origins", json={"origins": [HOME]}).status_code == 409                  # 删除
    assert c.post("/api/origins", json={"origins": [{**HOME, "loc": LOC}, COMPANY]}).status_code == 409  # 改位置
    assert conn.execute("SELECT COUNT(*) c FROM origins").fetchone()["c"] == 2
    r = c.post("/api/origins", json={"origins": [{**HOME, "name": "老家"}, COMPANY]})            # 只改名不受限
    assert r.status_code == 200 and r.get_json()[0]["ok"] is True
    gate.set()

def test_capture_reports_returned_platforms(monkeypatch):
    import creds
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(creds, "capture", lambda: {"flashx": {"t": 1}})
    r = appmod.create_app(conn, {}).test_client().post("/api/creds/capture").get_json()
    assert r == {"flashx": True}

# ---- 球馆管理接口 ----

def _venue_app(monkeypatch=None):
    # 两个馆名：「堂颂羽毛球馆(淞南店)」自动归到大华店(分店不同 → 可疑)；「野馆」pending
    import sync
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(1,'堂颂羽毛球馆(大华店)','B1',31.2904,121.4119)")
    conn.execute("INSERT INTO canonical_venues(id,amap_name,amap_id,lat,lng) VALUES(2,'万体汇','B2',31.18,121.44)")
    g = dict(title="t", date="2999-01-01", start_time="19:00", end_time="21:00", duration_min=120,
             price=30.0, applied_num=0, total_num=12, raw_text="t", tags="[]", organizer="o", club_name="c", detail_ref="1")
    sync.upsert_games(conn, [
        dict(g, source="flashx", source_id="1", venue_name="堂颂羽毛球馆(淞南店)", venue_key="flashx:1", lat=31.3422, lng=121.4119),
        dict(g, source="zhongyulian", source_id="2", venue_name="堂颂羽毛球馆（淞南店）", venue_key="zhongyulian:2", lat=None, lng=None),
        dict(g, source="zhongyulian", source_id="3", venue_name="野馆", venue_key="zhongyulian:3", lat=None, lng=None)])
    conn.execute("UPDATE venue_aliases SET origin='auto', canonical_id=1 WHERE platform_name_norm='堂颂羽毛球馆(淞南店)'")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','c:2',10,20,500)")
    conn.execute("INSERT INTO origins(key,name) VALUES('home','家')")
    conn.commit()
    return conn, appmod.create_app(conn, {}).test_client()

def _row(client, norm):
    return next(v for v in client.get("/api/venues").get_json()["venues"] if v["platform_name_norm"] == norm)

def test_venues_list_rows_and_suspect():
    conn, c = _venue_app()
    v = _row(c, "堂颂羽毛球馆(淞南店)")
    assert v["game_count"] == 2 and v["sources"] == ["flashx", "zhongyulian"]
    assert v["origin"] == "auto" and v["canonical"]["amap_name"] == "堂颂羽毛球馆(大华店)"
    assert v["suspect"][0] == "分店不同" and v["suspect"][1].startswith("闪动坐标相距")
    assert v["has_commute"] is False
    p = _row(c, "野馆")
    assert p["origin"] == "pending" and p["canonical"] is None and p["suspect"] == []
    # 全部统一球场（含未被引用的「万体汇」）供编辑区选择
    assert {x["amap_name"] for x in c.get("/api/venues").get_json()["canonicals"]} == {"堂颂羽毛球馆(大华店)", "万体汇"}
    assert c.get("/venues").status_code == 200

def test_assign_existing_canonical_sets_manual():
    conn, c = _venue_app()
    r = c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "canonical_id": 2}).get_json()
    assert r == {"ok": True, "canonical_id": 2, "has_commute": True}
    v = _row(c, "野馆")
    assert v["origin"] == "manual" and v["canonical"]["id"] == 2
    # 主页立即生效
    g = [x for x in c.get("/api/games").get_json()["games"] if x["venue_name"] == "野馆"][0]
    assert g["amap_name"] == "万体汇" and g["commute"]["home"]["drive_min"] == 10

def test_assign_new_poi_creates_canonical_and_reuses_amap_id():
    conn, c = _venue_app()
    poi = {"amap_id": "B9", "name": "堂颂羽毛球馆(淞南店)", "lat": 31.34, "lng": 121.41}
    r = c.post("/api/venues/assign", json={"platform_name_norm": "堂颂羽毛球馆(淞南店)", "poi": poi}).get_json()
    assert r["ok"] and r["has_commute"] is False
    new_id = r["canonical_id"]
    r2 = c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "poi": poi}).get_json()
    assert r2["canonical_id"] == new_id  # 同 amap_id 复用
    assert conn.execute("SELECT COUNT(*) FROM canonical_venues").fetchone()[0] == 3

def test_assign_manual_coord_creates_canonical_without_poi():
    conn, c = _venue_app()
    r = c.post("/api/venues/assign", json={"platform_name_norm": "野馆",
                                           "coord": {"name": " 野馆 ", "lat": 31.1894, "lng": 121.4356}}).get_json()
    assert r["ok"] and r["has_commute"] is False
    v = _row(c, "野馆")
    assert v["origin"] == "manual" and v["canonical"]["amap_name"] == "野馆" and v["canonical"]["manual_coord"] is True
    assert _row(c, "堂颂羽毛球馆(淞南店)")["canonical"]["manual_coord"] is False
    # 两个手动坐标球场都没有 POI id，不能互相复用
    r2 = c.post("/api/venues/assign", json={"platform_name_norm": "堂颂羽毛球馆(淞南店)",
                                            "coord": {"name": "另一个", "lat": 31.2, "lng": 121.4}}).get_json()
    assert r2["canonical_id"] != r["canonical_id"]

def test_assign_manual_coord_errors():
    conn, c = _venue_app()
    post = lambda coord: c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "coord": coord})
    assert post({"name": "x", "lat": 39.9, "lng": 116.4}).status_code == 400   # 不在上海
    assert post({"name": "x", "lat": 121.4, "lng": 31.2}).status_code == 400   # 经纬度写反
    assert post({"name": " ", "lat": 31.2, "lng": 121.4}).status_code == 400   # 没名字
    assert post({"name": "x", "lat": "31.2", "lng": 121.4}).status_code == 400
    r = post({"name": "万体汇", "lat": 31.2, "lng": 121.4})                     # 与已有统一球场重名
    assert r.status_code == 409 and not conn.in_transaction
    assert _row(c, "野馆")["origin"] == "pending"

def test_assign_errors():
    conn, c = _venue_app()
    assert c.post("/api/venues/assign", json={"platform_name_norm": "野馆"}).status_code == 400
    assert c.post("/api/venues/assign", json=["x"]).status_code == 400
    assert c.post("/api/venues/assign", json={"platform_name_norm": "没有", "canonical_id": 2}).status_code == 404
    assert c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "canonical_id": 99}).status_code == 404
    bad = {"amap_id": "B9", "name": "万体汇", "lat": 31.0, "lng": 121.0}  # 与已有统一球场同名不同 id
    r = c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "poi": bad})
    assert r.status_code == 409 and _row(c, "野馆")["origin"] == "pending"
    assert not conn.in_transaction  # 409 后必须回滚，否则主连接占着写锁，后台刷新/算通勤写不进
    assert c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "poi": dict(bad, lat="x")}).status_code == 400

def test_confirm_none_reset():
    conn, c = _venue_app()
    assert c.post("/api/venues/confirm", json={"platform_name_norm": "野馆"}).status_code == 409  # 只能确认 auto
    assert c.post("/api/venues/confirm", json={"platform_name_norm": "堂颂羽毛球馆(淞南店)"}).get_json()["ok"]
    v = _row(c, "堂颂羽毛球馆(淞南店)")
    assert v["origin"] == "confirmed" and v["canonical"]["id"] == 1 and v["suspect"] == []  # 确认后不再判可疑
    c.post("/api/venues/none", json={"platform_name_norm": "野馆"})
    assert _row(c, "野馆")["origin"] == "none"
    c.post("/api/venues/reset", json={"platform_name_norm": "堂颂羽毛球馆(淞南店)"})
    v = _row(c, "堂颂羽毛球馆(淞南店)")
    assert v["origin"] == "pending" and v["canonical"] is None and v["updated_at"] is None
    assert c.post("/api/venues/none", json={"platform_name_norm": "没有"}).status_code == 404

def test_restore_puts_row_back():
    # 撤销：整行写回操作前的状态
    conn, c = _venue_app()
    before = _row(c, "堂颂羽毛球馆(淞南店)")
    c.post("/api/venues/none", json={"platform_name_norm": "堂颂羽毛球馆(淞南店)"})
    keep = {k: before[k] for k in ("origin", "last_search", "updated_at")}
    r = c.post("/api/venues/restore", json={"platform_name_norm": "堂颂羽毛球馆(淞南店)", "canonical_id": 1, **keep})
    assert r.get_json()["ok"]
    after = _row(c, "堂颂羽毛球馆(淞南店)")
    assert after["origin"] == "auto" and after["canonical"]["id"] == 1 and after["updated_at"] == before["updated_at"]
    # 未搜过的 pending 写回：canonical_id 为空
    conn.execute("UPDATE venue_aliases SET last_search='empty', updated_at='2026-01-01' WHERE platform_name_norm='野馆'")
    c.post("/api/venues/assign", json={"platform_name_norm": "野馆", "canonical_id": 2})
    c.post("/api/venues/restore", json={"platform_name_norm": "野馆", "origin": "pending", "canonical_id": None,
                                        "last_search": "empty", "updated_at": "2026-01-01"})
    p = _row(c, "野馆")
    assert (p["origin"], p["canonical"], p["last_search"], p["updated_at"]) == ("pending", None, "empty", "2026-01-01")

def test_restore_errors():
    conn, c = _venue_app()
    base = {"platform_name_norm": "野馆", "last_search": None, "updated_at": None}
    assert c.post("/api/venues/restore", json=dict(base, origin="bogus", canonical_id=None)).status_code == 400
    assert c.post("/api/venues/restore", json=dict(base, origin="manual", canonical_id=None)).status_code == 400
    assert c.post("/api/venues/restore", json=dict(base, origin="pending", canonical_id=2)).status_code == 400
    assert c.post("/api/venues/restore", json=dict(base, origin="pending", canonical_id=None, last_search="x")).status_code == 400
    assert c.post("/api/venues/restore", json=dict(base, origin="manual", canonical_id=99)).status_code == 404
    assert _row(c, "野馆")["origin"] == "pending"

def test_prune_deletes_unreferenced_canonicals_and_their_commute():
    # 万体汇(2) 没有馆名关联 → 连同通勤缓存删掉；大华店(1) 有关联 → 保留
    conn, c = _venue_app()
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','c:1',10,20,500)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','v:flashx:9',10,20,500)")
    conn.commit()
    assert c.post("/api/venues/prune").get_json() == {"ok": True, "deleted": 1}
    assert [r[0] for r in conn.execute("SELECT id FROM canonical_venues")] == [1]
    assert sorted(r[0] for r in conn.execute("SELECT place_key FROM commute")) == ["c:1", "v:flashx:9"]
    assert c.post("/api/venues/prune").get_json() == {"ok": True, "deleted": 0}

def test_amap_search(monkeypatch):
    seen = {}
    monkeypatch.setattr(commute, "poi_search", lambda q, limit=1: seen.update(q=q, limit=limit) or [{"amap_id": "B1"}])
    conn = db.connect(":memory:"); db.init_schema(conn)
    c = appmod.create_app(conn, {}).test_client()
    assert c.get("/api/amap/search?q= 堂颂 ").get_json() == {"pois": [{"amap_id": "B1"}]}
    assert seen == {"q": "堂颂", "limit": 10}
    assert c.get("/api/amap/search?q=").status_code == 400
    monkeypatch.setattr(commute, "poi_search", lambda q, limit=1: (_ for _ in ()).throw(commute.AmapError("高德接口出错")))
    r = c.get("/api/amap/search?q=x")
    assert r.status_code == 502 and r.get_json()["msg"] == "高德接口出错"

def test_games_zyl_unclassified_has_no_commute_even_with_stale_cache():
    # 中羽联坐标不可信：未归到统一球场的中羽联球局通勤一律未知（旧地理编码留下的 v: 缓存也不用）
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO games(source,source_id,date,venue_key,platform_name_norm) VALUES('zhongyulian','1','2999-01-01','zhongyulian:9','野馆')")
    conn.execute("INSERT INTO venue_aliases(platform_name_norm,canonical_id,origin,updated_at) VALUES('野馆',NULL,'none',NULL)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min,walk_m) VALUES('home','v:zhongyulian:9',15,30,900)")
    conn.execute("INSERT INTO origins(key,name,pos) VALUES('home','家',0),('company','公司',1)")
    conn.commit()
    g = appmod.create_app(conn, {}).test_client().get("/api/games").get_json()["games"][0]
    assert g["commute"] == {"home": None, "company": None}

