# app.py
import json, threading, webbrowser
from flask import Flask, jsonify, request, send_from_directory
import commute, creds, db, sync

ORIGINS = ("home", "company")

def create_app(conn, config, db_path=None):
    app = Flask(__name__, static_folder=None)
    compute_lock = threading.Lock()   # 保证同时只有一个后台通勤任务
    compute_rerun = threading.Event()  # 运行期间又被触发 → 跑完再补一轮
    refresh_lock = threading.Lock()   # 保证同时只有一个后台拉取任务
    refresh_box = {"result": None}    # 最近一次拉取结果（供前端轮询取回）

    @app.get("/")
    def index():
        return send_from_directory("static", "index.html")

    @app.get("/filters.js")
    def filters_js():  # 页面 <script src="filters.js"> 依赖此路由（static_folder=None）
        return send_from_directory("static", "filters.js")

    @app.get("/api/games")
    def games():
        rows = conn.execute(
            "SELECT games.*, canonical_venues.canonical_name FROM games "
            "LEFT JOIN canonical_venues ON games.canonical_id=canonical_venues.id").fetchall()
        cm = {}
        for r in conn.execute("SELECT origin,place_key,drive_min,transit_min,walk_m FROM commute"):
            cm[(r["origin"], r["place_key"])] = {"drive_min": r["drive_min"],
                                                 "transit_min": r["transit_min"],
                                                 "walk_m": r["walk_m"]}
        out = []
        for r in rows:
            g = dict(r)
            pk = commute.place_key(g["canonical_id"], g["venue_key"])
            g["commute"] = {o: cm.get((o, pk)) for o in ORIGINS}
            out.append(g)
        return jsonify({"games": out})

    @app.get("/api/config")
    def get_config():
        names = [r["canonical_name"] for r in
                 conn.execute("SELECT canonical_name FROM canonical_venues ORDER BY id")]
        rows = conn.execute("SELECT word,in_include,in_exclude FROM keywords ORDER BY rowid").fetchall()
        kw = {"include": [r["word"] for r in rows if r["in_include"]],
              "exclude": [r["word"] for r in rows if r["in_exclude"]]}
        hist = [r["word"] for r in rows]  # 全部用过的词（永久历史，供下拉）
        return jsonify({"keywords": kw, "kw_history": hist, "canonical_names": names})

    @app.post("/api/keywords")
    def save_keywords():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"ok": False, "msg": "body 须为 JSON 对象"}), 400
        inc = {s for s in (body.get("include") or []) if isinstance(s, str) and s}
        exc = {s for s in (body.get("exclude") or []) if isinstance(s, str) and s}
        for w in inc | exc:  # 新词建行（已存在忽略）——行永不删除 = 永久历史
            conn.execute("INSERT OR IGNORE INTO keywords(word) VALUES(?)", (w,))
        conn.execute("UPDATE keywords SET in_include=0, in_exclude=0")  # 重置后按当前选择置位
        for w in inc:
            conn.execute("UPDATE keywords SET in_include=1 WHERE word=?", (w,))
        for w in exc:
            conn.execute("UPDATE keywords SET in_exclude=1 WHERE word=?", (w,))
        conn.commit()
        return jsonify({"ok": True})

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
                    sync.compute_pending(db_path, config)
                    if not compute_rerun.is_set():
                        break  # 期间无新触发才收工
            finally:
                compute_lock.release()
            if compute_rerun.is_set():  # 补查：防丢「判断与释放之间」卡进来的触发
                _start_compute()
        threading.Thread(target=bg, daemon=True).start()
        return True

    def _start_refresh():
        # 后台拉取入库，不阻塞请求（拉取 1333 场 + 解析较慢，同步会冻住单线程服务）
        if not db_path:  # 无 db_path(测试)：同步跑，用主连接
            refresh_box["result"] = sync.refresh(conn, creds.load(), config)
            return False
        if not refresh_lock.acquire(blocking=False):
            return True  # 已在拉取
        def bg():
            try:
                c = db.connect(db_path)
                try:
                    refresh_box["result"] = sync.refresh(c, creds.load(), config)
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
        return jsonify({"refreshing": refresh_lock.locked(), "result": refresh_box["result"]})

    @app.post("/api/commute")
    def commute_recompute():
        return jsonify({"computing": _start_compute()})  # 单独触发后台算通勤

    @app.get("/api/commute/status")
    def commute_status():
        return jsonify({"computing": compute_lock.locked(),
                        "commute_places": conn.execute("SELECT COUNT(*) c FROM commute").fetchone()["c"],
                        "pending": sync.pending_count(conn)})

    @app.post("/api/creds/capture")
    def capture():
        got = creds.capture() or {}
        return jsonify({p: bool(v) for p, v in got.items()})

    return app

if __name__ == "__main__":
    try:  # config.json 可选：缺省即用默认（端口 8765 / 无关键字预设 / 场馆全自动）
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
