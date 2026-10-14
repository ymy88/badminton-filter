// 前端筛选纯函数自测：node tests/filters.test.js
const assert = require("assert");
const { applyFilters, sortGames, pyMatch, parseTags } = require("../static/filters.js");

const g = (o) => Object.assign({
  date: "2026-10-11", start_time: "19:00", duration_min: 120,
  venue_name: "万体汇", raw_text: "周日对抗局", price: 30,
  applied_num: 6, total_num: 12,
  commute: { home: { drive_min: 20, transit_min: 35 }, company: { drive_min: 50, transit_min: 60 } },
}, o);

// 关键字双向
assert.equal(applyFilters([g({ raw_text: "练球场" }), g({ raw_text: "对抗局" })],
  { kwInclude: ["练球"] }).length, 1);
assert.equal(applyFilters([g({ raw_text: "教练陪练" }), g({ raw_text: "双打" })],
  { kwExclude: ["教练"] }).length, 1);

// 场馆双向
assert.equal(applyFilters([g({ venue_name: "万体汇" }), g({ venue_name: "荫荫" })],
  { venueInclude: ["万体汇"] }).length, 1);
assert.equal(applyFilters([g({ venue_name: "万体汇" }), g({ venue_name: "荫荫" })],
  { venueExclude: ["荫荫"] }).length, 1);
// 场馆按统一名也能筛（下拉给的是统一名，原始馆名不同）
assert.equal(applyFilters([g({ venue_name: "万体汇", canonical_name: "徐家汇体育公园" }), g({ venue_name: "荫荫" })],
  { venueInclude: ["徐家汇"] }).length, 1);

// 俱乐部双向筛选
assert.equal(applyFilters([g({ club_name: "辉腾羽球" }), g({ club_name: "翎动" })],
  { clubInclude: ["辉腾"] }).length, 1);
assert.equal(applyFilters([g({ club_name: "辉腾羽球" }), g({ club_name: "翎动" })],
  { clubExclude: ["辉腾"] }).length, 1);

// 通勤上限：家/公司各自一个，按当前方式（家驾20、公司驾50）
assert.equal(applyFilters([g()], { mode: "drive_min", homeCommuteMax: 10 }).length, 0);
assert.equal(applyFilters([g()], { mode: "drive_min", homeCommuteMax: 30 }).length, 1);
assert.equal(applyFilters([g()], { mode: "drive_min", companyCommuteMax: 40 }).length, 0);
assert.equal(applyFilters([g()], { mode: "drive_min", companyCommuteMax: 60 }).length, 1);
// 家、公司可同时设，都要满足
assert.equal(applyFilters([g()], { mode: "drive_min", homeCommuteMax: 30, companyCommuteMax: 40 }).length, 0);

// 通勤未知保留/隐藏（按家）
const unk = g({ commute: { home: { drive_min: null, transit_min: null }, company: {} } });
assert.equal(applyFilters([unk], { mode: "drive_min", homeCommuteMax: 30, includeUnknownCommute: true }).length, 1);
assert.equal(applyFilters([unk], { mode: "drive_min", homeCommuteMax: 30, includeUnknownCommute: false }).length, 0);

// 时长分档：未知时长（跨午夜 null）设了档即剔除；没设档不受影响
const durNull = g({ duration_min: null });
assert.equal(applyFilters([durNull], { durRange: [60, 120] }).length, 0);
assert.equal(applyFilters([durNull], { durRange: null }).length, 1);
// 分档边界：120 分钟属于「2小时」[120,180)，不属于「1小时」[60,120)（上界开区间）
assert.equal(applyFilters([g({ duration_min: 120 })], { durRange: [120, 180] }).length, 1);
assert.equal(applyFilters([g({ duration_min: 120 })], { durRange: [60, 120] }).length, 0);
// 90 分钟(1.5h)归「1小时」档；4小时及以上 = [240,∞)
assert.equal(applyFilters([g({ duration_min: 90 })], { durRange: [60, 120] }).length, 1);
assert.equal(applyFilters([g({ duration_min: 300 })], { durRange: [240, Infinity] }).length, 1);

// 还有空位
assert.equal(applyFilters([g({ applied_num: 12, total_num: 12 }), g({ applied_num: 6, total_num: 12 })],
  { onlyOpen: true }).length, 1);

// 排序：家通勤升序（按驾车）
const s = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { home: { drive_min: 5 } } })],
  "commute_home", "drive_min");
assert.equal(s[0].source_id, "b");
// 排序：公司通勤升序（a 公司驾50，b 公司驾10）
const sco = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { company: { drive_min: 10 } } })],
  "commute_company", "drive_min");
assert.equal(sco[0].source_id, "b");

// 排序：开始时间升序
assert.equal(sortGames([g({ source_id: "a", start_time: "20:00" }), g({ source_id: "b", start_time: "08:00" })],
  "start_time")[0].source_id, "b");
// 排序：价格升序
assert.equal(sortGames([g({ source_id: "a", price: 50 }), g({ source_id: "b", price: 10 })],
  "price")[0].source_id, "b");
// 排序：已报名升序
assert.equal(sortGames([g({ source_id: "a", applied_num: 9 }), g({ source_id: "b", applied_num: 1 })],
  "applied")[0].source_id, "b");
// 排序：俱乐部（字符串，中文）升序；无俱乐部排最后
const sc = sortGames([g({ source_id: "a", club_name: "乙俱乐部" }), g({ source_id: "b", club_name: "甲俱乐部" }), g({ source_id: "c", club_name: null })], "club");
assert.equal(sc[0].source_id, "b");
assert.equal(sc[2].source_id, "c");
// 排序：缺失值排最后（price/start_time/applied 各一例，验证共用比较器）
const sn = sortGames([g({ source_id: "a", price: null }), g({ source_id: "b", price: 10 })], "price");
assert.equal(sn[0].source_id, "b"); assert.equal(sn[1].source_id, "a");
assert.equal(sortGames([g({ source_id: "a", start_time: null }), g({ source_id: "b", start_time: "08:00" })], "start_time")[1].source_id, "a");
assert.equal(sortGames([g({ source_id: "a", applied_num: null }), g({ source_id: "b", applied_num: 1 })], "applied")[1].source_id, "a");
// sortGames 不改入参；未知 by 返回副本
const inp = [g({ source_id: "a" })]; sortGames(inp, "price"); assert.equal(inp[0].source_id, "a");
assert.equal(sortGames(inp, "nope").length, 1);

// 回归：include 仅含空串视为无筛选（不误剔全部）
assert.equal(applyFilters([g({ raw_text: "练球" }), g({ raw_text: "双打" })], { kwInclude: [""] }).length, 2);
assert.equal(applyFilters([g({ venue_name: "万体汇" })], { venueInclude: [""] }).length, 1);

console.log("filters.js OK");

// 下拉拼音匹配：原文 / 全拼 / 首字母，大小写不敏感；没拼音数据时只按原文
const py = ["wantihui", "wth"];
assert.ok(pyMatch("万体汇", "体汇", py));
assert.ok(pyMatch("万体汇", "wanti", py));
assert.ok(pyMatch("万体汇", "WTH", py));
assert.ok(!pyMatch("万体汇", "xyz", py));
assert.ok(!pyMatch("万体汇", "wth", undefined));
assert.ok(pyMatch("万体汇", "", py));

// 来源筛选
assert.equal(applyFilters([g({ source: "flashx" }), g({ source: "zhongyulian" })], { source: "flashx" }).length, 1);
assert.equal(applyFilters([g({ source: "flashx" }), g({ source: "zhongyulian" })], { source: null }).length, 2);

// 新增排序列：时长数值、场馆按统一名（无则原名）、标签空排最后
const ids = (arr) => arr.map((x) => x.id).join();
assert.equal(ids(sortGames([g({ id: "a", duration_min: 180 }), g({ id: "b", duration_min: null }), g({ id: "c", duration_min: 60 })], "duration")), "c,a,b");
assert.equal(ids(sortGames([g({ id: "a", venue_name: "乙", canonical_name: "甲馆" }), g({ id: "b", venue_name: "阿" })], "venue")), "b,a");
assert.equal(ids(sortGames([g({ id: "a", tags: "[]" }), g({ id: "b", tags: '["新手局"]' })], "tags")), "b,a");
assert.deepEqual(parseTags('["a",""]'), ["a"]);

// 降序：值倒过来，缺失值仍排最后
assert.equal(ids(sortGames([g({ id: "a", price: 10 }), g({ id: "b", price: null }), g({ id: "c", price: 50 })], "price", null, true)), "c,a,b");
assert.equal(ids(sortGames([g({ id: "a", club_name: "甲" }), g({ id: "b", club_name: "" }), g({ id: "c", club_name: "乙" })], "club", null, true)), "c,a,b");
