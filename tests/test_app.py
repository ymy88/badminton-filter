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
    c = appmod.create_app(conn, {"keywords": {"include": ["x"], "exclude": []}}).test_client()
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
