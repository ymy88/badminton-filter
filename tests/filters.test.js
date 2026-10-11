// 前端筛选纯函数自测：node tests/filters.test.js
const assert = require("assert");
const { applyFilters, sortGames } = require("../static/filters.js");

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

// 通勤上限（当前出发地+方式）
assert.equal(applyFilters([g()], { origin: "home", mode: "drive_min", commuteMax: 10 }).length, 0);
assert.equal(applyFilters([g()], { origin: "home", mode: "drive_min", commuteMax: 30 }).length, 1);

// 通勤未知保留/隐藏
const unk = g({ commute: { home: { drive_min: null, transit_min: null }, company: {} } });
assert.equal(applyFilters([unk], { origin: "home", mode: "drive_min", commuteMax: 30, includeUnknownCommute: true }).length, 1);
assert.equal(applyFilters([unk], { origin: "home", mode: "drive_min", commuteMax: 30, includeUnknownCommute: false }).length, 0);

// 时长下限：未知时长（跨午夜 null）在设了下限时不满足；没设下限时不受影响
const durNull = g({ duration_min: null });
assert.equal(applyFilters([durNull], { durMin: 60 }).length, 0);
assert.equal(applyFilters([durNull], { durMin: null }).length, 1);

// 还有空位
assert.equal(applyFilters([g({ applied_num: 12, total_num: 12 }), g({ applied_num: 6, total_num: 12 })],
  { onlyOpen: true }).length, 1);

// 排序：通勤升序
const s = sortGames([g({ source_id: "a" }), g({ source_id: "b", commute: { home: { drive_min: 5 } } })],
  "commute", "home", "drive_min");
assert.equal(s[0].source_id, "b");

// 排序：开始时间升序
assert.equal(sortGames([g({ source_id: "a", start_time: "20:00" }), g({ source_id: "b", start_time: "08:00" })],
  "start_time")[0].source_id, "b");
// 排序：价格升序
assert.equal(sortGames([g({ source_id: "a", price: 50 }), g({ source_id: "b", price: 10 })],
  "price")[0].source_id, "b");
// 排序：剩余空位升序（总−已报 小的在前）
assert.equal(sortGames([g({ source_id: "a", applied_num: 2, total_num: 12 }), g({ source_id: "b", applied_num: 11, total_num: 12 })],
  "open")[0].source_id, "b");
// 排序：已报名升序
assert.equal(sortGames([g({ source_id: "a", applied_num: 9 }), g({ source_id: "b", applied_num: 1 })],
  "applied")[0].source_id, "b");
// 排序：缺失值排最后（price/start_time/open/applied 各一例，验证共用比较器）
const sn = sortGames([g({ source_id: "a", price: null }), g({ source_id: "b", price: 10 })], "price");
assert.equal(sn[0].source_id, "b"); assert.equal(sn[1].source_id, "a");
assert.equal(sortGames([g({ source_id: "a", start_time: null }), g({ source_id: "b", start_time: "08:00" })], "start_time")[1].source_id, "a");
assert.equal(sortGames([g({ source_id: "a", total_num: null }), g({ source_id: "b", applied_num: 1, total_num: 12 })], "open")[1].source_id, "a");
assert.equal(sortGames([g({ source_id: "a", applied_num: null }), g({ source_id: "b", applied_num: 1 })], "applied")[1].source_id, "a");
// sortGames 不改入参；未知 by 返回副本
const inp = [g({ source_id: "a" })]; sortGames(inp, "price"); assert.equal(inp[0].source_id, "a");
assert.equal(sortGames(inp, "nope").length, 1);

// 回归：include 仅含空串视为无筛选（不误剔全部）
assert.equal(applyFilters([g({ raw_text: "练球" }), g({ raw_text: "双打" })], { kwInclude: [""] }).length, 2);
assert.equal(applyFilters([g({ venue_name: "万体汇" })], { venueInclude: [""] }).length, 1);

console.log("filters.js OK");
