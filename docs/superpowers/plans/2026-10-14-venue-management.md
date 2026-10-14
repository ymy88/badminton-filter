# 球馆管理页 Implementation Plan

> 用户要求直接开始写代码（未单独审阅本计划），由 Claude 在本会话内逐任务实现（native）。

**Goal:** 用「馆名 → 统一球场」对应表取代子串匹配归类，并提供球馆管理页审核/修正归类。

**Architecture:** 新表 `venue_aliases` 是馆名归属的唯一来源；`games` 不再存归类结果，查询时关联。`venues.py` 放馆名规范化与可疑规则纯函数；`sync.py` 放归类、通勤场馆、管理页数据；`app.py` 只做路由。前端抽出 `common.js`，改 ES Modules。

**Tech Stack:** Python 3.12+ / Flask / sqlite3 / 原生 HTML+JS（ES Modules）

**Spec:** `docs/superpowers/specs/2026-10-14-venue-management-design.md`

## Global Constraints

- 场馆坐标只来自高德 POI；中羽联坐标一律不用（`CLAUDE.md`）。
- `commute.geocode` 保留（家/公司地址用）；只删拿馆名做地理编码的兜底。
- `init_schema` 不写兼容旧结构的代码；旧库用一次性脚本迁移（不进代码库）。
- 不新增依赖。

## Review Focus

1. 用户在管理页改了归属的同时后台正在「算通勤」→ 自动归类不得覆盖 `manual`（UPDATE 带 `origin='pending'` 条件）。
2. 平台馆名为空 → 不写 `venue_aliases`，不报错。
3. `assign` 传入的 POI 与已有统一球场同名不同 id → 409，不崩。
4. 高德返回的 `address`/`adname` 为空时是 `[]` 而不是字符串 → 统一成空串。
5. 可疑规则遇到无括号、专名为空的名字 → 不误报、不抛异常。

## Tasks

1. **`venues.py` 纯函数**：`norm_name`、`distance_m`、`suspect_reasons`（分店不同 / 专名差异大 / 闪动坐标相距远）+ 单测。
2. **模型切换**：新 schema（`venue_aliases`、精简 `canonical_venues`、`games.platform_name_norm`、删 `venues` 表）；`upsert_games` 写 pending；`classify_pending` / `canonical_for_poi` / `commute_places` / `pending_count`；`commute.poi_search(name, limit)` 返回列表；删 `classify`、`enrich_venue`、`auto_canonical`、`seed_canonical_venues`、`load_canonical_list`、`_KEEP`；`/api/games` 改关联、`canonical_name`→`amap_name`（前后端）；`/api/config` 去掉 `canonical_names`。
3. **管理页接口**：`sync.venue_rows`；`GET /api/venues`、`GET /api/amap/search`、`POST /api/venues/{assign,confirm,none,reset}` + 测试。
4. **前端 ES Modules**：`static/common.js`；`filters.js` 改 `export`；主页 `<script type="module">`；`tests/filters.test.mjs`。
5. **管理页**：`static/venues.html`、`GET /venues`、主页链接；在数据库副本上用浏览器实际操作验证。
6. **迁移脚本与文档**：一次性脚本在副本上验证（不提交）；更新 README。
