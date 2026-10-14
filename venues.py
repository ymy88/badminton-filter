# venues.py —— 场馆名相关纯函数：馆名规范化 + 可疑归类判断（不访问数据库/网络）
import math, re, unicodedata

SIM_MIN = 0.2   # 专名 bigram 重合度低于此值 → 可疑
FAR_M = 1000    # 闪动坐标与高德坐标相距超过此值(米) → 可疑
# 通用词：比较「专名」前去掉；长词在前，免得「羽球」先吃掉「羽毛球」的一部分
_GENERIC = re.compile("羽毛球|乒乓球|俱乐部|羽球|体育|运动|中心|球馆|场馆|综合|篮球|乒羽|健身|训练|培训"
                      "|基地|公园|社区|上海|旗舰|校区|馆|市|区|店")

def classify(venue_name, venue_key, canonical_list):
    name = venue_name or ""
    for c in canonical_list:
        if venue_key in (c.get("platform_ids") or []):
            return c
    for c in canonical_list:
        if any(p and p in name for p in (c.get("name_patterns") or [])):
            return c
    return None

def norm_name(s):
    # 馆名规范化：NFKC（全角括号/字母转半角）→ 合并空白 → 去首尾空格 → 小写
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip().lower()

def _branch(s):
    # 第一个括号里的内容（分店名），没有则 None
    m = re.search(r"\((.+?)\)", norm_name(s))
    return m.group(1) if m else None

def _core(s):
    # 专名：去括号内容、通用词、标点空白
    s = re.sub(r"\(.*?\)", "", norm_name(s))
    return re.sub(r"[\W_]", "", _GENERIC.sub("", s))

def _bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}

def distance_m(a, b):
    # 两点 (lat, lng) 球面直线距离（米）
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    h = math.sin((la2 - la1) * p / 2) ** 2 + \
        math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))

def suspect_reasons(platform_name, amap_name, flashx_ll=None, amap_ll=None):
    # 自动归类是否可疑：返回原因列表，空 = 不可疑
    out = []
    b1, b2 = _branch(platform_name), _branch(amap_name)
    if b1 and b2:
        k1, k2 = _core(b1), _core(b2)
        if k1 and k2 and k1 not in k2 and k2 not in k1:
            out.append("分店不同")
    c1, c2 = _core(platform_name), _core(amap_name)
    if c1 and c2 and c1 not in c2 and c2 not in c1:
        a, b = _bigrams(c1), _bigrams(c2)
        if len(a & b) / len(a | b) < SIM_MIN:
            out.append("专名差异大")
    if flashx_ll and amap_ll:
        d = distance_m(flashx_ll, amap_ll)
        if d > FAR_M:
            out.append(f"闪动坐标相距 {d / 1000:.1f}km")
    return out
