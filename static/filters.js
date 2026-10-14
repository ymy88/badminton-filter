// 球局筛选与排序纯函数（ES 模块）：主页 import，node 下 tests/filters.test.mjs 自测。

// 开始时间 "HH:MM" → 当日分钟数；缺失返回 null
function startMinutes(t) {
  if (!t) return null;
  const [h, m] = t.split(":").map(Number);
  return h * 60 + m;
}

// tags 格式不定：JSON 数组 → 标签；其它非空字符串 → 单个标签；空/异常 → 无
function parseTags(t) {
  if (!t) return [];
  try {
    const v = JSON.parse(t);
    if (Array.isArray(v)) return v.map(String).filter(Boolean);
    if (typeof v === "string" && v) return [v];
    return [];
  } catch (e) { return [String(t)]; }
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
    if (c.source && g.source !== c.source) return false;

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

    // 通勤上限：家/公司各自一个，均按当前方式 c.mode（驾车/公交）比较
    if (c.mode) {
      for (const [o, max] of [["home", c.homeCommuteMax], ["company", c.companyCommuteMax]]) {
        if (max == null) continue;
        const v = g.commute?.[o]?.[c.mode];
        if (v == null) { if (!c.includeUnknownCommute) return false; }
        else if (v > max) return false;
      }
    }

    if (c.priceMax != null && !(g.price != null && g.price <= c.priceMax)) return false;

    if (c.appliedRange && (g.applied_num < c.appliedRange[0] || g.applied_num > c.appliedRange[1])) return false;
    if (c.totalRange && (g.total_num < c.totalRange[0] || g.total_num > c.totalRange[1])) return false;

    if (c.onlyOpen && !(g.applied_num < g.total_num)) return false;

    // 场馆同时匹配原始馆名与统一名（下拉可能给的是统一名）；include 用 some(Boolean) 容空串
    const venueText = (g.venue_name || "") + "\u0001" + (g.amap_name || "");
    if (c.venueInclude?.some(Boolean) && !textHit(venueText, c.venueInclude)) return false;
    if (c.venueExclude?.length && textHit(venueText, c.venueExclude)) return false;

    if (c.clubInclude?.some(Boolean) && !textHit(g.club_name || "", c.clubInclude)) return false;
    if (c.clubExclude?.length && textHit(g.club_name || "", c.clubExclude)) return false;

    if (c.kwInclude?.some(Boolean) && !textHit(g.raw_text || "", c.kwInclude)) return false;
    if (c.kwExclude?.length && textHit(g.raw_text || "", c.kwExclude)) return false;

    return true;
  });
}

// 排序取值表：by → 取值函数（数值或字符串，null/空串统一排最后）
function sortKey(by, mode) {
  switch (by) {
    case "commute_home": return (g) => g.commute?.home?.[mode];
    case "commute_company": return (g) => g.commute?.company?.[mode];
    case "start_time": return (g) => startMinutes(g.start_time);
    case "price": return (g) => g.price;
    case "applied": return (g) => g.applied_num;
    case "club": return (g) => g.club_name;  // 字符串排序
    case "duration": return (g) => g.duration_min;
    case "venue": return (g) => g.amap_name || g.venue_name;  // 按高德名，同一球馆两平台排在一起
    case "source": return (g) => g.source;
    case "tags": return (g) => parseTags(g.tags).join(" ");
    default: return null;
  }
}

// 按 by 排序（desc 为降序）；缺失值无论升降都排最后。返回新数组，不改入参；未知 by 原样返回副本
function sortGames(games, by, mode, desc = false) {
  const out = games.slice();
  const key = sortKey(by, mode);
  if (!key) return out;
  const isNull = (v) => v == null || v === "" || Number.isNaN(v);
  out.sort((a, b) => {
    const x = key(a), y = key(b);
    if (isNull(x) && isNull(y)) return 0;
    if (isNull(x)) return 1;
    if (isNull(y)) return -1;
    const r = (typeof x === "string" || typeof y === "string")
      ? String(x).localeCompare(String(y), "zh") : x - y;  // 字符串按中文排序
    return desc ? -r : r;
  });
  return out;
}

// 下拉候选匹配：原文包含，或输入为字母数字时按全拼/首字母包含；py = [全拼, 首字母]（可缺）
function pyMatch(text, q, py) {
  if (!q || text.includes(q)) return true;
  const l = q.toLowerCase();
  return !!py && /^[a-z0-9]+$/.test(l) && (py[0].includes(l) || py[1].includes(l));
}

// 候选推荐：原文/拼音包含 q 的排最前，其余按相邻两字组合(bigram)重合度排序；都不沾边的不要
// 用于球馆管理页「已有统一球场」——平台馆名「宝山体育中心」也能推荐出「宝山体育馆」
function rankCandidates(q, names, py = {}, k = 8, minSim = 0.25) {
  const grams = (s) => { s = s.toLowerCase().replace(/\s+/g, ""); const g = new Set();
    for (let i = 0; i < s.length - 1; i++) g.add(s.slice(i, i + 2)); return g; };
  const gq = grams(q);
  const scored = names.map((n) => {
    if (pyMatch(n, q, py[n])) return [n, 2];  // 包含匹配优先
    const gn = grams(n);
    const inter = [...gq].filter((x) => gn.has(x)).length;
    return [n, inter / (gq.size + gn.size - inter || 1)];
  }).filter(([, s]) => s >= minSim);
  return scored.sort((a, b) => b[1] - a[1]).slice(0, k).map(([n]) => n);
}

// 百度坐标 BD-09 → 高德坐标 GCJ-02（公开换算公式，误差几米）
function bd09ToGcj02(lng, lat) {
  const xPi = Math.PI * 3000 / 180;
  const x = lng - 0.0065, y = lat - 0.006;
  const z = Math.sqrt(x * x + y * y) - 0.00002 * Math.sin(y * xPi);
  const theta = Math.atan2(y, x) - 0.000003 * Math.cos(x * xPi);
  return [z * Math.cos(theta), z * Math.sin(theta)];
}

// 粘贴的坐标串 → 高德坐标 {lat, lng}；sys="bd09" 时从百度坐标换算。
// 逗号/空格分隔，经纬度顺序不限（大于 90 的是经度）；解析不了返回 null
function parseCoord(text, sys = "gcj02") {
  const nums = (text || "").split(/[\s,，]+/).filter(Boolean).map(Number);
  if (nums.length !== 2 || !nums.every(Number.isFinite)) return null;
  let [lng, lat] = nums[0] > 90 ? nums : [nums[1], nums[0]];
  if (sys === "bd09") [lng, lat] = bd09ToGcj02(lng, lat);
  return { lat: +lat.toFixed(6), lng: +lng.toFixed(6) };
}

export { applyFilters, sortGames, pyMatch, parseTags, rankCandidates, parseCoord };
