// 球局筛选与排序纯函数：浏览器挂全局，node 下 require 自测。

// 开始时间 "HH:MM" → 当日分钟数；缺失返回 null
function startMinutes(t) {
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

// 关键字/场馆双向匹配：include 命中任一即保留，exclude 命中任一即剔除；空数组跳过
function textHit(text, list) {
  return list.some((s) => text.includes(s));
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

    // 时长未知（跨午夜 null）在设了下限时不算满足
    if (c.durMin != null && !(g.duration_min != null && g.duration_min >= c.durMin)) return false;

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

    if (c.venueInclude?.length && !textHit(g.venue_name || "", c.venueInclude)) return false;
    if (c.venueExclude?.length && textHit(g.venue_name || "", c.venueExclude)) return false;

    if (c.kwInclude?.length && !textHit(g.raw_text || "", c.kwInclude)) return false;
    if (c.kwExclude?.length && textHit(g.raw_text || "", c.kwExclude)) return false;

    return true;
  });
}

// 按通勤升序；通勤未知排最后。返回新数组，不改入参
function sortGames(games, by, origin, mode) {
  const out = games.slice();
  if (by === "commute") {
    const key = (g) => g.commute?.[origin]?.[mode];
    out.sort((a, b) => {
      const x = key(a), y = key(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1;
      if (y == null) return -1;
      return x - y;
    });
  }
  return out;
}

if (typeof module !== "undefined") module.exports = { applyFilters, sortGames };
