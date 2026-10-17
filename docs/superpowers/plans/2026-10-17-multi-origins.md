# 多出发地 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 出发地从固定的「家」「公司」变成可增删改名的列表；定位方式沿用球馆管理页的高德搜索选点，选点区抽成两页共用的组件。

**Architecture:** 后端 `origins` 表加 `name/show/pos`，`/api/origins` 改为收发完整列表（坐标由前端给，后端只校验）；`/api/games` 的 `commute` 按 `origins` 表的 key 生成。前端 `filters.js` 的通勤筛选和排序改为按 key 通用；`common.js` 新增 `locPicker` 组件，球馆管理页改用它；主页按出发地列表动态生成上限框、表格列和详情。

**Tech Stack:** Python 3.12 + Flask + sqlite3，原生 HTML + ES Modules，pytest，`node tests/filters.test.mjs`。

**Spec:** `docs/superpowers/specs/2026-10-17-multi-origins-design.md`

## Global Constraints

- 一律中文：代码注释、commit message、界面文案。commit 结尾加 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。
- 不写迁移代码、不兼容旧筛选字段 `homeCommuteMax` / `companyCommuteMax`。
- 手动坐标一律是高德坐标（GCJ-02），只收高德坐标或百度地图网页链接（`parseCoord` 换算），后端用 `venues.in_shanghai` 校验。
- 新出发地 key：`"o" + uuid4().hex[:8]`，不复用。
- 已有 key `home`、`company` 保留，通勤缓存不重算。
- 算通勤进行中（`compute_lock` 被占），`POST /api/origins` 含删除或改位置 → 整个请求 409，文案「正在算通勤，等算完再删除或改位置」。
- 测试：`uv run pytest -q`、`node tests/filters.test.mjs` 全绿。
- 浏览器验证用 scratchpad 的 `run.py` 在 8799 端口起实例（数据库 `$S/test.db`），不碰用户 8765 的服务；cdp 前先 `--eval "window.confirm = () => true"`。

## Review Focus

1. **保存时部分条目失败**：一条名称为空、其他条正常 → 正常的照常保存，失败那条的输入（名称、选好的位置）留在面板里不丢。→ Task 4 Step 6 浏览器验证。
2. **删除出发地后筛选条件里残留它的上限**：重建上限框后立即存一次筛选条件，残留 key 被丢掉；恢复筛选时忽略不存在的 key。→ Task 2 测试（不存在的 key 的上限不影响结果）+ Task 4 Step 6。
3. **当前排序列被隐藏或删除**：回到按开始时间排序，不报错。→ Task 4 Step 6。
4. **管理页 tbody 的点击委托误接组件里的点击**：高德候选的 `div[data-poi]` 不能再被管理页旧的 `.pick > div[data-poi]` 分支处理。→ Task 3 Step 5 浏览器回归。
5. **出发地全部没坐标或列表为空**：提示条出现，表格没有通勤列、详情里没有通勤行，页面不报错。→ Task 4 Step 6。

---

### Task 1: 后端：origins 表、`/api/origins`、`/api/games`

**Files:**
- Modify: `db.py`（origins 建表语句）
- Modify: `app.py:1-7`（import、删 `ORIGINS`）、`app.py:29-48`（`/api/games`）、`app.py:203-232`（`/api/origins`）
- Modify: `commute.py:48-56`（删 `geocode`）
- Test: `tests/test_app.py`（改 `_app`，替换 130-181 行的出发地测试，改 345 行附近的断言）、`tests/test_commute.py:43-47`（删 geocode 测试）

**Interfaces:**
- Produces:
  - `GET /api/origins` → `[{"key", "name", "address", "lat", "lng", "show": bool}]`，按 `pos, rowid` 排序。
  - `POST /api/origins` body `{"origins": [{"key"?, "name", "show", "loc"?: {"address", "lat", "lng"}}]}` → 同顺序 `[{"ok": true, "changed": bool, "key"} | {"ok": false, "msg", "key"}]`；body 结构不对 400；算通勤中含删除/改位置 409。
  - `/api/games` 每条 `g["commute"] = {key: {...} | None}`，key 取自 `origins` 表全部行。

- [ ] **Step 1: 改测试**

`tests/test_app.py` 的 `_app()` 插入两个出发地，让 `commute` 里有 `home`、`company` 两个 key：

```python
def _app():
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,name,lat,lng,pos) VALUES('home','家',31.0,121.0,0),('company','公司',31.1,121.1,1)")
    conn.execute("INSERT INTO games(source,source_id,title,date,venue_key,platform_name_norm)"
                 " VALUES('flashx','1','t','2999-01-01','flashx:1','某馆')")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min)"
                 " VALUES('home',?,20,35)", (commute.place_key(None, "flashx:1"),))
    conn.commit()
    return appmod.create_app(conn, {}).test_client()
```

`test_games_endpoint_joins_alias_for_amap_name_and_commute` 在 `conn.commit()` 前加一行：

```python
    conn.execute("INSERT INTO origins(key,name) VALUES('home','家')")
```

`test_games_zyl_unclassified_has_no_commute_even_with_stale_cache` 在 `conn.commit()` 前加：

```python
    conn.execute("INSERT INTO origins(key,name,pos) VALUES('home','家',0),('company','公司',1)")
```

（末尾断言 `g["commute"] == {"home": None, "company": None}` 不变。）

删掉 `test_save_origins_geocodes_and_clears_commute`、`test_save_origins_geocode_fail_keeps_old`、`test_get_origins_and_config`、`test_save_origins_unchanged_keeps_commute`、`test_save_origins_non_dict_body` 五个测试，换成：

```python
def _origins_app(computing=False):
    # 两个已有出发地 + 各自一条通勤缓存
    conn = db.connect(":memory:"); db.init_schema(conn)
    conn.execute("INSERT INTO origins(key,name,address,lat,lng,show,pos) VALUES"
                 "('home','家','旧址',31.0,121.0,1,0),('company','公司','司',31.1,121.1,1,1)")
    conn.execute("INSERT INTO commute(origin,place_key,drive_min,transit_min) VALUES('home','v:x',20,30),('company','v:x',10,15)")
    conn.commit()
    app = appmod.create_app(conn, {})
    return conn, app.test_client()

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
```

`tests/test_commute.py` 删掉 `test_geocode_bad_location`（43-47 行，连同上面那行注释）。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest -q tests/test_app.py`
Expected: FAIL（`origins` 没有 `name` 列 / 接口返回格式不对）

- [ ] **Step 3: 实现**

`db.py` 的 origins 建表改成：

```sql
    CREATE TABLE IF NOT EXISTS origins(
      key TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', address TEXT, lat REAL, lng REAL,
      show INTEGER NOT NULL DEFAULT 1,   -- 1 = 在主页表格里显示成列
      pos INTEGER NOT NULL DEFAULT 0);   -- 排列顺序
```

`app.py`：第 1 行 import 加 `uuid`（`import json, sqlite3, threading, time, uuid, webbrowser`），删掉 `ORIGINS = ("home", "company")`。

`/api/games` 里，`cm` 循环之后加一行取 key，并替换 `g["commute"]` 那行：

```python
        keys = [r["key"] for r in conn.execute("SELECT key FROM origins")]
```

```python
            g["commute"] = {o: cm.get((o, pk)) for o in keys}
```

`get_origins` / `save_origins` 整段替换为：

```python
    @app.get("/api/origins")
    def get_origins():
        return jsonify([{"key": r["key"], "name": r["name"], "address": r["address"] or "",
                         "lat": r["lat"], "lng": r["lng"], "show": bool(r["show"])}
                        for r in conn.execute("SELECT * FROM origins ORDER BY pos, rowid")])

    def _origin_err(x):
        # 单条出发地的校验错误；没问题返回 None
        name, loc = x.get("name"), x.get("loc")
        if not isinstance(name, str) or not name.strip():
            return "名称不能为空"
        if loc is None:
            return None if x.get("key") else "先选位置"
        if not (isinstance(loc, dict) and isinstance(loc.get("address"), str)
                and _is_num(loc.get("lat")) and _is_num(loc.get("lng"))):
            return "位置格式不对"
        if not venues.in_shanghai(loc["lat"], loc["lng"]):
            return "坐标不在上海范围内，检查一下是不是复制错了"
        return None

    @app.post("/api/origins")
    def save_origins():
        # 收完整列表：有 key 的更新、没 key 的新增、库里有但列表里没有的删除。
        # 坐标由前端给（高德 POI 或换算好的手动坐标），这里只校验；单条失败不影响其他条
        body = request.get_json(silent=True)
        items = body.get("origins") if isinstance(body, dict) else None
        if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
            return jsonify({"ok": False, "msg": "body 须为 {origins: [...]}"}), 400
        old = {r["key"] for r in conn.execute("SELECT key FROM origins")}
        gone = old - {x.get("key") for x in items}
        # 后台算通勤按开始时的坐标快照写回，期间删除/改位置会留下孤儿行或旧位置的通勤
        if (gone or any(x.get("loc") is not None for x in items)) and compute_lock.locked():
            return jsonify({"ok": False, "msg": "正在算通勤，等算完再删除或改位置"}), 409
        for k in gone:
            conn.execute("DELETE FROM origins WHERE key=?", (k,))
            conn.execute("DELETE FROM commute WHERE origin=?", (k,))
        pos = conn.execute("SELECT COALESCE(MAX(pos), -1) FROM origins").fetchone()[0]
        res = []
        for x in items:
            key, loc = x.get("key"), x.get("loc")
            err = "没有这个出发地" if key is not None and key not in old else _origin_err(x)
            if err:
                res.append({"ok": False, "msg": err, "key": key})
                continue
            name, show = x["name"].strip(), 1 if x.get("show", True) else 0
            if key is None:  # 新增：key 不复用，免得筛选条件里旧上限套到新出发地上
                key, pos = "o" + uuid.uuid4().hex[:8], pos + 1
                conn.execute("INSERT INTO origins(key,name,address,lat,lng,show,pos) VALUES(?,?,?,?,?,?,?)",
                             (key, name, loc["address"], loc["lat"], loc["lng"], show, pos))
            else:
                conn.execute("UPDATE origins SET name=?, show=? WHERE key=?", (name, show, key))
                if loc is not None:
                    conn.execute("UPDATE origins SET address=?, lat=?, lng=? WHERE key=?",
                                 (loc["address"], loc["lat"], loc["lng"], key))
                    conn.execute("DELETE FROM commute WHERE origin=?", (key,))  # 坐标变了，缓存作废
            res.append({"ok": True, "changed": loc is not None, "key": key})
        conn.commit()
        return jsonify(res)  # 通勤由前端在位置改动后调 /api/commute 触发
```

注意 `_is_num` 定义在 `/api/venues/assign` 前面（`app.py:113`），比 `save_origins` 早，闭包里能直接用。

`commute.py` 删掉整个 `geocode` 函数（48-56 行）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest -q`
Expected: 全部 PASS（`tests/test_sync.py` 里 `INSERT INTO origins(key,address,lat,lng)` 靠 `name` 的默认值照常能插）

- [ ] **Step 5: Commit**

```bash
git add db.py app.py commute.py tests/test_app.py tests/test_commute.py
git commit -m "feat: 出发地改为可增删改名的列表，坐标由前端选点给出

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `filters.js`：按 key 的通勤上限和排序

**Files:**
- Modify: `static/filters.js:46-53`（通勤上限）、`static/filters.js:81-82`（sortKey）
- Test: `tests/filters.test.mjs:33-43`、`tests/filters.test.mjs:61-68`

**Interfaces:**
- Produces:
  - criteria 字段 `commuteMax: {key: number}`（只含填了数的），取代 `homeCommuteMax` / `companyCommuteMax`。
  - 排序键 `"commute:<key>"`，取代 `"commute_home"` / `"commute_company"`。

- [ ] **Step 1: 改测试**

`tests/filters.test.mjs` 33-43 行（从「// 通勤上限：家/公司各自一个」到「通勤未知保留/隐藏」两条断言）替换为：

```js
// 通勤上限：每个出发地各自一个，按当前方式（家驾20、公司驾50）
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { home: 10 } }).length, 0);
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { home: 30 } }).length, 1);
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { company: 40 } }).length, 0);
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { company: 60 } }).length, 1);
// 多个出发地同时设，都要满足（并且）
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { home: 30, company: 40 } }).length, 0);
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { home: 30, company: 60 } }).length, 1);
// 球局没有该出发地的通勤（新加的出发地还没算）= 未知，受「含通勤未知」控制
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { o1234abcd: 30 }, includeUnknownCommute: false }).length, 0);
assert.equal(applyFilters([g()], { mode: "drive_min", commuteMax: { o1234abcd: 30 }, includeUnknownCommute: true }).length, 1);

// 通勤未知保留/隐藏（按家）
const unk = g({ commute: { home: { drive_min: null, transit_min: null }, company: {} } });
assert.equal(applyFilters([unk], { mode: "drive_min", commuteMax: { home: 30 }, includeUnknownCommute: true }).length, 1);
assert.equal(applyFilters([unk], { mode: "drive_min", commuteMax: { home: 30 }, includeUnknownCommute: false }).length, 0);
```

61-68 行两段排序测试替换为：

```js
// 排序：按某个出发地的通勤升序（按驾车）；缺失排最后
const s = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { home: { drive_min: 5 } } })],
  "commute:home", "drive_min");
assert.equal(s[0].source_id, "b");
const sco = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { company: { drive_min: 10 } } })],
  "commute:company", "drive_min");
assert.equal(sco[0].source_id, "b");
const sNew = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { o1: { drive_min: 70 } } })],
  "commute:o1", "drive_min");
assert.equal(sNew[0].source_id, "b");  // a 没有 o1 的通勤，排最后
```

- [ ] **Step 2: 跑测试确认失败**

Run: `node tests/filters.test.mjs`
Expected: AssertionError（`commuteMax` 还没生效）

- [ ] **Step 3: 实现**

`static/filters.js` 通勤上限那段替换为：

```js
    // 通勤上限：每个出发地各自一个（commuteMax: {key: 分钟}），都要满足；均按当前方式 c.mode 比较
    if (c.mode) {
      for (const [o, max] of Object.entries(c.commuteMax || {})) {
        const v = g.commute?.[o]?.[c.mode];
        if (v == null) { if (!c.includeUnknownCommute) return false; }
        else if (v > max) return false;
      }
    }
```

`sortKey` 里删掉 `commute_home`、`commute_company` 两个 case，在 `switch` 前加：

```js
  if (by?.startsWith("commute:")) { const o = by.slice(8); return (g) => g.commute?.[o]?.[mode]; }  // 某个出发地的通勤
```

- [ ] **Step 4: 跑测试确认通过**

Run: `node tests/filters.test.mjs`
Expected: 输出 OK

- [ ] **Step 5: Commit**

```bash
git add static/filters.js tests/filters.test.mjs
git commit -m "feat: 通勤上限和排序按出发地 key 通用

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

（此时主页还在用旧字段，通勤上限和通勤列排序暂时失效，Task 4 接上。）

---

### Task 3: `locPicker` 组件 + 球馆管理页改用它

**Files:**
- Modify: `static/common.js`（新增 `mapLink`、`coordError`、`locPicker`）
- Modify: `static/app.css`（选点区样式，从 `venues.html` 移过来）
- Modify: `static/venues.html`（删本页的 `mapLink`、`coordError`、`searchAmap`、`.pick` 样式；`openEditor` 改用组件；tbody 点击委托去掉 `amap`/`coord`/`data-poi` 分支）

**Interfaces:**
- Consumes: `GET /api/amap/search?q=` → `{pois: [{amap_id, name, lat, lng, district, address}]}`；`parseCoord(text)` → `{lat, lng, coarse} | null`（`filters.js`）。
- Produces（`common.js` 导出）：
  - `mapLink(name, lat, lng)` → HTML 字符串（`lat == null` 时空串）。
  - `coordError(text, ll)` → 错误说明字符串，没问题返回 `""`。
  - `locPicker(el, { query = "", onPick, onInput = () => {} })` → `{ input, slot }`；`onPick(p)`：选候选时 `p` 是 POI 对象（有 `amap_id`），手动坐标时 `p = {lat, lng}`。

- [ ] **Step 1: `common.js` 加组件**

文件头注释改为 `// common.js —— 两个页面共用：DOM 取元素、请求、转义、平台名、拼音缓存、选点区`，顶部加 `import { parseCoord } from "/static/filters.js";`，文件末尾追加：

```js
// 高德地图上看这个点（新标签页打开）
export const mapLink = (name, lat, lng) => lat == null ? "" :
  ` <a href="https://uri.amap.com/marker?position=${lng},${lat}&name=${encodeURIComponent(name)}" target="_blank" rel="noopener">地图</a>`;

// 手动坐标输入的问题说明；没问题返回 ""
export function coordError(text, ll) {
  if (/j\.map\.baidu\.com/.test(text)) return "百度短链接里没有坐标：先在浏览器打开它，等页面显示出场馆后，复制地址栏的完整链接";
  if (!ll && /baidu\.com/.test(text)) return "这个百度链接没定位到场馆：等页面显示出场馆详情、地址栏里出现 uid= 后再复制";
  if (!ll) return "格式不对，应为高德坐标「经度,纬度」或百度地图链接（地址栏里带 @ 的那种）";
  if (ll.coarse) return "精度不够：小数至少 4 位（坐标拾取器要登录并认证才显示完整坐标）";
  return "";
}

// 选点区（主页出发地、球馆管理页共用）：关键字搜高德 POI 列候选；搜不到就手动填高德坐标或百度地图链接。
// onPick(p)：点候选 p = 高德 POI {amap_id,name,lat,lng,district,address}；「用这个坐标」p = {lat,lng}（已是高德坐标）
// onInput(q)：关键字框每次输入时调。返回 {input: 关键字框, slot: 搜索框与高德候选之间的空容器}
export function locPicker(el, { query = "", onPick, onInput = () => {} }) {
  el.innerHTML = `<div class="lp">
    <div class="row"><input class="lp-q" type="text"><button type="button" class="lp-go">在高德搜索</button></div>
    <div class="lp-slot"></div><div class="pick lp-res"></div>
    <div class="row"><span class="nowrap">高德搜不到？手动指定坐标：</span>
      <input class="lp-ll" type="text" placeholder="高德坐标 或 百度地图链接">
      <button type="button" class="lp-use">用这个坐标</button></div>
    <div class="muted">在 <a href="https://map.baidu.com/" target="_blank" rel="noopener">百度地图</a> 点开地点（别拖动地图），把地址栏链接整条粘进来；
      或从 <a href="https://lbs.amap.com/tools/picker" target="_blank" rel="noopener">高德坐标拾取器</a> 复制坐标（要登录认证才有完整小数）<span class="lp-prev"></span></div></div>`;
  const q = el.querySelector(".lp-q"), ll = el.querySelector(".lp-ll");
  const res = el.querySelector(".lp-res"), prev = el.querySelector(".lp-prev");
  q.value = query;
  let pois = [];
  const search = async () => {
    const kw = q.value.trim();
    if (!kw) return;
    res.innerHTML = `<div class="head">高德搜索中…</div>`;
    try {
      ({ pois } = await api(`/api/amap/search?q=${encodeURIComponent(kw)}`));
      res.innerHTML = `<div class="head">高德搜索结果（${pois.length}）</div>` + (pois.map((p, i) =>
        `<div data-poi="${i}">${esc(p.name)}<small>${esc(p.district)} ${esc(p.address)}</small>${mapLink(p.name, p.lat, p.lng)}</div>`).join("")
        || `<div class="head">高德没有搜到，换个关键字试试，或在下面手动指定坐标</div>`);
    } catch (e) { res.innerHTML = `<div class="head err">高德搜索失败：${esc(e.message)}</div>`; }
  };
  q.addEventListener("input", () => onInput(q.value.trim()));
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
  el.querySelector(".lp-go").onclick = search;
  res.onclick = (e) => {
    if (e.target.closest("a")) return;  // 地图链接照常打开
    const d = e.target.closest("[data-poi]");
    if (d) onPick(pois[Number(d.dataset.poi)]);
  };
  // 手动坐标：边输入边预览换算后的高德坐标，可点地图链接核对位置
  const check = () => { const t = ll.value, p = parseCoord(t); return [p, t.trim() ? coordError(t, p) : "先填高德坐标或百度地图链接"]; };
  ll.addEventListener("input", () => {
    const [p, err] = check();
    prev.innerHTML = !ll.value.trim() ? "" : err ? ` → <span class="err">${err}</span>`
      : ` → 高德坐标 ${p.lng},${p.lat}${mapLink("位置", p.lat, p.lng)}`;
  });
  el.querySelector(".lp-use").onclick = () => {
    const [p, err] = check();
    if (err) prev.innerHTML = ` → <span class="err">${err}</span>`;
    else onPick({ lat: p.lat, lng: p.lng });
  };
  return { input: q, slot: el.querySelector(".lp-slot") };
}
```

- [ ] **Step 2: 样式移到 `app.css`**

`static/venues.html` 的 `<style>` 里删掉 `.pick` 开头的 8 行（`.pick { border…` 到 `.pick .head:hover…`）。`static/app.css` 末尾追加：

```css
/* ---- 5. 共用控件：选点区（locPicker） ---- */
.lp {
  display: flex; flex-direction: column; gap: 8px; max-width: 760px;
  & .row { display: flex; gap: 8px; align-items: center; }
  & .row input { flex: 1; }
}
.pick {
  border: 1px solid var(--c-line); border-radius: var(--radius); background: var(--c-surface);
  &:empty { display: none; }
  & div { padding: 5px 8px; cursor: pointer; border-bottom: 1px solid var(--c-line); }
  & div:last-child { border-bottom: none; }
  & div:hover { background: var(--c-hover); }
  & small { color: var(--c-muted); margin-left: 6px; }
  & .head { cursor: default; color: var(--c-muted); font-size: var(--fs-sm); background: var(--c-surface-2); }
  & .head:hover { background: var(--c-surface-2); }
}
```

- [ ] **Step 3: 管理页改用组件**

`static/venues.html`：

import 改为：

```js
import { pyMatch, rankCandidates } from "/static/filters.js";
import { $, SRC, esc, api, PY, ensurePinyin, mapLink, locPicker } from "/static/common.js";
```

删掉本页的 `const mapLink = …`（两行）、`function coordError …`、`async function searchAmap …`。

`openEditor` 整个替换为：

```js
// ---- 编辑区：先选已有统一球场；没有合适的再搜高德或手动指定坐标 ----
function openEditor(tr) {
  document.querySelectorAll("tr.edit").forEach((e) => e.remove());
  const v = rows.find((x) => x.platform_name_norm === tr.dataset.norm);
  const norm = editing = v.platform_name_norm;
  const ed = document.createElement("tr");
  ed.className = "edit";
  ed.innerHTML = `<td colspan="6"><div class="ed"><div class="lpbox"></div>
    <div class="row"><span class="nowrap">手动坐标的名称：</span><input class="cname" type="text" value="${esc(v.name)}">
      <button data-act="close">取消</button></div></div></td>`;
  tr.after(ed);
  const showCanon = (q) => {
    const byName = new Map(canonicals.map((c) => [c.amap_name, c]));
    const hits = q ? rankCandidates(q, [...byName.keys()], PY).map((n) => byName.get(n)) : [];
    slot.innerHTML = hits.length ? `<div class="pick canon"><div class="head">已有统一球场（按名字相近程度）</div>` +
      hits.map((c) => `<div data-cid="${c.id}">${esc(c.amap_name)}${mapLink(c.amap_name, c.lat, c.lng)}</div>`).join("") + "</div>" : "";
  };
  const { input, slot } = locPicker(ed.querySelector(".lpbox"), {
    query: v.name,
    onInput: showCanon,
    onPick: (p) => {
      if (p.amap_id) return assign(norm, { poi: { amap_id: p.amap_id, name: p.name, lat: p.lat, lng: p.lng } }, p.name);
      const name = ed.querySelector(".cname").value.trim();
      if (!name) { $("status").className = "err"; $("status").textContent = "手动坐标需要名称"; return; }
      assign(norm, { coord: { name, lat: p.lat, lng: p.lng } }, name);
    },
  });
  showCanon(v.name);
  input.focus();
}
```

`.ed` 样式里的 `.ed .row`、`.ed input` 两行保留（名称那一行还在用）。

tbody 点击委托：`const pick = …` 那行改为只认已有统一球场：

```js
  const pick = e.target.closest(".canon > div[data-cid]");
```

`if (btn)` 分支里删掉 `else if (a === "amap") searchAmap(tr);` 和整个 `else if (a === "coord") { … }`。末尾删掉 `else if (pick?.dataset.poi) { … }` 分支。组件里的按钮没有 `data-act`，落进 `btn` 分支也不会命中任何动作。

- [ ] **Step 4: 跑现有测试**

Run: `uv run pytest -q && node tests/filters.test.mjs`
Expected: 全部 PASS / OK

- [ ] **Step 5: 浏览器回归（8799 实例）**

先写迁移脚本 `$S/migrate-origins.sql`（scratchpad，不入库；Task 4、Task 5 也用它）：

```sql
ALTER TABLE origins ADD COLUMN name TEXT NOT NULL DEFAULT '';
ALTER TABLE origins ADD COLUMN show INTEGER NOT NULL DEFAULT 1;
ALTER TABLE origins ADD COLUMN pos INTEGER NOT NULL DEFAULT 0;
UPDATE origins SET name='家', pos=0 WHERE key='home';
UPDATE origins SET name='公司', pos=1 WHERE key='company';
```

复制一份真实库做测试库并迁移，起 8799 实例：

```bash
S=/private/tmp/claude-501/-Users-ymy-Projects-badminton-filter/fad5d9a7-8ec5-42e0-bd9a-500fa4f7e284/scratchpad
cd /Users/ymy/Projects/badminton-filter && sqlite3 games.db ".backup $S/test.db"
sqlite3 $S/test.db < $S/migrate-origins.sql
(uv run $S/run.py > $S/run.log 2>&1 &); sleep 2
```

用 cdp 打开 `http://127.0.0.1:8799/venues`，选「全部」视图，点第一行「修改」，依次验证并截图：
1. 编辑区出现：关键字框（初值是平台馆名）、已有统一球场候选、手动坐标框、名称框、取消按钮。
2. 改关键字 → 已有统一球场候选跟着变。
3. 回车 → 出现「高德搜索结果（N）」。点地图链接不触发归类。
4. 手动坐标框输入 `121.4819,31.1976` → 预览「高德坐标 …」；输入 `121.48,31.19` → 「精度不够」；输入 `https://j.map.baidu.com/61/fojM` → 「百度短链接里没有坐标」。
5. 点一个高德候选 → 状态栏「已归到「…」」，出现撤销按钮 → 点撤销恢复。
6. 再开编辑区，点一个已有统一球场 → 归类成功 → 撤销。
7. 填合法手动坐标、清空名称框 → 点「用这个坐标」→ 「手动坐标需要名称」；填回名称 → 归类成功 → 撤销。

控制台不能有 exception。测完 `pkill -f scratchpad/run.py`。

- [ ] **Step 6: Commit**

```bash
git add static/common.js static/app.css static/venues.html
git commit -m "refactor: 选点区抽成 locPicker 组件，球馆管理页改用它

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 主页：出发地面板、动态上限框、表格列、详情

**Files:**
- Modify: `static/index.html`（样式、`#banner`/`#addr` 结构、筛选里两个上限框、表头、脚本里的 `FIELDS`/`formState`/`applyFormState`/`buildCriteria`/`rowHtml`/`applyOrigins`/`init`/表头点击/地址面板逻辑）

**Interfaces:**
- Consumes: Task 1 的 `GET/POST /api/origins`；Task 2 的 `commuteMax`、`"commute:<key>"`；Task 3 的 `locPicker`、`mapLink`。

- [ ] **Step 1: HTML 结构**

样式里把 `#addr div { margin: 4px 0; }` 和 `#addr input { width: 360px; }` 两行换成：

```css
  .orow { display: flex; gap: 8px; align-items: center; margin: 4px 0; }
  .orow .oname { width: 140px; }
  .opick { margin: 4px 0 10px; }
  .opick:empty { display: none; }
```

`#banner` 和 `#addr` 换成：

```html
<div id="banner">请先在『设置地址』填出发地地址</div>
<div id="addr">
  <div id="addrRows"></div>
  <div class="orow"><button id="addrAdd">+ 添加地址</button><span class="muted" id="addrCost"></span>
    <button id="addrSave" class="primary">保存</button><span id="addrMsg"></span></div>
</div>
```

筛选里两个上限框（`homeCommuteMax`、`companyCommuteMax` 两个 `div.f`）换成一个容器，放在「含通勤未知」前面：

```html
      <div id="cmaxBox" style="display: contents"></div>
```

表头里删掉 `家通勤`、`公司通勤` 两个 `th`（出发地列由脚本插在「俱乐部」后面）。

- [ ] **Step 2: 筛选条件与渲染**

import 改为：

```js
import { $, SRC, esc, api, PY, ensurePinyin, mapLink, locPicker } from "/static/common.js";
```

`FIELDS` 去掉 `"homeCommuteMax", "companyCommuteMax"`。

在 `const chips = {};` 上面加：

```js
let origins = [];          // 出发地列表（GET /api/origins）
const shownOrigins = () => origins.filter((o) => o.show);
// 通勤上限框：data-cmax = 出发地 key
const cmaxInputs = () => [...document.querySelectorAll("[data-cmax]")];
```

`formState` 里 `FIELDS.forEach` 之后加：

```js
  f.commuteMax = Object.fromEntries(cmaxInputs().map((el) => [el.dataset.cmax, el.value]));
```

`applyFormState` 里 `FIELDS.forEach` 之后加（不存在的 key 自然被忽略）：

```js
  cmaxInputs().forEach((el) => { el.value = f.commuteMax?.[el.dataset.cmax] ?? ""; });
```

`buildCriteria` 里 `homeCommuteMax: …, companyCommuteMax: …,` 那行换成：

```js
    commuteMax: Object.fromEntries(cmaxInputs().filter((el) => el.value !== "").map((el) => [el.dataset.cmax, Number(el.value)])),
```

`rowHtml` 改动：
- 两个通勤 `td` 换成 `${shownOrigins().map((o) => `<td class="nowrap">${fmtCommute(g.commute?.[o.key])}</td>`).join("")}`。
- 详情行 `colspan="10"` 换成 `colspan="${8 + shownOrigins().length}"`。
- 详情行「组织者：…」那行前面插入一行通勤（没有出发地时不插）：

```js
${origins.length ? "通勤：" + origins.map((o) => `${esc(o.name)} ${fmtCommute(g.commute?.[o.key])}`).join("　") + "\n" : ""}
```

`applyOrigins` 整个替换为：

```js
// 按出发地列表重建：筛选里的上限框（全部出发地）+ 表头通勤列（只「显示成列」的），保留已填的上限
function applyOrigins(list) {
  origins = list;
  const cur = Object.fromEntries(cmaxInputs().map((el) => [el.dataset.cmax, el.value]));
  $("cmaxBox").innerHTML = origins.map((o) =>
    `<div class="f"><span>${esc(o.name)}通勤上限（分钟，按当前方式）</span><input type="number" min="0" data-cmax="${esc(o.key)}"></div>`).join("");
  cmaxInputs().forEach((el) => {
    el.value = cur[el.dataset.cmax] ?? "";
    el.addEventListener("input", onFormChange);
  });
  document.querySelectorAll("th.oc").forEach((th) => th.remove());
  document.querySelector('th[data-sort="club"]').insertAdjacentHTML("afterend", shownOrigins().map((o) =>
    `<th class="s oc" data-sort="commute:${esc(o.key)}">${esc(o.name)}通勤</th>`).join(""));
  if (!document.querySelector(`th.s[data-sort="${CSS.escape(sortBy)}"]`)) { sortBy = "start_time"; sortDesc = false; }  // 排序列被删/隐藏
  $("banner").style.display = origins.some((o) => o.lat != null) ? "none" : "block";
}
```

`applyFormState` 里检查排序列那行同样改用 `CSS.escape`（key 里只有字母数字，但统一写法）：

```js
  if (document.querySelector(`th.s[data-sort="${CSS.escape(f.sortBy || "")}"]`)) { sortBy = f.sortBy; sortDesc = !!f.sortDesc; }
```

`init` 里把 `applyFormState(cfg.filters); applyOrigins(org);` 两行顺序对调为先 `applyOrigins(org);` 再 `applyFormState(cfg.filters);`（上限框和表头要先建出来，才能恢复值和排序列）。

表头点击改成委托（出发地列是动态插入的）：

```js
$("tbody").closest("table").querySelector("thead").addEventListener("click", (e) => {
  const th = e.target.closest("th.s");
  if (!th) return;
  // 点当前列切换升/降序；点别的列从升序开始
  sortDesc = th.dataset.sort === sortBy ? !sortDesc : false;
  sortBy = th.dataset.sort;
  onFormChange();
});
```

（替换原来的 `document.querySelectorAll("th.s").forEach(…)`。场馆列「?」的 `stopPropagation` 照样挡住冒泡。）

- [ ] **Step 3: 出发地面板**

删掉原来的 `$("addrSave").onclick = …` 整段，换成：

```js
// ---- 出发地面板：编辑草稿 draft，点「保存」整份提交 ----
let draft = [];  // [{key?, name, address, lat, lng, show, loc?, msg?, cls?}]；loc = 改过但未保存的位置
let picking = -1;  // 正在选位置的行（同一时间只开一个选点区）
function renderAddr() {
  $("addrRows").innerHTML = draft.map((d, i) => `<div class="orow" data-i="${i}">
      <input class="oname" type="text" value="${esc(d.name)}" placeholder="名称，如 父母家">
      <span>${d.lat == null ? '<span class="muted">未定位</span>' : esc(d.address) + mapLink(d.name || "出发地", d.lat, d.lng)}${d.loc ? ' <span class="muted">（未保存）</span>' : ""}</span>
      <button data-act="loc">改位置</button>
      <label><input class="oshow" type="checkbox"${d.show ? " checked" : ""}>显示成列</label>
      <button data-act="del" class="danger">删除</button>
      <span class="${d.cls || ""}">${esc(d.msg || "")}</span></div>
    <div class="opick"></div>`).join("");
  if (picking >= 0) openPicker(picking);
}
function openPicker(i) {
  picking = i;
  document.querySelectorAll(".opick").forEach((el) => { el.innerHTML = ""; });
  const { input } = locPicker(document.querySelectorAll(".opick")[i], {
    query: draft[i].name,
    onPick: (p) => {
      const address = p.amap_id ? `${p.name}（${p.district} ${p.address}）` : `手动坐标 ${p.lng},${p.lat}`;
      Object.assign(draft[i], { loc: { address, lat: p.lat, lng: p.lng }, address, lat: p.lat, lng: p.lng, msg: "" });
      picking = -1;
      renderAddr();
    },
  });
  input.focus();
}
$("addrRows").addEventListener("input", (e) => {  // 名称、显示成列：只改草稿，不重绘（免得丢焦点）
  const d = draft[Number(e.target.closest(".orow")?.dataset.i)];
  if (!d) return;
  if (e.target.classList.contains("oname")) d.name = e.target.value;
  if (e.target.classList.contains("oshow")) d.show = e.target.checked;
});
$("addrRows").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const i = Number(btn.closest(".orow").dataset.i);
  if (btn.dataset.act === "loc") openPicker(i);
  else if (btn.dataset.act === "del") {
    if (draft[i].key && !confirm(`删除「${draft[i].name}」会一起删掉它的通勤缓存，确定？`)) return;
    draft.splice(i, 1);
    picking = -1;
    renderAddr();
  }
});
$("addrAdd").onclick = async () => {
  draft.push({ name: "", address: "", lat: null, lng: null, show: true });
  picking = draft.length - 1;
  renderAddr();
  try { $("addrCost").textContent = `新地址第一次算通勤约 ${(await api("/api/commute/status")).commute_places * 3} 次高德调用`; }
  catch (e) { /* 只是提示，查不到不影响 */ }
};
$("addrBtn").onclick = () => {
  const a = $("addr");
  if (a.style.display === "block") { a.style.display = "none"; return; }
  draft = origins.map((o) => ({ ...o }));
  picking = -1;
  $("addrMsg").textContent = "";
  renderAddr();
  a.style.display = "block";
};
$("addrSave").onclick = async () => {
  const noLoc = draft.find((d) => !d.key && !d.loc);
  if (noLoc) { $("addrMsg").className = "err"; $("addrMsg").textContent = `先给「${noLoc.name || "新地址"}」选位置`; return; }
  let res;
  try {
    res = await api("/api/origins", { origins: draft.map((d) => ({ key: d.key, name: d.name, show: d.show, ...(d.loc ? { loc: d.loc } : {}) })) });
  } catch (e) { $("addrMsg").className = "err"; $("addrMsg").textContent = "保存失败：" + e.message; return; }
  // 成功的条目记下 key、清掉未保存标记；失败的保留输入，提示原因
  res.forEach((r, i) => Object.assign(draft[i], r.ok ? { key: r.key, loc: undefined, msg: "已保存", cls: "ok" } : { msg: r.msg, cls: "err" }));
  $("addrMsg").textContent = "";
  picking = -1;
  renderAddr();
  applyOrigins(await api("/api/origins"));
  onFormChange();  // 删掉的出发地的上限从存库的筛选条件里去掉
  if (res.some((r) => r.ok && r.changed)) { await loadGames(); startCompute("地址已更新"); }  // 位置变→重算通勤
};
```

删掉原来的 `$("addrBtn").onclick = …` 那一行（上面已重写）。

- [ ] **Step 4: 跑测试**

Run: `uv run pytest -q && node tests/filters.test.mjs`
Expected: 全部 PASS / OK

- [ ] **Step 5: 迁移测试库**

```bash
S=/private/tmp/claude-501/-Users-ymy-Projects-badminton-filter/fad5d9a7-8ec5-42e0-bd9a-500fa4f7e284/scratchpad
cd /Users/ymy/Projects/badminton-filter && sqlite3 games.db ".backup $S/test.db"
sqlite3 $S/test.db < $S/migrate-origins.sql   # Task 3 Step 5 写的脚本
(uv run $S/run.py > $S/run.log 2>&1 &); sleep 2
```

- [ ] **Step 6: 浏览器验证（8799 实例）**

用 cdp 打开 `http://127.0.0.1:8799/`（先 `--eval "window.confirm = () => true"`，加 `--filter "^$"`），依次验证并截图：
1. 筛选里有「家通勤上限」「公司通勤上限」；表头「俱乐部」后面是「家通勤」「公司通勤」；之前存的排序和其他筛选条件照常恢复。
2. 点「设置地址」→ 两行（家、公司），显示地址文字和地图链接。
3. 「+ 添加地址」→ 新行 + 选点区展开 + 成本提示有数字。名称填「父母家」，关键字搜「徐家汇」→ 点一个候选 → 行里显示「…（未保存）」。
4. 先不填名称再保存一次的分支：把「公司」名称清空，点保存 → 「公司」行显示「名称不能为空」，「父母家」行显示「已保存」且保留在面板里；把名称填回「公司」再保存 → 都是「已保存」。
5. 保存后：筛选里多出「父母家通勤上限」，表头多出「父母家通勤」列，状态栏开始算通勤（测试库会真的调高德，这一步只看到进度开始就行；需要时点开 `$S/run.log` 看）。
6. 给「父母家通勤上限」填 30 → 列表变少（或在通勤还没算完时受「含通勤未知」控制）；点「父母家通勤」表头排序 → 生效。
7. 把「父母家」的「显示成列」取消 → 保存 → 列消失、上限框还在、排序回到按时间；点开一行详情能看到「父母家 …」通勤。
8. 等通勤算完后删除「父母家」→ 保存 → 上限框和列都消失；`sqlite3 $S/test.db "SELECT COUNT(*) FROM commute WHERE origin LIKE 'o%'"` 为 0；`SELECT data FROM filters` 里的 `commuteMax` 没有该 key。
9. 算通勤进行中删除一个出发地 → 面板显示「正在算通勤，等算完再删除或改位置」，库没变。
10. 删掉全部出发地并保存 → 提示条出现，表格没有通勤列，详情没有通勤行，控制台无 exception。

测完 `pkill -f scratchpad/run.py`。

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: 主页支持多个出发地：出发地面板选点、按出发地生成上限框和通勤列

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 迁移本地 `games.db` + README

**Files:**
- Modify: `README.md:9,20,21,75,78,92,103`

- [ ] **Step 1: 确认迁移脚本**

`$S/migrate-origins.sql` 在 Task 3 Step 5 已写好，并已在测试库上跑通过。

- [ ] **Step 2: 迁移用户的 `games.db`**

这一步改的是用户的数据，执行前先确认用户已经停掉 8765 上的服务（WAL 模式下运行中也能改，但旧代码的 `/api/origins` 读新表会出错，需要用户重启到新代码）。

```bash
S=/private/tmp/claude-501/-Users-ymy-Projects-badminton-filter/fad5d9a7-8ec5-42e0-bd9a-500fa4f7e284/scratchpad
cd /Users/ymy/Projects/badminton-filter
sqlite3 games.db ".backup games.db.bak-$(date +%Y%m%d-%H%M%S)"
sqlite3 games.db < $S/migrate-origins.sql
sqlite3 games.db "SELECT key,name,show,pos,lat IS NOT NULL FROM origins; SELECT origin,COUNT(*) FROM commute GROUP BY origin"
```

Expected: `home|家|1|0|1`、`company|公司|1|1|1`；commute 两个 origin 的行数和迁移前一样。

- [ ] **Step 3: README**

- 第 9 行「家和公司公交通勤都不超过 60 分钟」保持（示例截图就是这么筛的）。
- 第 20 行改为：「**真实通勤**:调高德地图算各出发地(默认**家**和**公司**,可在「设置地址」里增删改名)的 **驾车 / 公交 / 步行** 通勤(平台自带的"距离"不准)。公交按傍晚 18:00 出发计算(避免午夜按停运算)。」
- 第 21 行「家/公司通勤上限」改为「各出发地通勤上限」。
- 第 75 行改为：「1. **设置地址**:点顶栏「设置地址」,每个出发地点「改位置」,输入关键字在高德搜索并点选;搜不到时可粘贴百度地图网页链接或高德坐标。可以「+ 添加地址」加更多出发地,勾选「显示成列」决定是否在表格里占一列(不显示的照样能设通勤上限)。」
- 第 78 行「到家/公司的通勤」改为「到各出发地的通勤」。
- 第 92 行 `origins`(家/公司地址与坐标) 改为 `origins`(出发地:名称、位置、坐标、是否显示成列、顺序)。
- 第 103 行「含家/公司地址等本地数据」改为「含出发地地址等本地数据」。

- [ ] **Step 4: 全量测试**

Run: `uv run pytest -q && node tests/filters.test.mjs`
Expected: 全部 PASS / OK

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: README 说明多出发地

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: 告诉用户**

提醒用户重启 8765 的服务（`uv run app.py`），并说明 `games.db` 的备份文件名。不推送，等用户说。
