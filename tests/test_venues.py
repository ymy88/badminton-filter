# tests/test_venues.py
import venues

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
