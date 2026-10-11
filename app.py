# app.py
import json, threading, webbrowser
from flask import Flask, jsonify, request, send_from_directory
import commute, creds, db, sync

ORIGINS = ("home", "company")

def create_app(conn, config):
    app = Flask(__name__, static_folder=None)

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
        for r in conn.execute("SELECT origin,place_key,drive_min,transit_min FROM commute"):
            cm[(r["origin"], r["place_key"])] = {"drive_min": r["drive_min"],
                                                 "transit_min": r["transit_min"]}
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
        return jsonify({"keywords": config.get("keywords", {"include": [], "exclude": []}),
                        "canonical_names": names})

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
        return jsonify(res)

    @app.post("/api/refresh")
    def refresh():
        return jsonify(sync.refresh(conn, creds.load(), config))

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
    create_app(conn, cfg).run(port=port, threaded=False)  # sqlite 连接不跨线程
