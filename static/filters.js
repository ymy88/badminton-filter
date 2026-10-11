// 球局筛选与排序纯函数：浏览器挂全局，node 下 require 自测。

// 开始时间 "HH:MM" → 当日分钟数；缺失返回 null
function startMinutes(t) {
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

// 关键字/场馆双向匹配：include 命中任一即保留，exclude 命中任一即剔除；空数组/空串跳过
function textHit(text, list) {
  return list.some((s) => s && text.includes(s));
}

// 逐条过筛选条件，空条件（null/空数组/undefined）跳过
function applyFilters(games, criteria) {
  const c = criteria;
  return games.filter((g) => {
    if (c.date && g.date !== c.date) return false;

    if (c.startMin != null || c.startMax != null) {
      const s = startMinutes(g.start_time);
      if (s == null) return false;
      if (c.startMin != null && s < c.startMin) return false;
      if (c.startMax != null && s > c.startMax) return false;
    }

    // 时长分档 [min,max)（max 可为 Infinity）；时长未知（跨午夜 null）设了档即剔除
    if (c.durRange) {
      const d = g.duration_min;
      if (d == null || d < c.durRange[0] || d >= c.durRange[1]) return false;
    }

    if (c.origin && c.mode && c.commuteMax != null) {
      const v = g.commute?.[c.origin]?.[c.mode];
      if (v == null) {
        if (!c.includeUnknownCommute) return false;
      } else if (v > c.commuteMax) {
        return false;
      }
    }

    if (c.priceMax != null && !(g.price != null && g.price <= c.priceMax)) return false;

    if (c.appliedRange && (g.applied_num < c.appliedRange[0] || g.applied_num > c.appliedRange[1])) return false;
    if (c.totalRange && (g.total_num < c.totalRange[0] || g.total_num > c.totalRange[1])) return false;

    if (c.onlyOpen && !(g.applied_num < g.total_num)) return false;

    // 场馆同时匹配原始馆名与统一名（下拉可能给的是统一名）；include 用 some(Boolean) 容空串
    const venueText = (g.venue_name || "") + "\u0001" + (g.canonical_name || "");
    if (c.venueInclude?.some(Boolean) && !textHit(venueText, c.venueInclude)) return false;
    if (c.venueExclude?.length && textHit(venueText, c.venueExclude)) return false;

    if (c.kwInclude?.some(Boolean) && !textHit(g.raw_text || "", c.kwInclude)) return false;
    if (c.kwExclude?.length && textHit(g.raw_text || "", c.kwExclude)) return false;

    return true;
  });
}

// 排序取值表：by → 取数值的函数（全为数值，null/缺失统一排最后）
// 对应设计文档排序键：开始时间/通勤/价格/剩余空位(总−已报)/已报名
function sortKey(by, origin, mode) {
  switch (by) {
    case "commute": return (g) => g.commute?.[origin]?.[mode];
    case "start_time": return (g) => startMinutes(g.start_time);
    case "price": return (g) => g.price;
    case "open": return (g) =>
      g.total_num != null && g.applied_num != null ? g.total_num - g.applied_num : null;
    case "applied": return (g) => g.applied_num;
    default: return null;
  }
}

// 按 by 升序；缺失值排最后。返回新数组，不改入参；未知 by 原样返回副本
function sortGames(games, by, origin, mode) {
  const out = games.slice();
  const key = sortKey(by, origin, mode);
  if (!key) return out;
  const isNull = (v) => v == null || Number.isNaN(v);
  out.sort((a, b) => {
    const x = key(a), y = key(b);
    if (isNull(x) && isNull(y)) return 0;
    if (isNull(x)) return 1;
    if (isNull(y)) return -1;
    return x - y;
  });
  return out;
}

if (typeof module !== "undefined") module.exports = { applyFilters, sortGames };
