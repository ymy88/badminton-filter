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

# 评审修复：响应结构异常（缺 duration）时置 None，不抛
def test_route_minutes_missing_duration(monkeypatch):
    def fake(url, params):
        if "driving" in url:
            return {"status": "1", "route": {"paths": [{}]}}
        return {"status": "1", "route": {"transits": [{}]}}
    monkeypatch.setattr(commute, "_amap_get", fake)
    assert commute.route_minutes("121,31", "121.5,31.2") == {"drive_min": None, "transit_min": None}

# 评审修复：geocode 的 location 格式异常时返回 None，不抛
def test_geocode_bad_location(monkeypatch):
    monkeypatch.setattr(commute, "_amap_get",
                        lambda url, params: {"status": "1", "geocodes": [{"location": "bad"}]})
    assert commute.geocode("某地址") is None

def test_poi_search_parses(monkeypatch):
    # 取第一条 POI：名字 + id + 坐标(lng,lat → lat,lng)
    monkeypatch.setattr(commute, "_amap_get", lambda url, params: {"status": "1",
        "pois": [{"id": "B1", "name": "徐家汇体育公园", "location": "121.43,31.18"}]})
    assert commute.poi_search("万体汇") == {"amap_id": "B1", "name": "徐家汇体育公园", "lat": 31.18, "lng": 121.43}

def test_poi_search_empty(monkeypatch):
    monkeypatch.setattr(commute, "_amap_get", lambda url, params: {"status": "1", "pois": []})
    assert commute.poi_search("不存在") is None

def test_route_minutes_transit_gets_departure_time(monkeypatch):
    # 公交必须带出发时间 date/time，否则午夜按停运算（返回空/超长）
    seen = {}
    def fake(url, params):
        if "transit" in url:
            seen.update(params)
        return {"status": "1", "route": {"paths": [{"duration": "600"}], "transits": [{"duration": "600"}]}}
    monkeypatch.setattr(commute, "_amap_get", fake)
    commute.route_minutes("121,31", "121.5,31.2")
    assert seen.get("time") == commute.DEPART_TIME and "date" in seen
