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

def test_missing_showcost_price_none():
    r = _rec(); r["data"].pop("showCost")
    assert f.parse_games(_resp([r]))[0]["price"] is None

def test_cross_midnight_duration_none():
    assert f.parse_games(_resp([_rec(startTime="23:00", endTime="00:30")]))[0]["duration_min"] is None

def test_non_json_or_fail_returns_empty():
    assert f.parse_games("<html>") == []
    assert f.parse_games(json.dumps({"success": False})) == []
    assert f.parse_games("[]") == []
    assert f.parse_games("null") == []
    assert f.parse_games(json.dumps({"success": True, "result": None})) == []
