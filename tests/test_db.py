import db

def test_init_schema_creates_all_tables():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"games", "canonical_venues", "venues", "commute", "origins"} <= names

def test_games_unique_source_id():
    conn = db.connect(":memory:"); db.init_schema(conn)
    row = ("flashx", "1", "t", "2026-10-11", "19:00", "21:00", 120,
           "馆", "flashx:1", None, 30.0, 0, 12, None, None, "t", "[]", "o", "c", "1")
    cols = ("source,source_id,title,date,start_time,end_time,duration_min,"
            "venue_name,venue_key,canonical_id,price,applied_num,total_num,"
            "lat,lng,raw_text,tags,organizer,club_name,detail_ref")
    q = f"INSERT OR REPLACE INTO games({cols}) VALUES ({','.join('?'*20)})"
    conn.execute(q, row); conn.execute(q, row)   # 同 id 两次
    assert conn.execute("SELECT COUNT(*) c FROM games").fetchone()["c"] == 1
