import sqlite3
import pytest
import db

def test_init_schema_creates_all_tables():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"games", "canonical_venues", "venue_aliases", "commute", "origins"} <= names
    assert "venues" not in names  # 地理编码缓存表已删

def test_games_unique_source_id():
    conn = db.connect(":memory:"); db.init_schema(conn)
    q = "INSERT OR REPLACE INTO games(source,source_id,date) VALUES('flashx','1','2026-10-11')"
    conn.execute(q); conn.execute(q)   # 同 id 两次
    assert conn.execute("SELECT COUNT(*) c FROM games").fetchone()["c"] == 1

def test_alias_origin_checked_and_amap_name_unique():
    conn = db.connect(":memory:"); db.init_schema(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO venue_aliases(platform_name_norm,origin) VALUES('x','bogus')")
    conn.execute("INSERT INTO canonical_venues(amap_name,amap_id,lat,lng) VALUES('馆','B1',31.0,121.0)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO canonical_venues(amap_name,amap_id,lat,lng) VALUES('馆','B2',31.0,121.0)")
    with pytest.raises(sqlite3.IntegrityError):  # 统一球场必来自高德 POI，必有坐标
        conn.execute("INSERT INTO canonical_venues(amap_name,amap_id) VALUES('无坐标','B3')")
