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
