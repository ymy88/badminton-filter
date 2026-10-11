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

console.log("filters.js OK");
