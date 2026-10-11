import db, sync

def _g(**kw):
    base = dict(source="flashx", source_id="1", title="t", date="2026-10-11",
                start_time="19:00", end_time="21:00", duration_min=120,
                venue_name="馆", venue_key="flashx:1", canonical_id=None,
                price=30.0, applied_num=0, total_num=12, lat=31.1, lng=121.5,
                raw_text="t", tags="[]", organizer="o", club_name="c", detail_ref="1")
    base.update(kw); return base

def test_upsert_and_dedup(tmp_path):
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(applied_num=0)])
    sync.upsert_games(conn, [_g(applied_num=5)])  # 同 id，更新报名数
    rows = conn.execute("SELECT applied_num FROM games").fetchall()
    assert len(rows) == 1 and rows[0]["applied_num"] == 5

def test_upsert_purges_past(tmp_path):
    conn = db.connect(":memory:"); db.init_schema(conn)
    sync.upsert_games(conn, [_g(source_id="old", date="2000-01-01"),
                             _g(source_id="new", date="2999-01-01")])
    ids = {r["source_id"] for r in conn.execute("SELECT source_id FROM games")}
    assert ids == {"new"}
