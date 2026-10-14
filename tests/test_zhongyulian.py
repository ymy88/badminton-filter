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
    # 合法 JSON 但不是对象（如 [] / null）也应返回空，不崩
    for s in ["[]", "5", "null"]:
        assert z.parse_games(s, 0, TODAY) == []

def test_ok_not_1_returns_empty():
    assert z.parse_games(json.dumps({"ok": -2, "error": "x"}), 0, TODAY) == []

# Review Focus: time 异常容错
def test_time_malformed():
    g = z.parse_games(_resp([_item(time="")]), 0, TODAY)[0]
    assert g["start_time"] is None and g["duration_min"] is None
    g2 = z.parse_games(_resp([_item(time="08:00~22:30")]), 0, TODAY)[0]
    assert g2["duration_min"] == 870
    # 无 "~" 分隔：起止与时长都为 None
    g3 = z.parse_games(_resp([_item(time="19:00")]), 0, TODAY)[0]
    assert g3["start_time"] is None and g3["duration_min"] is None
    # 跨零点（负时长）：不产生负数
    g4 = z.parse_games(_resp([_item(time="22:00~08:00")]), 0, TODAY)[0]
    assert g4["duration_min"] is None

def test_fetch_keeps_pages_when_later_page_fails(monkeypatch):
    # 首页满 10 条，第 2 页重试后仍超时 → 不整体失败，保留首页
    import json
    from platforms import zhongyulian as z
    calls = []
    class R:
        def __init__(self, b): self.b = b
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return self.b
    def fake_urlopen(req, timeout):
        skip = json.loads(req.data)["skip"]
        calls.append(skip)
        if skip:
            raise TimeoutError("read timed out")
        return R(json.dumps({"ok": 1, "list": {"other_club_movement_list": [{"activity_id": str(i)} for i in range(10)]}}).encode())
    monkeypatch.setattr(z.urllib.request, "urlopen", fake_urlopen)
    out = json.loads(z.fetch({"skey": "k"}, 0))
    assert len(out["list"]["other_club_movement_list"]) == 10
    assert calls == [0, 10, 10]  # 第 2 页重试了一次

def test_duration_rounded_to_30min():
    dur = lambda t: z.parse_games(_resp([_item(time=t)]), day=0, today=TODAY)[0]["duration_min"]
    assert dur("21:00~23:59") == 180
    assert dur("19:00~21:15") == 150
