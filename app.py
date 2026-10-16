# app.py
import json, sqlite3, threading, time, webbrowser
from pypinyin import Style, lazy_pinyin
from flask import Flask, jsonify, request, send_from_directory
import commute, creds, db, sync, venues

ORIGINS = ("home", "company")

def create_app(conn, config, db_path=None):
    app = Flask(__name__, static_folder=None)
    compute_lock = threading.Lock()   # 保证同时只有一个后台通勤任务
    compute_rerun = threading.Event()  # 运行期间又被触发 → 跑完再补一轮
    refresh_lock = threading.Lock()   # 保证同时只有一个后台拉取任务
    refresh_box = {"result": None, "progress": None}  # 最近一次拉取结果/进度（供前端轮询取回）
    compute_box = {"progress": None}  # 后台算通勤进度文字

    @app.get("/")
    def index():
        return send_from_directory("static", "index.html")

    @app.get("/venues")
    def venues_page():
        return send_from_directory("static", "venues.html")

    @app.get("/static/<path:p>")
    def static_file(p):  # 页面引用的 css/js（static_folder=None，统一走这里）
        return send_from_directory("static", p)

    @app.get("/api/games")
    def games():
        # 场馆归属查询时现关联：games.platform_name_norm → venue_aliases → canonical_venues
        rows = conn.execute(
            "SELECT games.*, a.canonical_id, c.amap_name FROM games"
            " LEFT JOIN venue_aliases a ON a.platform_name_norm = games.platform_name_norm"
            " LEFT JOIN canonical_venues c ON c.id = a.canonical_id").fetchall()
        cm = {}
        for r in conn.execute("SELECT origin,place_key,drive_min,transit_min,walk_m FROM commute"):
            cm[(r["origin"], r["place_key"])] = {"drive_min": r["drive_min"],
                                                 "transit_min": r["transit_min"],
                                                 "walk_m": r["walk_m"]}
        out = []
        for r in rows:
            g = dict(r)
            # 未归类：只有闪动场馆用自带坐标算过通勤；中羽联坐标不可信，通勤一律未知
            pk = commute.place_key(g["canonical_id"], g["venue_key"]) \
                if g["canonical_id"] or g["source"] == "flashx" else None
            g["commute"] = {o: cm.get((o, pk)) for o in ORIGINS}
            out.append(g)
        return jsonify({"games": out})

    @app.get("/api/config")
    def get_config():
        hist = [r["word"] for r in conn.execute("SELECT word FROM kw_history ORDER BY rowid")]
        row = conn.execute("SELECT data FROM filters WHERE id=1").fetchone()
        return jsonify({"filters": json.loads(row["data"]) if row else None,
                        "kw_history": hist})

    @app.post("/api/filters")
    def save_filters():
        # 整份筛选条件(前端表单状态)存一行；其中关键字顺带记进永久历史
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "msg": "body 须为 JSON 对象"}), 400
        for k in ("kwInc", "kwExc"):
            for w in body.get(k) or []:
                if isinstance(w, str) and w:
                    conn.execute("INSERT OR IGNORE INTO kw_history(word) VALUES(?)", (w,))
        conn.execute("INSERT OR REPLACE INTO filters(id,data) VALUES(1,?)",
                     (json.dumps(body, ensure_ascii=False),))
        conn.commit()
        return jsonify({"ok": True})

    @app.post("/api/pinyin")
    def pinyin():
        # 文本 → [全拼, 首字母]（小写、无空格），供下拉框按拼音匹配
        texts = (request.get_json(silent=True) or {}).get("texts") or []
        return jsonify({t: ["".join(lazy_pinyin(t)).lower(),
                            "".join(lazy_pinyin(t, style=Style.FIRST_LETTER)).lower()]
                        for t in texts if isinstance(t, str)})

    @app.get("/api/venues")
    def venues_list():
        # 管理页整张表 + 全部统一球场（编辑区「选已有统一球场」用）
        canon = [dict(r) for r in conn.execute("SELECT id, amap_name, lat, lng FROM canonical_venues ORDER BY amap_name")]
        return jsonify({"venues": sync.venue_rows(conn), "canonicals": canon})

    @app.get("/api/amap/search")
    def amap_search():  # 管理页「在高德搜索」：前 10 个 POI
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"ok": False, "msg": "缺少关键字 q"}), 400
        try:
            return jsonify({"pois": commute.poi_search(q, limit=10)})
        except commute.AmapError as e:
            return jsonify({"ok": False, "msg": str(e)}), 502

    def _alias_req():
        # 管理页写接口的公共校验：body 须含 platform_name_norm，且该馆名存在
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("platform_name_norm"), str):
            return None, None, (jsonify({"ok": False, "msg": "body 须为含 platform_name_norm 的 JSON 对象"}), 400)
        row = conn.execute("SELECT * FROM venue_aliases WHERE platform_name_norm=?",
                           (body["platform_name_norm"],)).fetchone()
        if not row:
            return body, None, (jsonify({"ok": False, "msg": "没有这个馆名"}), 404)
        return body, row, None

    def _set_alias(norm, origin, cid, touched="datetime('now')"):
        conn.execute(f"UPDATE venue_aliases SET origin=?, canonical_id=?, last_search=NULL, updated_at={touched}"
                     " WHERE platform_name_norm=?", (origin, cid, norm))
        conn.commit()

    def _is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    @app.post("/api/venues/assign")
    def venues_assign():
        # 手动指定：已有统一球场(canonical_id)、高德 POI(poi，amap_id 已有则复用) 或 手动坐标(coord)
        body, row, err = _alias_req()
        if err:
            return err
        cid, poi, coord = body.get("canonical_id"), body.get("poi"), body.get("coord")
        if isinstance(cid, int) and not isinstance(cid, bool):
            if not conn.execute("SELECT 1 FROM canonical_venues WHERE id=?", (cid,)).fetchone():
                return jsonify({"ok": False, "msg": "没有这个统一球场"}), 404
        elif (isinstance(poi, dict) and isinstance(poi.get("amap_id"), str) and isinstance(poi.get("name"), str)
              and _is_num(poi.get("lat")) and _is_num(poi.get("lng"))):
            try:
                cid = sync.canonical_for_poi(conn, poi)
            except sqlite3.IntegrityError:
                conn.rollback()  # 失败的 INSERT 已隐式开事务并拿写锁，不收掉会卡住后台线程的写入
                return jsonify({"ok": False, "msg": f"高德名「{poi['name']}」与已有统一球场重名"}), 409
        elif isinstance(coord, dict) and isinstance(coord.get("name"), str) and coord["name"].strip() \
                and _is_num(coord.get("lat")) and _is_num(coord.get("lng")):
            # 手动坐标（前端已换成高德坐标）：新建无 POI id 的统一球场
            if not venues.in_shanghai(coord["lat"], coord["lng"]):
                return jsonify({"ok": False, "msg": "坐标不在上海范围内，检查一下是不是复制错了"}), 400
            try:
                cid = sync.canonical_for_poi(conn, {"amap_id": None, "name": coord["name"].strip(),
                                                    "lat": coord["lat"], "lng": coord["lng"]})
            except sqlite3.IntegrityError:
                conn.rollback()
                return jsonify({"ok": False, "msg": f"「{coord['name'].strip()}」与已有统一球场重名，换个名字或直接选已有的"}), 409
        else:
            return jsonify({"ok": False, "msg": "需要 canonical_id、poi{amap_id,name,lat,lng} 或 coord{name,lat,lng}"}), 400
        _set_alias(row["platform_name_norm"], "manual", cid)
        has = conn.execute("SELECT 1 FROM commute WHERE place_key=?", (commute.place_key(cid, None),)).fetchone()
        return jsonify({"ok": True, "canonical_id": cid, "has_commute": bool(has)})

    @app.post("/api/venues/confirm")
    def venues_confirm():  # 自动归类确认无误
        body, row, err = _alias_req()
        if err:
            return err
        if row["origin"] != "auto":
            return jsonify({"ok": False, "msg": "只有自动归类可以确认"}), 409
        _set_alias(row["platform_name_norm"], "confirmed", row["canonical_id"])
        return jsonify({"ok": True})

    @app.post("/api/venues/none")
    def venues_none():  # 高德搜不到：通勤保持未知，不再自动重试
        body, row, err = _alias_req()
        if err:
            return err
        _set_alias(row["platform_name_norm"], "none", None)
        return jsonify({"ok": True})

    @app.post("/api/venues/reset")
    def venues_reset():  # 恢复自动：改回 pending（未搜索），下次「算通勤」重新归类
        body, row, err = _alias_req()
        if err:
            return err
        _set_alias(row["platform_name_norm"], "pending", None, touched="NULL")
        return jsonify({"ok": True})

    @app.post("/api/venues/restore")
    def venues_restore():
        # 撤销：把这一行整行写回操作前的状态（前端操作前记下的 origin/canonical_id/last_search/updated_at）
        body, row, err = _alias_req()
        if err:
            return err
        origin, cid = body.get("origin"), body.get("canonical_id")
        ls, ts = body.get("last_search"), body.get("updated_at")
        has_cid = origin in ("auto", "confirmed", "manual")
        ok = (origin in ("pending", "auto", "confirmed", "manual", "none")
              and (isinstance(cid, int) and not isinstance(cid, bool) if has_cid else cid is None)
              and ls in (None, "error", "empty", "dup") and (ts is None or isinstance(ts, str)))
        if not ok:
            return jsonify({"ok": False, "msg": "origin/canonical_id/last_search/updated_at 不合法"}), 400
        if has_cid and not conn.execute("SELECT 1 FROM canonical_venues WHERE id=?", (cid,)).fetchone():
            return jsonify({"ok": False, "msg": "没有这个统一球场"}), 404
        conn.execute("UPDATE venue_aliases SET origin=?, canonical_id=?, last_search=?, updated_at=?"
                     " WHERE platform_name_norm=?", (origin, cid, ls, ts, row["platform_name_norm"]))
        conn.commit()
        return jsonify({"ok": True})

    @app.post("/api/venues/prune")
    def venues_prune():  # 删掉没有馆名关联的统一球场
        if compute_lock.locked():  # 算通勤会把馆名归到已有统一球场，同时删可能删掉它刚选中的
            return jsonify({"ok": False, "msg": "正在算通勤，等算完再清理"}), 409
        return jsonify({"ok": True, "deleted": sync.delete_orphan_canonicals(conn)})

    @app.get("/api/origins")
    def get_origins():
        rows = {r["key"]: r for r in conn.execute("SELECT key,address,lat FROM origins")}
        return jsonify({o: {"address": rows[o]["address"] if o in rows else "",
                            "has_coord": o in rows and rows[o]["lat"] is not None}
                        for o in ORIGINS})

    @app.post("/api/origins")
    def save_origins():
        body = request.get_json(silent=True)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return jsonify({"ok": False, "msg": "body 须为 JSON 对象"}), 400
        old = {r["key"]: r["address"] for r in conn.execute("SELECT key,address FROM origins")}
        res = {}
        for o in ORIGINS:
            addr = (body.get(o) or "").strip()
            if not addr or addr == old.get(o):
                res[o] = {"ok": True, "changed": False}  # 空或没变：不动
                continue
            ll = commute.geocode(addr)
            if not ll:  # 查不到：不覆盖旧坐标
                res[o] = {"ok": False, "msg": "地址查不到，填具体些"}
                continue
            conn.execute("INSERT OR REPLACE INTO origins(key,address,lat,lng) VALUES(?,?,?,?)",
                         (o, addr, ll[0], ll[1]))
            conn.execute("DELETE FROM commute WHERE origin=?", (o,))  # 坐标变了，缓存作废
            conn.commit()
            res[o] = {"ok": True, "changed": True}
        return jsonify(res)  # 通勤由前端在地址改动后调 /api/commute 触发

    def _start_compute():
        # 后台算通勤，不阻塞请求。单飞：同时只跑一个线程；
        # 若运行期间又被触发（连点刷新/改地址），置 rerun，跑完自动再补一轮，不丢新活也不并发。
        if not db_path:
            return False
        if not compute_lock.acquire(blocking=False):
            compute_rerun.set()  # 已在跑 → 标记跑完再来一轮
            return True
        def bg():
            try:
                while True:
                    compute_rerun.clear()
                    compute_box["progress"] = None
                    sync.compute_pending(db_path, config, progress=_setter(compute_box, "算通勤"))
                    print(f"[算通勤] 完成：{compute_box['progress'] or '无需计算'}", flush=True)
                    if not compute_rerun.is_set():
                        break  # 期间无新触发才收工
            finally:
                compute_lock.release()
            if compute_rerun.is_set():  # 补查：防丢「判断与释放之间」卡进来的触发
                _start_compute()
        threading.Thread(target=bg, daemon=True).start()
        return True

    def _setter(box, tag):
        # 进度存进 box 供前端轮询，同时打到 console（每秒最多一行，免得缓存命中时刷屏）
        last = [0.0]
        def set_(msg):
            box["progress"] = msg
            if time.monotonic() - last[0] >= 1:
                last[0] = time.monotonic()
                print(f"[{tag}] {msg}", flush=True)
        return set_

    def _start_refresh():
        # 后台拉取入库，不阻塞请求（拉取 1333 场 + 解析较慢，同步会冻住单线程服务）
        if not db_path:  # 无 db_path(测试)：同步跑，用主连接
            refresh_box["result"] = sync.refresh(conn, creds.load(), config, progress=_setter(refresh_box, "刷新"))
            return False
        if not refresh_lock.acquire(blocking=False):
            return True  # 已在拉取
        def bg():
            try:
                refresh_box["progress"] = None
                c = db.connect(db_path)
                try:
                    refresh_box["result"] = sync.refresh(c, creds.load(), config, progress=_setter(refresh_box, "刷新"))
                    print(f"[刷新] 完成：{refresh_box['result']}", flush=True)
                finally:
                    c.close()
            finally:
                refresh_lock.release()
        threading.Thread(target=bg, daemon=True).start()
        return True

    @app.post("/api/refresh")
    def refresh():
        refreshing = _start_refresh()  # 只拉取入库，不算通勤
        return jsonify({"refreshing": refreshing,
                        "result": None if refreshing else refresh_box["result"]})

    @app.get("/api/refresh/status")
    def refresh_status():
        return jsonify({"refreshing": refresh_lock.locked(), "result": refresh_box["result"],
                        "progress": refresh_box["progress"]})

    @app.post("/api/commute")
    def commute_recompute():
        return jsonify({"computing": _start_compute()})  # 单独触发后台算通勤

    @app.get("/api/commute/status")
    def commute_status():
        return jsonify({"computing": compute_lock.locked(), "progress": compute_box["progress"],
                        "commute_places": conn.execute("SELECT COUNT(DISTINCT place_key) c FROM commute").fetchone()["c"],
                        "pending": sync.pending_count(conn), "unmatched": sync.unmatched_count(conn)})

    @app.post("/api/creds/capture")
    def capture():
        got = creds.capture() or {}
        return jsonify({p: bool(v) for p, v in got.items()})

    return app

if __name__ == "__main__":
    try:  # config.json 可选：缺省即用默认（端口 8765）
        with open("config.json", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    conn = db.connect("games.db")
    db.init_schema(conn)
    port = cfg.get("port", 8765)
    threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    # 主连接只在 Flask 单线程里用（threaded=False）；后台算通勤的线程自开连接（WAL 并发）
    create_app(conn, cfg, db_path="games.db").run(port=port, threaded=False)
