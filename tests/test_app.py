import db, commute, app as appmod

def _app():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO games(source,source_id,title,date,venue_key,canonical_id)"
                 " VALUES('flashx','1','t','2999-01-01','flashx:1',NULL)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min)"
                 " VALUES('home',?,20,35)", (commute.place_key(None, "flashx:1"),))
    conn.commit()
    return appmod.create_app(conn, {}).test_client()

def test_games_endpoint_attaches_commute():
    g = _app().get("/api/games").get_json()["games"][0]
    assert g["source_id"] == "1"
    assert g["canonical_name"] is None
    assert g["commute"]["home"]["drive_min"] == 20
    assert g["commute"]["home"]["transit_min"] == 35
    assert g["commute"]["company"] is None

def test_filters_js_is_served():
    # 页面 <script src="filters.js"> 依赖此路由，缺了真机会 404 白屏
    r = _app().get("/filters.js")
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
        progress(3, 9)
        gate.wait(2)
        return {"ok": True, "count": 7, "auth_failed": [], "errors": []}
    monkeypatch.setattr(sync, "refresh", fake_refresh)
    client = appmod.create_app(conn, {}, db_path=str(tmp_path / "x.db")).test_client()
    r = client.post("/api/refresh").get_json()
    assert r["refreshing"] is True and r["result"] is None   # 立刻返回，不阻塞
    assert _wait(lambda: client.get("/api/refresh/status").get_json()["progress"] == [3, 9])
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

def test_save_origins_geocodes_and_clears_commute(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','旧址',31.0,121.0)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min) VALUES('home','v:x',20,30)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min) VALUES('company','v:x',10,15)")
    conn.commit()
    monkeypatch.setattr(commute, "geocode", lambda a: (31.2, 121.5))
    client = appmod.create_app(conn, {}).test_client()
    r = client.post("/api/origins", json={"home": "新址", "company": ""}).get_json()
    row = conn.execute("SELECT address,lat,lng FROM origins WHERE key='home'").fetchone()
    assert row["address"] == "新址" and row["lat"] == 31.2
    assert r["home"]["ok"] is True
    # 地址变了 → 该出发地的 commute 被清
    assert conn.execute("SELECT COUNT(*) c FROM commute WHERE origin='home'").fetchone()["c"] == 0
    # 只清该出发地：company 缓存仍在
    assert conn.execute("SELECT COUNT(*) c FROM commute WHERE origin='company'").fetchone()["c"] == 1

def test_save_origins_geocode_fail_keeps_old(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','旧址',31.0,121.0)")
    conn.commit()
    monkeypatch.setattr(commute, "geocode", lambda a: None)
    client = appmod.create_app(conn, {}).test_client()
    r = client.post("/api/origins", json={"home": "乱填查不到", "company": ""}).get_json()
    assert conn.execute("SELECT lat FROM origins WHERE key='home'").fetchone()["lat"] == 31.0  # 不覆盖
    assert r["home"]["ok"] is False

def test_get_origins_and_config():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','家',31.0,121.0)")
    conn.execute("INSERT INTO canonical_venues(canonical_name) VALUES('馆A')")
    conn.commit()
    c = appmod.create_app(conn, {}).test_client()
    o = c.get("/api/origins").get_json()
    assert o["home"] == {"address": "家", "has_coord": True}
    assert o["company"] == {"address": "", "has_coord": False}
    cfg = c.get("/api/config").get_json()
    assert cfg["canonical_names"] == ["馆A"]

def test_save_origins_unchanged_keeps_commute(monkeypatch):
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','旧址',31.0,121.0)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min) VALUES('home','v:x',20,30)")
    conn.commit()
    monkeypatch.setattr(commute, "geocode", lambda a: (_ for _ in ()).throw(AssertionError("不该调")))
    c = appmod.create_app(conn, {}).test_client()
    c.post("/api/origins", json={"home": "旧址", "company": ""})
    assert conn.execute("SELECT COUNT(*) c FROM commute WHERE origin='home'").fetchone()["c"] == 1

def test_save_origins_non_dict_body():
    conn = db.connect(":memory:"); db.init_schema(conn)
    c = appmod.create_app(conn, {}).test_client()
    assert c.post("/api/origins", json=["x"]).status_code == 400

def test_capture_reports_returned_platforms(monkeypatch):
    import creds
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(creds, "capture", lambda: {"flashx": {"t": 1}})
    r = appmod.create_app(conn, {}).test_client().post("/api/creds/capture").get_json()
    assert r == {"flashx": True}
