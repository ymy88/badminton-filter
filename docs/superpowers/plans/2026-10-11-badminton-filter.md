# 球局筛选工具 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把中羽联和闪动FlashX的上海羽毛球球局拉进本地 SQLite，在 localhost 网页上按用户条件筛选、排序、看详情。

**Architecture:** Flask 薄后端只做三件事——刷新入库、返回全量球局、凭证抓取；筛选和排序全在前端原生 JS。两平台接口各自封装，归一化成统一「球局」记录。场馆靠用户维护的规范表归类，通勤走高德并按规范馆缓存。

**Tech Stack:** Python 3（`uv` 管理环境）/ Flask / 标准库 sqlite3 / 原生 HTML+JS / pytest（纯逻辑）/ node（前端筛选自测）/ mitmdump（凭证抓取，手测）

**Spec:** `docs/superpowers/specs/2026-10-11-badminton-filter-design.md`

## Global Constraints

- 技术栈仅限：Flask + 标准库 `sqlite3` + 原生 HTML/JS。不引入 ORM、前端框架、构建工具。
- Python 环境用 `uv` 管理：依赖写 `pyproject.toml`，锁定 `uv.lock`（入库），所有 Python 命令走 `uv run ...`。
- 高德 key 只从环境变量 `GAO_DE_MAP_API_KEY` 读，不硬编码、不入库。
- 高德坐标顺序一律 `经度,纬度`（lng,lat）。
- 敏感文件进 `.gitignore`：`creds.json`（600 权限）、`games.db`、`*.mitm`。
- 中羽联只取 `day=0/1/2`；请求固定 `city="上海"`。
- 出发地只有 `home`、`company` 两个；城市上海。
- 代码注释、commit message 用中文。
- commit message 末尾加：`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Review Focus

- **中羽联返回非 JSON**（city 为空时后端 Fatal error 吐 HTML）：解析必须容错，不能当 JSON 崩溃 → Task 2 加测试。
- **中羽联 `time` 异常**（空、缺 `~`、大跨度如 `08:00~22:30`）：拆分与 `duration` 要容错，不产出负时长或崩溃 → Task 2 加测试。
- **闪动 `showCost` 非纯数字**（空串、缺字段）：转数字要容错为 `None`，不崩 → Task 3 加测试。
- **凭证失效信号误判**（中羽联 `indexList` 的 `ok:-2` 才是 skey 失效；`info` 的「签名错误」不在本工具路径内）：失效检测只认 `indexList`/`activity/list` 的鉴权失败 → Task 7 加测试。
- **通勤 API 失败**（配额用尽、场馆查不到）：该场馆通勤置 `None`、不阻塞其余球局 → Task 5 加测试。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `pyproject.toml` / `uv.lock` | uv 项目与依赖（Flask；dev: pytest） |
| `config.example.json` | 配置样例（出发地、规范场馆表、端口、关键词预设） |
| `db.py` | SQLite 建表 + 读写助手 |
| `platforms/zhongyulian.py` | 中羽联：请求 + 解析/归一化（纯函数 `parse_games`） |
| `platforms/flashx.py` | 闪动FlashX：请求 + 解析/归一化（纯函数 `parse_games`） |
| `venues.py` | 场馆归类：`classify(venue_name, venue_key, canonical_list)` 纯函数 |
| `commute.py` | 高德地理编码/路径规划 + 坐标与缓存键助手 |
| `creds.py` | 凭证读写、失效检测（纯函数 `is_auth_failure`）、mitmdump 抓取 |
| `sync.py` | 刷新编排：拉两平台 → 归一化 → 入库 → 归类 → 补坐标通勤 |
| `app.py` | Flask 路由 + 启动开浏览器 |
| `static/index.html` | 前端单页 |
| `static/filters.js` | 前端筛选/排序纯函数（node 可自测） |
| `tests/` | pytest 纯逻辑测试 |

依赖顺序：Task1(db) → Task2/3(归一化) → Task4(归类) → Task5(commute) → Task6(sync) → Task7(creds) → Task8(app) → Task9(filters.js) → Task10(前端页面)。

---

## Task 1: 项目骨架 + SQLite 建表

**Files:**
- Create: `pyproject.toml`, `uv.lock`, `config.example.json`, `db.py`, `tests/test_db.py`

**Interfaces:**
- Produces: `db.connect(path) -> sqlite3.Connection`；`db.init_schema(conn)` 建全部表；连接设 `row_factory = sqlite3.Row`。

- [ ] **Step 0: 初始化 uv 项目**

```bash
uv init --bare --python 3.12   # 生成 pyproject.toml，不建示例代码
uv add flask
uv add --dev pytest
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_db.py
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL（`No module named 'db'`）

- [ ] **Step 3: 写 db.py**

```python
# db.py
import sqlite3

def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS games(
      id INTEGER PRIMARY KEY, source TEXT, source_id TEXT,
      title TEXT, date TEXT, start_time TEXT, end_time TEXT, duration_min INTEGER,
      venue_name TEXT, venue_key TEXT, canonical_id INTEGER,
      price REAL, applied_num INTEGER, total_num INTEGER,
      lat REAL, lng REAL, raw_text TEXT, tags TEXT,
      organizer TEXT, club_name TEXT, detail_ref TEXT,
      UNIQUE(source, source_id));
    CREATE TABLE IF NOT EXISTS canonical_venues(
      id INTEGER PRIMARY KEY, canonical_name TEXT, lat REAL, lng REAL,
      name_patterns TEXT, platform_ids TEXT);
    CREATE TABLE IF NOT EXISTS venues(
      venue_key TEXT PRIMARY KEY, name TEXT, lat REAL, lng REAL,
      address TEXT, geocoded_at TEXT);
    CREATE TABLE IF NOT EXISTS commute(
      origin TEXT, place_key TEXT, drive_min INTEGER, transit_min INTEGER,
      computed_at TEXT, PRIMARY KEY(origin, place_key));
    CREATE TABLE IF NOT EXISTS origins(
      key TEXT PRIMARY KEY, address TEXT, lat REAL, lng REAL);
    """)
    conn.commit()
```

同时写 `config.example.json`（依赖已由 Step 0 的 `uv add` 装好）：

```json
{
  "port": 8765,
  "canonical_venues": [
    {"canonical_name": "徐家汇体育公园(万体汇)",
     "name_patterns": ["徐家汇体育公园", "万体汇", "徐汇体育公园综合馆"],
     "platform_ids": []}
  ],
  "keywords": {"include": [], "exclude": []}
}
```

出发地地址**不在 config**：运行后在网页界面填，存入 db 的 `origins` 表（Task 8 的 `/api/origins`）。

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_db.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml uv.lock config.example.json db.py tests/test_db.py
git commit -m "feat: uv 项目初始化 + SQLite 建表与连接助手"
```

---

## Task 2: 中羽联解析归一化

**Files:**
- Create: `platforms/__init__.py`, `platforms/zhongyulian.py`, `tests/test_zhongyulian.py`

**Interfaces:**
- Consumes: 无。
- Produces: `zhongyulian.parse_games(resp_text: str, day: int, today: date) -> list[dict]`。返回统一记录 dict，键：`source, source_id, title, date, start_time, end_time, duration_min, venue_name, venue_key, price, applied_num, total_num, lat, lng, raw_text, tags, organizer, club_name, detail_ref`。`lat/lng=None`（中羽联无坐标）。`source='zhongyulian'`，`venue_key=f"zhongyulian:{address_id}"`，`tags='[]'`。非 JSON 或 `ok!=1` → 返回 `[]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_zhongyulian.py
from datetime import date
import json
from platforms import zhongyulian as z

TODAY = date(2026, 10, 11)

def _resp(items):
    return json.dumps({"ok": 1, "list": {
        "my_club_movement_list": items, "other_club_movement_list": []}})

def _item(**kw):
    base = {"activity_id": "a1", "name": "周日局", "time": "19:00~21:00",
            "price": 85, "address": "万体汇", "address_id": "v1",
            "joinstate": "7/18", "organizer": "张三", "club_name": "某club"}
    base.update(kw); return base

def test_basic_fields_and_day0_date():
    g = z.parse_games(_resp([_item()]), day=0, today=TODAY)[0]
    assert g["source"] == "zhongyulian" and g["source_id"] == "a1"
    assert g["date"] == "2026-10-11"
    assert g["start_time"] == "19:00" and g["end_time"] == "21:00"
    assert g["duration_min"] == 120
    assert g["venue_key"] == "zhongyulian:v1"
    assert g["applied_num"] == 7 and g["total_num"] == 18
    assert g["price"] == 85 and g["lat"] is None

def test_day_offset():
    assert z.parse_games(_resp([_item()]), 2, TODAY)[0]["date"] == "2026-10-13"

def test_merges_both_lists_and_dedup():
    resp = json.dumps({"ok": 1, "list": {
        "my_club_movement_list": [_item(activity_id="a1")],
        "other_club_movement_list": [_item(activity_id="a1"), _item(activity_id="a2")]}})
    games = z.parse_games(resp, 0, TODAY)
    assert {g["source_id"] for g in games} == {"a1", "a2"}

# Review Focus: 非 JSON（city 为空的 Fatal error HTML）不崩
def test_non_json_returns_empty():
    assert z.parse_games("<br /><b>Fatal error</b> line 113", 0, TODAY) == []

def test_ok_not_1_returns_empty():
    assert z.parse_games(json.dumps({"ok": -2, "error": "x"}), 0, TODAY) == []

# Review Focus: time 异常容错
def test_time_malformed():
    g = z.parse_games(_resp([_item(time="")]), 0, TODAY)[0]
    assert g["start_time"] is None and g["duration_min"] is None
    g2 = z.parse_games(_resp([_item(time="08:00~22:30")]), 0, TODAY)[0]
    assert g2["duration_min"] == 870
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/test_zhongyulian.py -v` → FAIL

- [ ] **Step 3: 写 platforms/zhongyulian.py 的 parse_games**

```python
# platforms/zhongyulian.py
import json
from datetime import timedelta

def _split_time(t):
    if not t or "~" not in t:
        return None, None, None
    a, b = (x.strip() for x in t.split("~", 1))
    try:
        dur = (_mins(b) - _mins(a))
        return a, b, dur if dur >= 0 else None
    except ValueError:
        return a, b, None

def _mins(hhmm):
    h, m = hhmm.split(":"); return int(h) * 60 + int(m)

def _split_join(s):
    try:
        a, b = s.split("/"); return int(a), int(b)
    except (ValueError, AttributeError):
        return None, None

def parse_games(resp_text, day, today):
    try:
        data = json.loads(resp_text)
    except (ValueError, TypeError):
        return []
    if data.get("ok") != 1:
        return []
    lst = data.get("list") or {}
    items = (lst.get("my_club_movement_list") or []) + \
            (lst.get("other_club_movement_list") or [])
    d = (today + timedelta(days=day)).isoformat()
    out, seen = [], set()
    for it in items:
        sid = it.get("activity_id")
        if sid in seen:
            continue
        seen.add(sid)
        st, et, dur = _split_time(it.get("time"))
        ap, tot = _split_join(it.get("joinstate"))
        out.append({
            "source": "zhongyulian", "source_id": sid,
            "title": it.get("name"), "date": d,
            "start_time": st, "end_time": et, "duration_min": dur,
            "venue_name": it.get("address"),
            "venue_key": f"zhongyulian:{it.get('address_id')}",
            "price": it.get("price"), "applied_num": ap, "total_num": tot,
            "lat": None, "lng": None, "raw_text": it.get("name") or "",
            "tags": "[]", "organizer": it.get("organizer"),
            "club_name": it.get("club_name"), "detail_ref": sid,
        })
    return out
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run pytest tests/test_zhongyulian.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add platforms/__init__.py platforms/zhongyulian.py tests/test_zhongyulian.py
git commit -m "feat: 中羽联列表解析与归一化（含非JSON/异常容错）"
```

---

## Task 3: 闪动FlashX解析归一化

**Files:**
- Create: `platforms/flashx.py`, `tests/test_flashx.py`

**Interfaces:**
- Produces: `flashx.parse_games(resp_text: str) -> list[dict]`。统一记录键同 Task 2。`source='flashx'`，`venue_key=f"flashx:{placeId}"`，`lat/lng` 取 `latitude/longitude`，`tags` 透传 `displayTags` 字符串，`price` 由 `showCost` 转 float（失败为 None），`duration` 由 `startTime/endTime` 算。`success` 非真或非 JSON → `[]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_flashx.py
import json
from platforms import flashx as f

def _resp(records):
    return json.dumps({"success": True, "code": 200,
                       "result": {"records": records}})

def _rec(**kw):
    data = {"id": 5798714, "activityTitle": "周日A局", "activityDate": "2026-10-11",
            "startTime": "19:00", "endTime": "21:00",
            "activityAddress": "Okay羽毛球馆", "placeId": 56811,
            "showCost": "30", "applyMemberNum": 11, "needMemberNum": 12,
            "latitude": 31.168994, "longitude": 121.562419,
            "displayTags": "[\"对抗局\"]", "organizerName": "Okay俱乐部"}
    data.update(kw); return {"data": data, "type": 1}

def test_basic_fields():
    g = f.parse_games(_resp([_rec()]))[0]
    assert g["source"] == "flashx" and g["source_id"] == "5798714"
    assert g["date"] == "2026-10-11" and g["duration_min"] == 120
    assert g["venue_key"] == "flashx:56811"
    assert g["price"] == 30.0
    assert g["applied_num"] == 11 and g["total_num"] == 12
    assert g["lat"] == 31.168994 and g["lng"] == 121.562419
    assert g["tags"] == "[\"对抗局\"]"

# Review Focus: showCost 非纯数字
def test_showcost_bad():
    assert f.parse_games(_resp([_rec(showCost="")]))[0]["price"] is None
    assert f.parse_games(_resp([_rec(showCost="面议")]))[0]["price"] is None

def test_non_json_or_fail_returns_empty():
    assert f.parse_games("<html>") == []
    assert f.parse_games(json.dumps({"success": False})) == []
```

- [ ] **Step 2: 运行确认失败** → `uv run pytest tests/test_flashx.py -v` → FAIL

- [ ] **Step 3: 写 platforms/flashx.py**

```python
# platforms/flashx.py
import json

def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def _dur(st, et):
    try:
        (h1, m1), (h2, m2) = (x.split(":") for x in (st, et))
        return (int(h2)*60+int(m2)) - (int(h1)*60+int(m1))
    except (ValueError, AttributeError):
        return None

def parse_games(resp_text):
    try:
        data = json.loads(resp_text)
    except (ValueError, TypeError):
        return []
    if not data.get("success"):
        return []
    out = []
    for r in data.get("result", {}).get("records", []):
        d = r.get("data", {})
        st, et = d.get("startTime"), d.get("endTime")
        out.append({
            "source": "flashx", "source_id": str(d.get("id")),
            "title": d.get("activityTitle"), "date": d.get("activityDate"),
            "start_time": st, "end_time": et, "duration_min": _dur(st, et),
            "venue_name": d.get("activityAddress"),
            "venue_key": f"flashx:{d.get('placeId')}",
            "price": _to_float(d.get("showCost")),
            "applied_num": d.get("applyMemberNum"), "total_num": d.get("needMemberNum"),
            "lat": d.get("latitude"), "lng": d.get("longitude"),
            "raw_text": d.get("activityTitle") or "",
            "tags": d.get("displayTags") or "[]",
            "organizer": d.get("organizerName"), "club_name": d.get("organizerName"),
            "detail_ref": str(d.get("id")),
        })
    return out
```

- [ ] **Step 4: 运行确认通过** → PASS

- [ ] **Step 5: 提交**

```bash
git add platforms/flashx.py tests/test_flashx.py
git commit -m "feat: 闪动FlashX列表解析与归一化（含价格/非JSON容错）"
```

---

## Task 4: 场馆归类

**Files:**
- Create: `venues.py`, `tests/test_venues.py`

**Interfaces:**
- Consumes: 规范馆 list，每项 dict：`{id, canonical_name, lat, lng, name_patterns: list[str], platform_ids: list[str]}`。
- Produces: `venues.classify(venue_name, venue_key, canonical_list) -> dict | None`。先按 `platform_ids` 精确匹配 `venue_key`，再按 `name_patterns` 对 `venue_name` 包含匹配；命中返回该规范馆 dict，否则 `None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_venues.py
import venues

CANON = [{"id": 1, "canonical_name": "万体汇", "lat": 31.17, "lng": 121.44,
          "name_patterns": ["徐家汇体育公园", "万体汇"], "platform_ids": ["flashx:12203"]}]

def test_match_by_name():
    assert venues.classify("徐家汇体育公园综合馆", "zhongyulian:x", CANON)["id"] == 1

def test_match_by_platform_id():
    assert venues.classify("随便什么名", "flashx:12203", CANON)["id"] == 1

def test_no_match():
    assert venues.classify("别的馆", "flashx:999", CANON) is None

def test_platform_id_takes_priority():
    # 名字不含任何 pattern，但 platform_id 命中
    assert venues.classify("xyz", "flashx:12203", CANON)["canonical_name"] == "万体汇"
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 写 venues.py**

```python
# venues.py
def classify(venue_name, venue_key, canonical_list):
    for c in canonical_list:
        if venue_key in (c.get("platform_ids") or []):
            return c
    for c in canonical_list:
        name = venue_name or ""
        if any(p and p in name for p in (c.get("name_patterns") or [])):
            return c
    return None
```

- [ ] **Step 4: 运行确认通过** → PASS

- [ ] **Step 5: 提交**

```bash
git add venues.py tests/test_venues.py
git commit -m "feat: 场馆按规范表归类（platform_id 优先、名字包含兜底）"
```

---

## Task 5: 高德通勤（坐标/缓存键纯逻辑 + 网络封装）

**Files:**
- Create: `commute.py`, `tests/test_commute.py`

**Interfaces:**
- Produces:
  - `commute.amap_coord(lat, lng) -> str`：返回 `"lng,lat"`。
  - `commute.place_key(canonical_id, venue_key) -> str`：有 canonical_id → `f"c:{id}"`，否则 `f"v:{venue_key}"`。
  - `commute.sec_to_min(sec) -> int|None`。
  - `commute.geocode(address: str) -> tuple[float,float]|None`（网络，失败返回 None）。
  - `commute.route_minutes(origin_ll, dest_ll) -> dict`：`{"drive_min": int|None, "transit_min": int|None}`（任一失败置 None）。
  - key 从 `os.environ["GAO_DE_MAP_API_KEY"]` 读。

- [ ] **Step 1: 写失败测试（纯逻辑 + 网络失败容错）**

```python
# tests/test_commute.py
import commute

def test_amap_coord_order():
    # 高德要 lng,lat
    assert commute.amap_coord(31.17, 121.44) == "121.44,31.17"

def test_place_key():
    assert commute.place_key(5, "flashx:1") == "c:5"
    assert commute.place_key(None, "flashx:1") == "v:flashx:1"

def test_sec_to_min():
    assert commute.sec_to_min("1800") == 30
    assert commute.sec_to_min(None) is None
    assert commute.sec_to_min("") is None

# Review Focus: 路径规划失败不抛、置 None（用假 fetcher 注入）
def test_route_minutes_failure(monkeypatch):
    monkeypatch.setattr(commute, "_amap_get", lambda url, params: None)
    r = commute.route_minutes("121,31", "121.5,31.2")
    assert r == {"drive_min": None, "transit_min": None}

def test_route_minutes_parses(monkeypatch):
    def fake(url, params):
        if "driving" in url:
            return {"status": "1", "route": {"paths": [{"duration": "1200"}]}}
        return {"status": "1", "route": {"transits": [{"duration": "1800"}]}}
    monkeypatch.setattr(commute, "_amap_get", fake)
    assert commute.route_minutes("121,31", "121.5,31.2") == {"drive_min": 20, "transit_min": 30}
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 写 commute.py**

```python
# commute.py
import os, time, json, urllib.parse, urllib.request

BASE = "https://restapi.amap.com"
CITY = "上海"

def _key():
    return os.environ["GAO_DE_MAP_API_KEY"]

def amap_coord(lat, lng):
    return f"{lng},{lat}"

def place_key(canonical_id, venue_key):
    return f"c:{canonical_id}" if canonical_id else f"v:{venue_key}"

def sec_to_min(sec):
    try:
        return round(int(sec) / 60)
    except (ValueError, TypeError):
        return None

def _amap_get(url, params):
    params = {**params, "key": _key()}
    q = urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=10) as r:
            data = json.loads(r.read().decode())
        return data if data.get("status") == "1" else None
    except Exception:
        return None

def geocode(address):
    d = _amap_get(f"{BASE}/v3/geocode/geo", {"address": address, "city": CITY})
    if not d or not d.get("geocodes"):
        return None
    lng, lat = d["geocodes"][0]["location"].split(",")
    return float(lat), float(lng)

def route_minutes(origin_ll, dest_ll):
    drive = _amap_get(f"{BASE}/v3/direction/driving",
                      {"origin": origin_ll, "destination": dest_ll})
    transit = _amap_get(f"{BASE}/v3/direction/transit/integrated",
                        {"origin": origin_ll, "destination": dest_ll,
                         "city": CITY, "cityd": CITY})
    dm = sec_to_min(drive["route"]["paths"][0]["duration"]) if drive and drive.get("route", {}).get("paths") else None
    tm = sec_to_min(transit["route"]["transits"][0]["duration"]) if transit and transit.get("route", {}).get("transits") else None
    time.sleep(0.3)  # 节流，避开 QPS
    return {"drive_min": dm, "transit_min": tm}
```

- [ ] **Step 4: 运行确认通过** → PASS

- [ ] **Step 5: 提交**

```bash
git add commute.py tests/test_commute.py
git commit -m "feat: 高德通勤封装（坐标顺序/缓存键/失败不阻塞）"
```

---

## Task 6: 刷新编排 sync.py

**Files:**
- Create: `platforms/zhongyulian.py`(补 `fetch`)、`platforms/flashx.py`(补 `fetch`)、`sync.py`、`tests/test_sync.py`

**Interfaces:**
- Consumes: Task2/3 的 `parse_games`、Task4 `classify`、Task5 `geocode/route_minutes/place_key/amap_coord`、Task1 db。
- Produces:
  - `zhongyulian.fetch(creds, day) -> str`、`flashx.fetch(creds, date_str, page) -> str`（返回响应文本；网络，手测）。
  - `sync.upsert_games(conn, games)`：`INSERT OR REPLACE` 入库 + 删 `date < 今天`。
  - `sync.enrich_venue(conn, game, canonical_list, origins_ll)`：归类 → 补坐标 → 补通勤（命中缓存则跳过）。
  - `sync.refresh(conn, creds, config) -> dict`：全流程，返回 `{"ok": bool, "count": int, "auth_failed": [平台]}`。

- [ ] **Step 1: 写失败测试（入库纯逻辑）**

```python
# tests/test_sync.py
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
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现**

`sync.upsert_games`：

```python
# sync.py
import json
from datetime import date
import venues, commute

_COLS = ("source","source_id","title","date","start_time","end_time","duration_min",
         "venue_name","venue_key","canonical_id","price","applied_num","total_num",
         "lat","lng","raw_text","tags","organizer","club_name","detail_ref")

def upsert_games(conn, games):
    q = (f"INSERT OR REPLACE INTO games({','.join(_COLS)}) "
         f"VALUES ({','.join('?'*len(_COLS))})")
    conn.executemany(q, [tuple(g.get(c) for c in _COLS) for g in games])
    conn.execute("DELETE FROM games WHERE date < ?", (date.today().isoformat(),))
    conn.commit()
```

`enrich_venue` 与 `refresh`（网络部分，手测验证）：

```python
def enrich_venue(conn, game, canonical_list, origins_ll):
    c = venues.classify(game["venue_name"], game["venue_key"], canonical_list)
    if c:
        game["canonical_id"] = c["id"]; game["lat"], game["lng"] = c["lat"], c["lng"]
    elif game["lat"] is None:  # 中羽联未归类，需地理编码
        row = conn.execute("SELECT lat,lng FROM venues WHERE venue_key=?",
                           (game["venue_key"],)).fetchone()
        if row and row["lat"] is not None:
            game["lat"], game["lng"] = row["lat"], row["lng"]
        else:
            ll = commute.geocode(game["venue_name"] or "")
            if ll:
                game["lat"], game["lng"] = ll
                conn.execute("INSERT OR REPLACE INTO venues(venue_key,name,lat,lng,geocoded_at)"
                             " VALUES(?,?,?,?,datetime('now'))",
                             (game["venue_key"], game["venue_name"], ll[0], ll[1]))
    if game["lat"] is None:
        return  # 坐标仍缺，通勤跳过
    pk = commute.place_key(game.get("canonical_id"), game["venue_key"])
    dest = commute.amap_coord(game["lat"], game["lng"])
    for origin, oll in origins_ll.items():
        if conn.execute("SELECT 1 FROM commute WHERE origin=? AND place_key=?",
                        (origin, pk)).fetchone():
            continue
        m = commute.route_minutes(oll, dest)
        conn.execute("INSERT OR REPLACE INTO commute(origin,place_key,drive_min,transit_min,computed_at)"
                     " VALUES(?,?,?,?,datetime('now'))",
                     (origin, pk, m["drive_min"], m["transit_min"]))
    conn.commit()
```

`refresh` 串起：从 db `origins` 表读两个出发地的坐标（`origins_ll={"home":"lng,lat","company":"lng,lat"}`，缺坐标的出发地跳过其通勤，不报错）→ 对两平台 fetch+parse（捕获鉴权失败）→ upsert → 逐条 enrich。地址的地理编码与「地址变则清 commute」发生在界面保存地址时（Task 8 `/api/origins`），refresh 只读 origins 表现成坐标。鉴权失败平台计入 `auth_failed`，不中断另一平台。各平台 `fetch` 用 Task 7 的 creds 组装请求头（中羽联 POST JSON body 带 `skey`、`day`、`city:"上海"`、`cmd:"indexList"`、`limit/skip` 翻页 other 列表；闪动 GET 带 `x-access-token` 头、`date`/`pageNo` 翻页）。接口字段与请求头照 `RESEARCH.md`。

- [ ] **Step 4: 运行确认通过** → `uv run pytest tests/test_sync.py -v` → PASS

- [ ] **Step 5: 提交**

```bash
git add sync.py platforms/zhongyulian.py platforms/flashx.py tests/test_sync.py
git commit -m "feat: 刷新编排（入库去重/清旧、场馆补坐标通勤）"
```

---

## Task 7: 凭证 creds.py

**Files:**
- Create: `creds.py`, `tests/test_creds.py`

**Interfaces:**
- Produces:
  - `creds.load() -> dict`、`creds.save(data)`（写 `creds.json`，`os.chmod(path, 0o600)`）。
  - `creds.is_auth_failure(source, resp_text, status_code) -> bool`：中羽联 `source='zhongyulian'` 且 JSON `ok==-2` → True；闪动 `status_code==401` 或 JSON `code in (401,403)` → True；否则 False。非 JSON（中羽联城市 Fatal error）→ False（不是鉴权问题）。
  - `creds.capture(timeout=90) -> dict`：拉起 `mitmdump --mode local:WeChat` 子进程 + addon，抓到两平台凭证写入后停；手测。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_creds.py
import json, creds

def test_zyl_auth_failure():
    assert creds.is_auth_failure("zhongyulian", json.dumps({"ok": -2, "error": "x"}), 200)

def test_zyl_ok_not_failure():
    assert not creds.is_auth_failure("zhongyulian", json.dumps({"ok": 1}), 200)

# Review Focus: 城市为空的非 JSON Fatal error 不是鉴权失败
def test_zyl_fatal_html_not_auth():
    assert not creds.is_auth_failure("zhongyulian", "<b>Fatal error</b>", 200)

def test_flashx_401():
    assert creds.is_auth_failure("flashx", "", 401)
    assert creds.is_auth_failure("flashx", json.dumps({"code": 403}), 200)

def test_flashx_ok():
    assert not creds.is_auth_failure("flashx", json.dumps({"success": True, "code": 200}), 200)

def test_save_sets_600(tmp_path, monkeypatch):
    p = tmp_path / "creds.json"; monkeypatch.setattr(creds, "PATH", str(p))
    creds.save({"a": 1})
    assert oct(p.stat().st_mode)[-3:] == "600"
    assert creds.load() == {"a": 1}
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 写 creds.py**

```python
# creds.py
import json, os

PATH = "creds.json"

def load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}

def save(data):
    with open(PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(PATH, 0o600)

def is_auth_failure(source, resp_text, status_code):
    if source == "flashx" and status_code == 401:
        return True
    try:
        d = json.loads(resp_text)
    except (ValueError, TypeError):
        return False
    if source == "zhongyulian":
        return d.get("ok") == -2
    if source == "flashx":
        return d.get("code") in (401, 403)
    return False
```

`capture()`（手测）：写一个 mitmproxy addon（临时文件）只从 `service2.zhongyulian.com`/`flash.flashxsport.com` 的请求头/body 提取 `skey` 和 `x-access-token` + 必要头，`subprocess.Popen(["mitmdump","--mode","local:WeChat","-s",addon])`，轮询 `creds.json` 直到两平台都有或超时，然后 `terminate()`。首次调用前检查 `shutil.which("mitmdump")`，缺则抛带中文提示的异常。

- [ ] **Step 4: 运行确认通过** → PASS

- [ ] **Step 5: 提交**

```bash
git add creds.py tests/test_creds.py
git commit -m "feat: 凭证读写与失效检测（区分鉴权失败与城市Fatal error）"
```

---

## Task 8: Flask 路由 app.py

**Files:**
- Create: `app.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: db、sync、creds、config。
- Produces: Flask app。路由：
  - `GET /` → `static/index.html`。
  - `GET /api/games` → 当前库全部球局，JSON `{"games": [...]}`。每条 `LEFT JOIN canonical_venues` 带出 `canonical_name`（未归类为 null），并按 `place_key(canonical_id, venue_key)` 关联 `commute` 表组装 `commute.home`/`commute.company`。
  - `GET /api/config` → 前端需要的配置（关键词预设、规范场馆名列表）。
  - `GET /api/origins` → 当前两个出发地 `{"home": {"address","has_coord"}, "company": {...}}`，供界面回填。
  - `POST /api/origins` → body `{"home": "地址文字", "company": "地址文字"}`：对每个**有变化**的地址调 `commute.geocode` 得坐标、写 `origins` 表、清掉该出发地的 `commute` 行（坐标变了）。地理编码失败 → 该出发地返回 `{"ok": false, "msg": "地址查不到，填具体些"}`，不写坏坐标。返回每个出发地的结果。
  - `POST /api/refresh` → 调 `sync.refresh`，返回 `{"ok", "count", "auth_failed"}`。
  - `POST /api/creds/capture` → 调 `creds.capture`，返回结果。
  - `app.create_app(conn, config)` 工厂，便于测试注入内存库。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_app.py
import db, app as appmod

def _app():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO games(source,source_id,title,date,venue_key,canonical_id)"
                 " VALUES('flashx','1','t','2999-01-01','flashx:1',NULL)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min)"
                 " VALUES('home','v:flashx:1',20,35)")
    conn.commit()
    return appmod.create_app(conn, {"keywords": {"include": [], "exclude": []}}).test_client()

def test_games_endpoint_attaches_commute():
    g = _app().get("/api/games").get_json()["games"][0]
    assert g["source_id"] == "1"
    assert g["commute"]["home"]["drive_min"] == 20
    assert g["commute"]["home"]["transit_min"] == 35

def test_save_origins_geocodes_and_clears_commute(monkeypatch):
    import commute
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','旧址',31.0,121.0)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min) VALUES('home','v:x',20,30)")
    conn.commit()
    monkeypatch.setattr(commute, "geocode", lambda a: (31.2, 121.5))
    client = appmod.create_app(conn, {}).test_client()
    r = client.post("/api/origins", json={"home": "新址", "company": ""}).get_json()
    row = conn.execute("SELECT address,lat,lng FROM origins WHERE key='home'").fetchone()
    assert row["address"] == "新址" and row["lat"] == 31.2
    # 地址变了 → 该出发地的 commute 被清
    assert conn.execute("SELECT COUNT(*) c FROM commute WHERE origin='home'").fetchone()["c"] == 0

def test_save_origins_geocode_fail_keeps_old(monkeypatch):
    import commute
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,address,lat,lng) VALUES('home','旧址',31.0,121.0)")
    conn.commit()
    monkeypatch.setattr(commute, "geocode", lambda a: None)
    client = appmod.create_app(conn, {}).test_client()
    r = client.post("/api/origins", json={"home": "乱填查不到", "company": ""}).get_json()
    assert conn.execute("SELECT lat FROM origins WHERE key='home'").fetchone()["lat"] == 31.0  # 不覆盖
    assert r["home"]["ok"] is False
```

- [ ] **Step 2: 运行确认失败** → FAIL

- [ ] **Step 3: 实现 app.py**

`create_app(conn, config)`：注册路由。`/api/games` 用 `SELECT games.*, canonical_venues.canonical_name FROM games LEFT JOIN canonical_venues ON games.canonical_id=canonical_venues.id`，每条再按 `place_key(canonical_id, venue_key)` 查 `commute` 的 home/company 两行，组装 `g["canonical_name"]` 与 `g["commute"]={"home":{...},"company":{...}}`。`/api/origins` 读写 `origins` 表；`POST` 时对地址有变化的出发地调 `commute.geocode`，成功则更新坐标并 `DELETE FROM commute WHERE origin=?` 清该出发地缓存，失败则该出发地返回错误、不动旧值。`GET /` 用 `send_from_directory("static","index.html")`。顶层 `app.py` main：读 `config.json`、连 `games.db`、`init_schema`、`threading.Timer` 开浏览器 `webbrowser.open`、`app.run(port=config["port"])`。

- [ ] **Step 4: 运行确认通过** → PASS

- [ ] **Step 5: 提交**

```bash
git add app.py tests/test_app.py
git commit -m "feat: Flask 路由（games 带通勤、refresh、凭证抓取）"
```

---

## Task 9: 前端筛选逻辑 filters.js

**Files:**
- Create: `static/filters.js`, `tests/filters.test.js`

**Interfaces:**
- Produces（挂到 `globalThis`/`module.exports` 双导出，供浏览器与 node 共用）：
  - `applyFilters(games, criteria) -> games[]`
  - `sortGames(games, by, origin, mode) -> games[]`
  - `criteria` 形状：`{date, startMin, startMax, durMin, origin, mode, commuteMax, includeUnknownCommute, venueInclude:[], venueExclude:[], priceMax, appliedRange:[min,max], totalRange:[min,max], onlyOpen:bool, kwInclude:[], kwExclude:[]}`。

- [ ] **Step 1: 写失败测试（node 跑）**

```javascript
// tests/filters.test.js
const assert = require("assert");
const { applyFilters, sortGames } = require("../static/filters.js");

const g = (o) => Object.assign({
  date: "2026-10-11", start_time: "19:00", duration_min: 120,
  venue_name: "万体汇", raw_text: "周日对抗局", price: 30,
  applied_num: 6, total_num: 12,
  commute: { home: { drive_min: 20, transit_min: 35 }, company: { drive_min: 50, transit_min: 60 } },
}, o);

// 关键字双向
assert.equal(applyFilters([g({ raw_text: "练球场" }), g({ raw_text: "对抗局" })],
  { kwInclude: ["练球"] }).length, 1);
assert.equal(applyFilters([g({ raw_text: "教练陪练" }), g({ raw_text: "双打" })],
  { kwExclude: ["教练"] }).length, 1);

// 场馆双向
assert.equal(applyFilters([g({ venue_name: "万体汇" }), g({ venue_name: "荫荫" })],
  { venueInclude: ["万体汇"] }).length, 1);

// 通勤上限（当前出发地+方式）
assert.equal(applyFilters([g()], { origin: "home", mode: "drive_min", commuteMax: 10 }).length, 0);
assert.equal(applyFilters([g()], { origin: "home", mode: "drive_min", commuteMax: 30 }).length, 1);

// 通勤未知保留/隐藏
const unk = g({ commute: { home: { drive_min: null, transit_min: null }, company: {} } });
assert.equal(applyFilters([unk], { origin: "home", mode: "drive_min", commuteMax: 30, includeUnknownCommute: true }).length, 1);
assert.equal(applyFilters([unk], { origin: "home", mode: "drive_min", commuteMax: 30, includeUnknownCommute: false }).length, 0);

// 还有空位
assert.equal(applyFilters([g({ applied_num: 12, total_num: 12 }), g({ applied_num: 6, total_num: 12 })],
  { onlyOpen: true }).length, 1);

// 排序：通勤升序
const s = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { home: { drive_min: 5 } } })],
  "commute", "home", "drive_min");
assert.equal(s[0].source_id, "b");

console.log("filters.js OK");
```

- [ ] **Step 2: 运行确认失败** → `node tests/filters.test.js` → 报错（找不到模块/函数）

- [ ] **Step 3: 写 static/filters.js**

实现 `applyFilters`（逐条过每个条件，空条件跳过）与 `sortGames`。关键字/场馆用 `Array.some(s => text.includes(s))`；通勤取 `g.commute[origin]?.[mode]`，为 null 时按 `includeUnknownCommute` 决定；`onlyOpen` 即 `applied_num < total_num`。文件尾部：
```javascript
if (typeof module !== "undefined") module.exports = { applyFilters, sortGames };
```

- [ ] **Step 4: 运行确认通过** → `node tests/filters.test.js` → 打印 `filters.js OK`

- [ ] **Step 5: 提交**

```bash
git add static/filters.js tests/filters.test.js
git commit -m "feat: 前端筛选与排序纯函数（node 自测）"
```

---

## Task 10: 前端页面 index.html

**Files:**
- Create: `static/index.html`

**Interfaces:**
- Consumes: `/api/games`、`/api/config`、`/api/origins`、`/api/refresh`、`/api/creds/capture`、`filters.js`。

- [ ] **Step 1: 搭页面骨架**

单 HTML：顶栏（出发地 家/公司 单选、「设置地址」入口、刷新按钮、凭证状态条）、左栏筛选表单（日期/时间范围/时长/通勤方式+上限/价格/人数/还有空位/场馆 只看·不看/关键字 只找·排除）、右栏列表 `<table>`。「设置地址」展开两个输入框（家、公司地址）+ 保存按钮。引 `filters.js`。

- [ ] **Step 2: 数据流 + 地址设置**

页面载入 `GET /api/config`（填关键词预设）+ `GET /api/origins`（回填家/公司地址；两者皆空或缺坐标 → 提示「请先在『设置地址』填家和公司地址」）+ `GET /api/games` 存全量到 `window._games`。「设置地址」保存 → `POST /api/origins` → 某出发地地理编码失败则就地提示「地址查不到，填具体些」；成功后重拉 `/api/games`（通勤已变）。任何筛选控件 `input/change` → 读表单拼 `criteria` → `applyFilters` + `sortGames` → 重渲染 `<tbody>`。切出发地/排序同理。零往返。

- [ ] **Step 3: 列表与详情**

每行：时间（date + start~end）、时长、场馆双名（`venue_name` +（`canonical_name` 若有，来自 games 关联 — 由 /api/games 带出 canonical_name））、当前出发地通勤（drive/transit）、价格、`applied/total`、来源标（中羽联/闪动）、tags。点行展开详情：`raw_text`、组织者、俱乐部、回原平台链接（闪动 `detail_ref`→小程序页；中羽联同理，无链接则显示 id 供手动查）。

- [ ] **Step 4: 刷新与凭证**

刷新按钮 → `POST /api/refresh` → 成功重拉 `/api/games`；返回 `auth_failed` 非空 → 顶栏变红、显示「抓取凭证」按钮。点它 → `POST /api/creds/capture` + 文案提示「打开对应小程序刷一下；若抓不到先关 VPN/sing-box」；完成后提示重新刷新。

- [ ] **Step 5: 手动验证 + 提交**

手测：`uv run app.py` → 浏览器自动打开 → 在「设置地址」填家/公司地址（验证地理编码成功、以及乱填时的「查不到」提示）→ 刷新拉到两平台球局 → 各筛选项逐个验证 → 切出发地通勤变化 → 点详情 → 制造凭证失效走一遍抓取。通过后：

```bash
git add static/index.html
git commit -m "feat: 前端单页（筛选/排序/详情/刷新/凭证抓取）"
```

---

## 收尾

- [ ] 全量 pytest + node 测试通过：`uv run pytest -q && node tests/filters.test.js`
- [ ] `.gitignore` 确认挡住 `creds.json`/`games.db`/`*.mitm`（Task 建仓时已加）
- [ ] 复制 `config.example.json` → `config.json`，填真实出发地与规范场馆，端到端跑一次
