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

// 百度墨卡托 → 百度经纬度（BD-09）：按纬度分带的多项式，系数取自百度地图 JS API 的 MC2LL
const MCBAND = [12890594.86, 8362377.87, 5591021, 3481989.83, 1678043.12, 0];
const MC2LL = [
  [1.410526172116255e-08, 8.98305509648872e-06, -1.9939833816331, 200.9824383106796, -187.2403703815547, 91.6087516669843, -23.38765649603339, 2.57121317296198, -0.03801003308653, 17337981.2],
  [-7.435856389565537e-09, 8.983055097726239e-06, -0.78625201886289, 96.32687599759846, -1.85204757529826, -59.36935905485877, 47.40033549296737, -16.50741931063887, 2.28786674699375, 10260144.86],
  [-3.030883460898826e-08, 8.98305509983578e-06, 0.30071316287616, 59.74293618442277, 7.357984074871, -25.38371002664745, 13.45380521110908, -3.29883767235584, 0.32710905363475, 6856817.37],
  [-1.981981304930552e-08, 8.983055099779535e-06, 0.03278182852591, 40.31678527705744, 0.65659298677277, -4.44255534477492, 0.85341911805263, 0.12923347998204, -0.04625736007561, 4482777.06],
  [3.09191371068437e-09, 8.983055096812155e-06, 6.995724062e-05, 23.10934304144901, -0.00023663490511, -0.6321817810242, -0.00663494467273, 0.03430082397953, -0.00466043876332, 2555164.4],
  [2.890871144776878e-09, 8.983055095805407e-06, -3.068298e-08, 7.47137025468032, -3.53937994e-06, -0.02145144861037, -1.234426596e-05, 0.00010322952773, -3.23890364e-06, 826088.5],
];
function bdMercatorToBd09(x, y) {
  const c = MC2LL[MCBAND.findIndex((b) => Math.abs(y) >= b)];
  const t = Math.abs(y) / c[9];
  let lat = 0;
  for (let k = 6; k >= 0; k--) lat = lat * t + c[2 + k];
  return [c[0] + c[1] * Math.abs(x), lat];
}

// 粘贴的坐标串 → 高德坐标 {lat, lng, coarse}；sys="bd09" 时从百度坐标换算。
// 逗号/空格分隔，经纬度顺序不限（大于 90 的是经度）；解析不了返回 null。
// coarse：任一数小数不足 4 位（坐标拾取器未登录/未认证只显示 2 位，误差可达几百米）
// 也认百度地图网页链接（map.baidu.com/…/@x,y,19z）：@ 后是地图中心的百度墨卡托坐标，与 sys 无关
function parseCoord(text, sys = "gcj02") {
  const m = /baidu\.com\/.*@(-?[\d.]+),(-?[\d.]+)/.exec(text || "");
  if (m) {
    const [lng, lat] = bd09ToGcj02(...bdMercatorToBd09(+m[1], +m[2]));
    return { lat: +lat.toFixed(6), lng: +lng.toFixed(6), coarse: false };
  }
  const parts = (text || "").split(/[\s,，]+/).filter(Boolean);
  const nums = parts.map(Number);
  if (nums.length !== 2 || !nums.every(Number.isFinite)) return null;
  let [lng, lat] = nums[0] > 90 ? nums : [nums[1], nums[0]];
  if (sys === "bd09") [lng, lat] = bd09ToGcj02(lng, lat);
  const coarse = parts.some((p) => (p.split(".")[1] || "").length < 4);
  return { lat: +lat.toFixed(6), lng: +lng.toFixed(6), coarse };
}

export { applyFilters, sortGames, pyMatch, parseTags, rankCandidates, parseCoord };
