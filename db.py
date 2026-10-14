# db.py
import sqlite3

def connect(path):
    # 打开连接；行以 sqlite3.Row 返回，可按列名取值
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")   # 允许后台算通勤的连接与主连接并发读写
    conn.execute("PRAGMA busy_timeout=5000")  # 写锁冲突时等待而非立即报错
    return conn

def init_schema(conn):
    # 建全部表（幂等）。场馆归属：games.platform_name_norm → venue_aliases → canonical_venues
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS games(
      id INTEGER PRIMARY KEY, source TEXT, source_id TEXT,
      title TEXT, date TEXT, start_time TEXT, end_time TEXT, duration_min INTEGER,
      venue_name TEXT, platform_name_norm TEXT, venue_key TEXT,
      price REAL, applied_num INTEGER, total_num INTEGER,
      lat REAL, lng REAL, raw_text TEXT, tags TEXT,
      organizer TEXT, club_name TEXT, detail_ref TEXT,
      UNIQUE(source, source_id));
    CREATE TABLE IF NOT EXISTS canonical_venues(
      id INTEGER PRIMARY KEY, amap_name TEXT UNIQUE, amap_id TEXT UNIQUE, lat REAL, lng REAL);
    CREATE TABLE IF NOT EXISTS venue_aliases(
      platform_name_norm TEXT PRIMARY KEY,
      canonical_id INTEGER REFERENCES canonical_venues(id),
      origin TEXT NOT NULL CHECK(origin IN ('pending','auto','confirmed','manual','none')),
      updated_at TEXT);
    CREATE TABLE IF NOT EXISTS commute(
      origin TEXT, place_key TEXT, drive_min INTEGER, transit_min INTEGER,
      walk_m INTEGER, computed_at TEXT, PRIMARY KEY(origin, place_key));
    CREATE TABLE IF NOT EXISTS origins(
      key TEXT PRIMARY KEY, address TEXT, lat REAL, lng REAL);
    CREATE TABLE IF NOT EXISTS kw_history(word TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS filters(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT);
    """)
    conn.commit()
