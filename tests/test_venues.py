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

# ---- 馆名规范化与可疑规则 ----

def test_norm_name_unifies_width_case_space():
    assert venues.norm_name("堂颂羽毛球馆（淞南店）") == venues.norm_name("堂颂羽毛球馆(淞南店)")
    assert venues.norm_name(" Mook羽毛球馆 ") == "mook羽毛球馆"
    assert venues.norm_name("VS  运动\t中心") == "vs 运动 中心"
    assert venues.norm_name(None) == ""

def test_suspect_branch_differs():
    assert venues.suspect_reasons("堂颂羽毛球馆(淞南店)", "堂颂羽毛球馆(大华店)") == ["分店不同"]

def test_suspect_branch_ok_when_one_side_has_no_branch():
    assert venues.suspect_reasons("YONEX虎啸羽毛球馆(普陀馆)", "YONEX虎啸球馆") == []

def test_suspect_branch_ok_when_same_branch_different_suffix():
    # 「大华」与「大华店」去掉通用词后相同，不算分店不同
    assert venues.suspect_reasons("堂颂羽毛球馆（大华）", "堂颂羽毛球馆(大华店)") == []

def test_suspect_core_name_differs():
    assert venues.suspect_reasons("静安体育馆羽毛球场", "VJUMP微跃羽毛球篮球乒乓球馆旗舰店") == ["专名差异大"]

def test_suspect_same_name_not_flagged():
    assert venues.suspect_reasons("万体汇", "万体汇") == []
    assert venues.suspect_reasons("Mook羽毛球馆", "MOOK羽毛球馆(新华园店)") == []

def test_suspect_generic_only_names_not_flagged():
    # 专名为空（全是通用词）时不判断，避免误报
    assert venues.suspect_reasons("羽毛球馆", "体育中心") == []

def test_suspect_flashx_far():
    r = venues.suspect_reasons("堂颂羽毛球馆", "堂颂羽毛球馆(大华店)",
                               flashx_ll=(31.3267, 121.4119), amap_ll=(31.2904, 121.4119))
    assert r == ["闪动坐标相距 4.0km"]

def test_suspect_flashx_near_not_flagged():
    assert venues.suspect_reasons("万体汇", "万体汇", flashx_ll=(31.180, 121.440), amap_ll=(31.181, 121.441)) == []

def test_distance_m():
    assert abs(venues.distance_m((31.0, 121.0), (31.01, 121.0)) - 1112) < 5
