import db, commute, app as appmod

def _app():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO games(source,source_id,title,date,venue_key,canonical_id)"
                 " VALUES('flashx','1','t','2999-01-01','flashx:1',NULL)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min)"
                 " VALUES('home',?,20,35)", (commute.place_key(None, "flashx:1"),))
    conn.commit()
    return appmod.create_app(conn, {"keywords": {"include": [], "exclude": []}}).test_client()

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

def test_refresh_status_shape():
    conn = db.connect(":memory:"); db.init_schema(conn)
    r = appmod.create_app(conn, {}).test_client().get("/api/refresh/status").get_json()
    assert r["computing"] is False and r["commute_places"] == 0

def test_refresh_compute_single_flight_and_rerun(monkeypatch, tmp_path):
    # 连点刷新：后台通勤不并发（单飞），但期间的触发不丢（跑完补一轮）
    import threading, time, sync
    def _wait(cond, t=2.0):
        end = time.time() + t
        while time.time() < end:
            if cond(): return True
            time.sleep(0.01)
        return False
    conn = db.connect(":memory:"); db.init_schema(conn)
    monkeypatch.setattr(sync, "refresh", lambda *a: {"ok": True, "count": 0, "auth_failed": [], "errors": []})
    calls, gate = [], threading.Event()
    monkeypatch.setattr(sync, "compute_pending", lambda p, c: (calls.append(1), gate.wait(2)))
    client = appmod.create_app(conn, {}, db_path=str(tmp_path / "x.db")).test_client()
    client.post("/api/refresh")                 # 起第一轮，卡在 gate
    assert _wait(lambda: len(calls) == 1)
    client.post("/api/refresh")                 # 第二次：占锁中 → 不并发，置 rerun
    time.sleep(0.05)
    assert len(calls) == 1                       # 没有并发起第二个
    gate.set()                                   # 放行 → 收尾时因 rerun 再补一轮
    assert _wait(lambda: len(calls) == 2)

def test_keywords_save_and_load_roundtrip():
    # 关键字存库：保存→读回，空串被剔除
    conn = db.connect(":memory:"); db.init_schema(conn)
    client = appmod.create_app(conn, {}).test_client()
    assert client.get("/api/config").get_json()["keywords"] == {"include": [], "exclude": []}
    assert client.post("/api/keywords", json={"include": ["练球", ""], "exclude": ["教练"]}).get_json()["ok"] is True
    assert client.get("/api/config").get_json()["keywords"] == {"include": ["练球"], "exclude": ["教练"]}

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
    conn.execute("INSERT INTO settings(key,value) VALUES('keywords','{\"include\":[\"x\"],\"exclude\":[]}')")
    conn.commit()
    c = appmod.create_app(conn, {}).test_client()
    o = c.get("/api/origins").get_json()
    assert o["home"] == {"address": "家", "has_coord": True}
    assert o["company"] == {"address": "", "has_coord": False}
    cfg = c.get("/api/config").get_json()
    assert cfg["keywords"]["include"] == ["x"] and cfg["canonical_names"] == ["馆A"]

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
