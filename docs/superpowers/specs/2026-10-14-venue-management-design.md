# 球馆管理页 — 设计文档

日期：2026-10-14
状态：已通过 brainstorming，待写实现计划

---

## 1. 背景与目标

球局的通勤按「场馆 → 高德 POI 坐标」计算。现有的自动归类有两类错误：

1. **名字子串匹配吞掉分店**：`venues.classify` 只要某个统一球场的 `name_patterns` 是新馆名的子串就归过去。`name_patterns` 里有「堂颂羽毛球馆」这种不带分店的名字，于是「堂颂羽毛球馆(淞南店)」「(红光店)」「(V翼店)」全部被归进「堂颂羽毛球馆(大华店)」。
2. **高德 POI 搜索取第一条结果，取错**：「静安体育馆羽毛球场」被归到「VJUMP微跃」，「康健社区体育场-羽毛球馆」被归到「上海师范大学徐汇校区西部羽毛球馆」。

另有 69 个平台场馆没归类（其中 30 个没有坐标）。

**目标**：

- 每个平台馆名都能对应到正确的高德 POI，通勤算得准。
- 提供球馆管理页，集中审核可疑的自动归类，修正错误、处理未归类的场馆。
- 手动修正不会被之后的自动归类改回去。
- 自动归类本身不再吞分店。

**约束**（沿用项目约定，见 `CLAUDE.md`）：

- 场馆坐标只来自高德 POI。手动修正也只能从高德搜索结果里选，不能手填经纬度。
- 中羽联坐标一律不用，连用来判断可疑都不行。
- 高德确实搜不到的场馆，通勤保持「未知」。

### 已确认决策

| 项 | 决定 |
|---|---|
| 怎么发现错误 | 页面突出显示可疑的自动归类（三条规则，见第 5 节），其余默认可信；也支持搜索任意馆名手动改 |
| 可疑判断 | 规则判断，不用 embedding（embedding 对「同类语义」和「只差分店名」都不敏感，且要引入模型） |
| 修正的单位 | 规范化后的**馆名**，不区分平台。同名的所有场馆 id、以后新出现的同名场馆一起生效 |
| 修正的方式 | 优先选已有统一球场（支持拼音）；没有合适的再搜高德，从结果里选 |
| 选高德结果的辅助 | 文字列表（名称、所在区、地址）+「地图」链接，不内嵌地图（内嵌要另申请高德 JS API key） |
| 存储方案 | 新建「馆名 → 统一球场」对应表 `venue_aliases`，自动和手动都存这里；删掉子串匹配 |
| 地理编码兜底 | 删除。用馆名做地理编码只能猜到路或区的中心，误差可达数公里且无从发现 |
| 统一球场名 | `canonical_name` 改名为 `amap_name`，加唯一约束；重名冲突出现了再处理 |
| 手动修正后的通勤 | 不自动计算，提示用户回主页点「算通勤」 |
| 旧数据 | 一次性脚本迁移，不在 `init_schema` 里写兼容代码 |
| 前端组织 | 抽出 `static/common.js`，改用 ES Modules；本次不做 `<chip-select>` 组件 |

### 非目标

- 页面内嵌地图。
- 给统一球场存地址（只在高德搜索结果里显示地址）。
- 清理不再被引用的统一球场和通勤缓存。
- 同名不同馆的例外处理（按 `venue_key` 单独指定），出现了再加。
- 统一球场重名时自动加区名后缀。

---

## 2. 数据模型

三张表的关系：

```
games                          venue_aliases                    canonical_venues
───────────────────            ──────────────────────           ────────────────────
venue_name          平台原样馆名  platform_name_norm (主键) ◄─┐   id
platform_name_norm ──────────►  canonical_id ─────────────────►  amap_name  (唯一)
lat, lng   平台原始坐标          origin                           amap_id    (唯一)
                                updated_at                       lat, lng   (高德坐标)
```

### 2.1 `canonical_venues`（统一球场）

一行是一个真实地点，即一个高德 POI。

| 列 | 说明 |
|---|---|
| `id` | 主键。迁移时保留原值，通勤缓存键 `c:<id>` 继续有效 |
| `amap_name` | 高德 POI 名称，唯一（原 `canonical_name`） |
| `amap_id` | 高德 POI id，唯一，用于跨平台去重 |
| `lat`, `lng` | 高德 POI 坐标 |

删除原来的 `name_patterns`、`platform_ids` 两列。

### 2.2 `venue_aliases`（馆名对应表，新表）

一行是一个平台上出现过的馆名（规范化后）。每个出现过的馆名都有一行，包括还没归类的。

| 列 | 说明 |
|---|---|
| `platform_name_norm` | 主键。平台馆名经 `venues.norm_name` 规范化后的结果 |
| `canonical_id` | 归到哪个统一球场；`pending`、`none` 时为空 |
| `origin` | 这条对应关系的来源，见下表 |
| `updated_at` | 最后修改时间；`pending` 行用它显示「上次高德接口出错于何时」 |
| `last_search` | `pending` 行上次自动搜索的结果：空 = 还没搜过；`error` = 高德接口出错（网络、限流、key 等），下次「算通勤」重试；`empty` = 高德正常返回但没结果；`dup` = 搜到的球场与已有统一球场重名。`empty`/`dup` 不再自动搜，留在「待处理」由用户手动处理 |

| `origin` | 含义 | `canonical_id` | 自动归类的处理 |
|---|---|---|---|
| `pending` | 待归类：还没搜过，或自动搜索没归上（原因见 `last_search`） | 空 | 只重试没搜过的和 `error` |
| `auto` | 自动搜高德归的 | 有 | 不再处理 |
| `confirmed` | 自动归的，用户确认无误 | 有 | 不碰 |
| `manual` | 用户手动指定 | 有 | 不碰 |
| `none` | 用户确认高德搜不到，通勤保持未知 | 空 | 不碰 |

### 2.3 `games`（球局）

- 删除 `canonical_id` 列。球局属于哪个统一球场，查询时经 `platform_name_norm → venue_aliases → canonical_venues` 关联得出。手动修正对所有同名球局（含以后的新球局）立即生效，不回写 `games`。
- 新增 `platform_name_norm` 列，由 `upsert_games` 根据 `venue_name` 计算。
- `lat`、`lng` 只存平台原始坐标：闪动存平台自带坐标，中羽联恒为空。归类不再覆盖这两列。

### 2.4 删除的表

- `venues`（地理编码缓存）。

### 2.5 馆名规范化 `venues.norm_name(s)`

依次：Unicode NFKC 规范化（全角括号、全角字母转半角）→ 去首尾空格 → 连续空白合并为一个空格 → 英文转小写。

例：「堂颂羽毛球馆（淞南店）」与「堂颂羽毛球馆(淞南店)」、「Mook羽毛球馆」与「mook羽毛球馆」规范化后相同。

---

## 3. 归类与通勤流程

### 3.1 刷新入库（`sync.upsert_games`）

- 计算每场球局的 `platform_name_norm`。
- 按 `(source, source_id)` 整行更新（去掉上次为保留 `canonical_id`/`lat`/`lng` 加的逻辑：`canonical_id` 列已删除，`lat`/`lng` 只存平台原始坐标，新值覆盖即正确）。
- 对新出现的馆名 `INSERT OR IGNORE` 一行 `venue_aliases`，`origin = pending`。

### 3.2 算通勤（`sync.compute_pending`）

1. **归类**：取 `venue_aliases` 中 `origin = pending`、`last_search` 为空或 `error`，且当前 `games` 里有球局的馆名，逐个处理（按馆名，不按球局）：
   - 用该馆名下任一原样 `venue_name` 搜高德 POI，取第一条结果。
   - POI 的 `amap_id` 已存在于 `canonical_venues` → 归到该统一球场；否则新建统一球场。
   - 更新该行为 `origin = auto`、填 `canonical_id`。
   - 没归上 → 保持 `pending`，`last_search` 记 `error`（`poi_search` 抛 `AmapError`）、`empty`（返回空列表）或 `dup`（新建统一球场时 `amap_name` 唯一约束冲突），并更新 `updated_at`。
   - 高德返回每秒请求数超限（infocode 10021）时，`_amap_get` 等 1 秒重试，最多 3 次。
   - 进度文字：「归类场馆 i/n」，n 为待归类馆名数。
2. **算通勤**：需要通勤的场馆有两类：
   - 当前球局引用到的统一球场：坐标用统一球场的高德坐标，缓存键 `c:<id>`。（修正现有问题：现在用的是「第一场球局的坐标」，可能是闪动坐标。）
   - 没归到统一球场、但有闪动自带坐标的场馆：坐标用闪动坐标，缓存键 `v:<venue_key>`（与现在一致）。
   - 中羽联未归类、或 `none` 的中羽联场馆：不算，通勤保持未知。
   - 进度文字：「算各场馆通勤 i/n」（不变）。

### 3.3 `/api/games`

改为 `games LEFT JOIN venue_aliases LEFT JOIN canonical_venues`，返回字段中 `canonical_name` 改名为 `amap_name`（前端同步改）。`commute` 字段的取法不变：已归类用 `c:<canonical_id>`，否则用 `v:<venue_key>`。

### 3.4 `pending_count`（主页「还有 N 个场馆没算出」）

= 当前球局涉及的、还会自动重试的 `pending` 馆名数（`last_search` 为空或 `error`）+ 通勤三项未算齐的场馆数（场馆定义同 3.2 第 2 步）。

另有 `unmatched_count` = 当前球局涉及的 `last_search` 为 `empty`/`dup` 的馆名数，主页提示「N 个场馆高德没自动搜到，去「球馆管理」处理」。

公交规划接口正常返回但没有方案（多为距离太近）时，公交时间按步行时间算；只有接口出错才留空、下次重试。

### 3.5 删除的代码

- `venues.classify`（子串匹配）。
- `sync.enrich_venue` 中拿馆名做地理编码的兜底。`commute.geocode` 本身保留：家/公司地址（`/api/origins`）仍用它把地址换算成坐标，这是地理编码的正当用途。
- `sync.seed_canonical_venues`、`sync.load_canonical_list`；`config.json` 中 `canonical_venues` 配置项随之失效（README 同步更新）。
- `upsert_games` 中的保留逻辑（`_KEEP`）。
- `/api/config` 返回的 `canonical_names`（前端未使用）。

---

## 4. 球馆管理页

### 4.1 页面与入口

- 文件 `static/venues.html`，路由 `GET /venues`。
- 主页顶部加「球馆管理」链接；管理页顶部有链接回主页。

### 4.2 主体表格

每行一个 `venue_aliases` 记录。

| 列 | 内容 |
|---|---|
| 平台馆名 | 原样显示一个例子：该规范化名下球局最多的那种写法 |
| 来源 | 中羽联 / 闪动（两平台都有则都标） |
| 球局数 | 当前库里该馆名的球局数 |
| 高德 POI | `amap_name` + 「地图」链接（新标签页打开高德地图标出该坐标） |
| 状态 | 未归类 / 自动 / 自动·可疑（附原因）/ 已确认 / 手动 / 高德搜不到；未归类行显示上次搜索时间 |
| 操作 | 「确认无误」（仅 `auto`）、「修改」、「标记搜不到」、「恢复自动」 |

「恢复自动」= 把该行改回 `pending`、清空 `canonical_id`，下次「算通勤」重新自动归类，用来撤销误操作。

表格上方：

- 视图切换：**待处理**（默认，= `pending` 行 + 可疑的 `auto` 行）/ **全部**。
- 搜索框：按平台馆名或高德名过滤，支持拼音（复用 `pyMatch` 与 `/api/pinyin`）。

### 4.3 「修改」交互

点「修改」，在该行下方展开编辑区：

1. 搜索框，默认填入平台馆名，可改。
2. 输入时即时列出名字匹配的**已有统一球场**（支持拼音），点一个即归过去。
3. 「在高德搜索」按钮：点了才请求高德，列出前 10 个 POI，每个显示名称、所在区 + 地址、「地图」链接。点一个即归过去：该 POI 的 `amap_id` 已是某个统一球场则归到它，否则新建统一球场。
4. 保存后该行 `origin = manual`。若目标统一球场还没有通勤缓存，页面提示「这个场馆的通勤还没算，回主页点『算通勤』」。

### 4.4 接口

| 接口 | 说明 |
|---|---|
| `GET /api/venues` | 管理页整张表：每行的平台馆名示例、来源、球局数、`origin`、`updated_at`、统一球场（id、`amap_name`、坐标）、可疑原因列表、目标统一球场是否已有通勤 |
| `GET /api/amap/search?q=` | 转发高德 POI 搜索，返回前 10 条：`amap_id`、名称、所在区、地址、坐标。`commute.poi_search` 改为接受条数参数、返回列表（自动归类取第一条） |
| `POST /api/venues/assign` | `{platform_name_norm, canonical_id}` 或 `{platform_name_norm, poi: {amap_id, name, lat, lng}}`；保存为 `manual` |
| `POST /api/venues/confirm` | `{platform_name_norm}`：`auto` → `confirmed` |
| `POST /api/venues/none` | `{platform_name_norm}`：`origin = none`，清空 `canonical_id` |
| `POST /api/venues/reset` | `{platform_name_norm}`：改回 `pending`，清空 `canonical_id` |
| `POST /api/venues/assign` 的 `coord` | `{platform_name_norm, coord:{name, lat, lng}}`：手动坐标（高德 POI 搜不到时）。前端 `parseCoord` 只收两种输入：高德坐标「经度,纬度」（小数至少 4 位），或带 `uid=` 的百度地图网页链接（`@x,y` 为百度墨卡托 → BD-09 → 高德 GCJ-02）；后端校验在上海范围内，新建 `amap_id` 为空的统一球场，名称重名 409 |
| `POST /api/venues/restore` | `{platform_name_norm, origin, canonical_id, last_search, updated_at}`：撤销，整行写回操作前的状态（前端操作前记下）。管理页操作过的行在「待处理」里不消失、带「撤销」按钮，刷新页面后清空；撤销「改归到新 POI」不删已新建的统一球场 |

写接口对不存在的 `platform_name_norm` 返回 404，参数缺失或类型不对返回 400。`assign` 传入新 POI 时，若 `amap_name` 与已有统一球场重名而 `amap_id` 不同，唯一约束报错，接口返回 409 并提示重名（出现了再决定怎么处理）。

### 4.5 前端代码组织

- 新增 `static/common.js`（ES 模块）：`$`、`api`、`esc`、拼音缓存与 `ensurePinyin`。两个页面 `import` 使用。
- `static/filters.js` 改为 ES 模块（`export`），主页用 `<script type="module">` 引入；node 测试改为 `tests/filters.test.mjs`。
- 管理页的统一球场选择是单选，不用胶囊多选；本次不做 `<chip-select>` 组件。
- 样式沿用 `static/app.css`，管理页特有布局写在页面自己的 `<style>` 里，颜色引用变量。

---

## 5. 可疑规则

只对 `origin = auto` 的行判断，由 `GET /api/venues` 在服务端调用 `venues.suspect_reasons(platform_name, amap_name, flashx_ll, amap_ll)` 计算，返回原因列表（空 = 不可疑）。纯函数，不访问数据库和网络。

阈值作为常量写在 `venues.py` 开头，不做配置项。

### 5.1 分店不同

平台馆名和高德名都带括号，第一个括号内的内容规范化后不同，且互不包含。

- 抓：「堂颂羽毛球馆(淞南店)」→「堂颂羽毛球馆(大华店)」
- 不抓：「YONEX虎啸羽毛球馆(普陀馆)」→「YONEX虎啸球馆」（高德名无括号）

### 5.2 专名差异大

两边都去掉括号内容、通用词、标点，得到「专名」；两个专名的 bigram 集合 Jaccard 重合度低于 `0.2`，且互不包含。

通用词：羽毛球、羽球、体育、运动、中心、球馆、场馆、馆、综合、俱乐部、篮球、乒乓球、乒羽、健身、训练、培训、基地、公园、社区、上海、市、区、店、旗舰、校区。

- 抓：「静安体育馆羽毛球场」→「VJUMP微跃羽毛球篮球乒乓球馆旗舰店」
- 不抓：「万体汇」→「万体汇」
- 已知误报：「万体馆羽毛球馆（徐家汇体育公园）」→「徐家汇体育公园」，由用户「确认无误」处理。

### 5.3 闪动坐标相距远

该馆名下有闪动球局时，取这些球局 `lat`/`lng` 的中位数作为闪动坐标，与统一球场高德坐标的直线距离（haversine）超过 `1000` 米即可疑，原因文字写出距离（如「闪动坐标相距 4.1km」）。中羽联馆名不适用。

- 抓：「堂颂羽毛球馆(红光店)」（闪动坐标与大华店相距约 4 公里）

### 5.4 预期效果

在 2026-10-14 的数据上，规则 5.1 + 5.2 合计约 55 个，大部分为真错误；5.3 另补闪动场馆。误报约 6–8 个。

---

## 6. 数据迁移（一次性脚本，不进代码库）

`init_schema` 只建新结构。现有 `games.db` 用一次性脚本迁移，脚本跑完即删：

1. 备份 `games.db`。
2. 由现有 `games` 的「`venue_name` → `canonical_id`」生成 `venue_aliases`：按 `norm_name` 分组；有归类的写 `auto`（同一规范化名对应多个统一球场时取球局数最多的），没归类的写 `pending`。
3. 重建 `canonical_venues`：`canonical_name` → `amap_name`，删 `name_patterns`、`platform_ids`，保留 `id`。
4. `games` 删 `canonical_id`、加并填好 `platform_name_norm`；删 `venues` 表。

闪动球局的 `lat`/`lng` 在下次刷新后自然变回平台原始坐标，脚本不处理。

---

## 7. 测试

- `venues.py`：`norm_name` 的全角/半角、大小写、空白；三条可疑规则各用第 5 节的真实例子测「抓」与「不抓」。
- 归类（`compute_pending`，mock `poi_search`）：搜到 → `auto`；搜不到 → 保持 `pending` 且 `updated_at` 更新；`amap_id` 已存在 → 复用统一球场；不碰 `manual`/`confirmed`/`none`；同名多场球局只搜一次。
- 入库：新馆名插入 `pending`；`lat`/`lng` 为平台原始坐标；已有馆名的 `venue_aliases` 行不被改动。
- 通勤：统一球场用高德坐标；未归类闪动场馆用闪动坐标；中羽联未归类不算。
- 接口：`/api/games` 返回 `amap_name`；`assign` 的三种情况（已有统一球场 / 新 POI / 已存在 `amap_id` 的 POI）；`confirm`、`none`、`reset`；`/api/venues` 的可疑原因与「是否已有通勤」；参数错误 400、不存在 404。
- 前端：`tests/filters.test.mjs` 通过；在测试实例（`games.db` 副本）上实际操作管理页并截图。
- 文档：更新 README 的数据表说明、功能列表与目录结构。
