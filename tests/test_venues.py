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
    # 馆 A 只靠 platform_id 命中；馆 B 只靠名字 pattern 命中。
    # 两个馆都能命中，但 platform_id 必须优先，所以返回 A。
    # 若实现改成先查名字，就会返回 B，本测试失败。
    canon = [
        {"id": 1, "canonical_name": "馆A", "name_patterns": ["不会命中"],
         "platform_ids": ["flashx:12203"]},
        {"id": 2, "canonical_name": "馆B", "name_patterns": ["别的馆"],
         "platform_ids": ["flashx:999"]},
    ]
    assert venues.classify("别的馆综合", "flashx:12203", canon)["canonical_name"] == "馆A"

def test_empty_pattern_does_not_match_everything():
    # 空串 pattern 不能被当成包含任意字符串
    canon = [{"id": 3, "canonical_name": "空", "name_patterns": [""], "platform_ids": []}]
    assert venues.classify("任意场馆名", "zhongyulian:x", canon) is None

def test_missing_platform_ids_and_none_name_do_not_raise():
    canon = [{"id": 4, "canonical_name": "缺字段", "name_patterns": ["foo"]}]
    assert venues.classify(None, "flashx:1", canon) is None
