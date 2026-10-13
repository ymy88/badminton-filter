# app.py
import json, threading, webbrowser
from pypinyin import Style, lazy_pinyin
from flask import Flask, jsonify, request, send_from_directory
import commute, creds, db, sync

ORIGINS = ("home", "company")

def create_app(conn, config, db_path=None):
    app = Flask(__name__, static_folder=None)
    compute_lock = threading.Lock()   # 保证同时只有一个后台通勤任务
    compute_rerun = threading.Event()  # 运行期间又被触发 → 跑完再补一轮
    refresh_lock = threading.Lock()   # 保证同时只有一个后台拉取任务
    refresh_box = {"result": None, "progress": None}  # 最近一次拉取结果/进度（供前端轮询取回）
    compute_box = {"progress": None}  # 后台算通勤进度 (阶段, 已完成, 总数)

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
        hist = [r["word"] for r in conn.execute("SELECT word FROM kw_history ORDER BY rowid")]
        row = conn.execute("SELECT data FROM filters WHERE id=1").fetchone()
        return jsonify({"filters": json.loads(row["data"]) if row else None,
                        "kw_history": hist, "canonical_names": names})

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
                    sync.compute_pending(db_path, config, progress=_setter(compute_box))
                    if not compute_rerun.is_set():
                        break  # 期间无新触发才收工
            finally:
                compute_lock.release()
            if compute_rerun.is_set():  # 补查：防丢「判断与释放之间」卡进来的触发
                _start_compute()
        threading.Thread(target=bg, daemon=True).start()
        return True

    def _setter(box):
        return lambda *p: box.__setitem__("progress", p)

    def _start_refresh():
        # 后台拉取入库，不阻塞请求（拉取 1333 场 + 解析较慢，同步会冻住单线程服务）
        if not db_path:  # 无 db_path(测试)：同步跑，用主连接
            refresh_box["result"] = sync.refresh(conn, creds.load(), config, progress=_setter(refresh_box))
            return False
        if not refresh_lock.acquire(blocking=False):
            return True  # 已在拉取
        def bg():
            try:
                refresh_box["progress"] = None
                c = db.connect(db_path)
                try:
                    refresh_box["result"] = sync.refresh(c, creds.load(), config, progress=_setter(refresh_box))
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
