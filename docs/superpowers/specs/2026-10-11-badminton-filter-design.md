# 球局筛选工具 — 设计文档

日期：2026-10-11
状态：已通过 brainstorming，待写实现计划
接口与抓包结论见同目录外的 `RESEARCH.md`（本文件不重复接口鉴权细节）。

---

## 1. 目标与范围

把**中羽联**和**闪动FlashX**两个平台的上海羽毛球球局拉到一起，按用户自己的条件筛选。两平台各自的筛选太弱，这个工具补足。

- **形态**：本地工具。运行后浏览器打开 `localhost`，左边筛选、右边列表，可排序、看详情。
- **使用**：随查随用，手动刷新。只有用户本人使用。
- **报名**：不在工具内报名，筛出后跳回原平台。
- **城市**：上海。出发地两个固定点：**家**、**公司**。

### 非目标（明确不做）
- 定时抓取、推送、服务器部署。
- 水平筛选、场地数筛选（已确认删除：中羽联无结构化字段、口径不一，成本不值）。
- 中羽联 `day=3`（两天以后）——只要今天/明天/后天。
- 中羽联 `info` 接口（要签名，不碰；日期靠 `day` 推算即可，无需 `info`）。

### 已确认决策

| 项 | 决定 |
|---|---|
| 技术栈 | Flask + 标准库 `sqlite3` + 原生 HTML/JS |
| 凭证管理 | 方案 C 半自动：平时不挂代理，失效时点按钮临时拉起 mitmdump 抓一次 |
| 通勤 | 高德 API（key 读环境变量 `GAO_DE_MAP_API_KEY`），驾车+公交都算；文字地址→地理编码→坐标 |
| 数据流 | 先拉进 SQLite，再在前端本地筛选；手动刷新 |
| 场馆归一 | 用户维护「规范场馆表」，界面双名显示；跨平台按规范馆合并通勤。纠错交互留后续迭代 |
| 筛选·场馆 | 双向：只看 / 不看某些场馆（按场馆名包含匹配） |
| 筛选·关键字 | 双向：只找 / 排除（对 `raw_text` 包含匹配） |

---

## 2. 项目结构

```
badminton-filter/
  app.py            # Flask 入口 + 路由；启动后自动开浏览器
  config.json       # 用户维护：家/公司地址、规范场馆表、端口、默认关键词预设
  db.py             # SQLite 建表 + 读写
  sync.py           # 「刷新」：调两平台 → 归一化 → 入库 → 补坐标/通勤
  platforms/
    zhongyulian.py  # 中羽联接口封装（indexList，day=0/1/2，两个列表+翻页）
    flashx.py       # 闪动FlashX接口封装（activity/list，按天，翻页）
  commute.py        # 高德：地址→坐标、坐标→通勤时间，带缓存与节流
  creds.py          # 凭证读写 + 失效检测 + 临时拉起 mitmdump 抓取
  static/index.html # 前端单页：筛选表单 + 列表 + 详情（筛选/排序在此做）
  creds.json        # 凭证（600 权限，.gitignore）
  games.db          # SQLite（.gitignore）
```

后端刻意薄：只负责「刷新入库」「返回全量球局」「凭证抓取」。筛选、排序全在前端。

---

## 3. 数据流

1. `python app.py` → 起 Flask + 自动打开浏览器到 `localhost:<端口>`。
2. 点**「刷新」** → `POST /api/refresh` → `sync.py`：
   - 调中羽联（day=0/1/2）+ 闪动FlashX（对应三天）列表接口；
   - 归一化成统一「球局」记录，`INSERT OR REPLACE` 入库；刷新前删 `date < 今天`；
   - 每个场馆归类到规范馆（见 §5）→ 缺坐标的补地理编码、缺通勤的调高德（见 §6）；
   - 凭证失效 → 中断并返回错误，前端提示抓凭证（见 §7）。
3. 前端 `GET /api/games` 一次拿当前库里全部球局（每条已带四个通勤值），**筛选/排序在前端 JS 做**，改条件零延迟。
4. 点球局 → 详情 + 回原平台报名链接。

平台接口只在刷新时调用；通勤按规范馆缓存，几乎不重复调高德。

---

## 4. 数据模型（SQLite）

```sql
-- 球局：刷新写入，查询读取。(source, source_id) 唯一去重
games(
  id INTEGER PRIMARY KEY,
  source TEXT,            -- 'zhongyulian' | 'flashx'
  source_id TEXT,
  title TEXT, date TEXT,  -- date = YYYY-MM-DD
  start_time TEXT, end_time TEXT,   -- HH:MM
  duration_min INTEGER,
  venue_name TEXT,        -- 管理员填的原始名
  venue_key TEXT,         -- source + 平台场馆ID（address_id / placeId）
  canonical_id INTEGER,   -- 命中规范馆则关联，否则 NULL
  price REAL, applied_num INTEGER, total_num INTEGER,
  lat REAL, lng REAL,     -- 闪动自带；中羽联地理编码补；命中规范馆用规范坐标
  raw_text TEXT,          -- 标题(+描述)，关键字匹配用
  tags TEXT,              -- JSON 字符串（闪动 displayTags 等）
  organizer TEXT, club_name TEXT,
  detail_ref TEXT,        -- 回原平台所需 id
  UNIQUE(source, source_id)
)

-- 规范场馆表：用户手动维护（也可从 config.json 载入后写库）
canonical_venues(
  id INTEGER PRIMARY KEY,
  canonical_name TEXT,    -- 我们维护的名字
  lat REAL, lng REAL,     -- 权威坐标
  name_patterns TEXT,     -- JSON 数组，名字包含规则
  platform_ids TEXT       -- JSON 数组，可选的精确平台场馆ID
)

-- 自动发现的场馆坐标缓存（未归类场馆用）
venues(venue_key TEXT PRIMARY KEY, name TEXT, lat REAL, lng REAL,
       address TEXT, geocoded_at TEXT)

-- 通勤缓存：key 优先 canonical_id，未归类用 venue_key
commute(origin TEXT, place_key TEXT,   -- origin='home'|'company'; place_key='c:<id>'|'v:<venue_key>'
        drive_min INTEGER, transit_min INTEGER, computed_at TEXT,
        PRIMARY KEY(origin, place_key))

-- 出发地坐标：地址来自 config，地理编码后缓存；地址变则清 commute
origins(key TEXT PRIMARY KEY, address TEXT, lat REAL, lng REAL)
```

### 归一化映射

| 统一字段 | 中羽联 | 闪动FlashX |
|---|---|---|
| source_id | `activity_id` | `id` |
| title / raw_text | `name` | `activityTitle`（+详情 `des` 若取） |
| date | 由 `day` 推（0今/1明/2后，基准=运行当天） | `activityDate` |
| start/end_time | `time` 拆 `19:00~21:00` | `startTime` / `endTime` |
| duration_min | end − start | 同左 |
| venue_name / venue_key | `address` / `address_id` | `activityAddress` / `placeId` |
| price | `price` | `showCost` → 数字 |
| applied / total | `joinstate` 拆 `7/18` | `applyMemberNum` / `needMemberNum` |
| lat/lng | 无 → 地理编码 | `latitude` / `longitude` |
| tags | 无（靠关键字从 raw_text 识别） | `displayTags` |

**归一化要点**
- 中羽联两个列表 `my_club_movement_list`（一次全返回）+ `other_club_movement_list`（每次 10 条，`skip` 翻页拉完），合并后靠 `UNIQUE` 去重。
- 闪动按 `pageNo` 翻到 `pages`。
- `day → date`：以运行当天为基准 +0/+1/+2。

---

## 5. 场馆归类（规范场馆表）

刷新时每个球局的场馆按序匹配规范馆：
1. 先按 `platform_ids` 精确匹配（平台场馆ID）；
2. 再按 `name_patterns` 对 `venue_name` 包含匹配。
3. 命中 → `games.canonical_id` 关联该规范馆，`lat/lng` 用规范坐标，通勤按 `c:<id>` 缓存（**跨平台自动合并**，中羽联和闪动的同一个馆共用一份通勤）。界面双名显示：`管理员名（规范名）`。
4. 未命中 → 「未归类」：坐标走兜底（闪动自带 / 中羽联地理编码），通勤按 `v:<venue_key>` 缓存，界面只显示管理员名。

用户初期按名字维护 `name_patterns`（界面上看到的就是名字）；名字太乱的馆再补 `platform_ids`。

**后续迭代（不在 MVP）**：界面上点一下把未归类场馆归到某规范馆的纠错交互。MVP 先手动编辑 `config.json` 里的规范场馆表。

---

## 6. 通勤计算（高德）

- **地理编码** `/v3/geocode/geo`（`city=上海`）：仅未归类且缺坐标的中羽联场馆用。
- **路径规划**：驾车 `/v3/direction/driving`、公交 `/v3/direction/transit/integrated`（带 `city`）→ 耗时（秒）转分钟。
- ⚠️ 高德坐标顺序是 **`经度,纬度`（lng,lat）**，与直觉相反；闪动字段 `latitude/longitude` 拼接时摆对。统一在 `commute.py` 封装。字段的精确 JSON 路径实现时以高德官方文档为准（用 find-docs 查）。

**缓存命中**（刷新时对每个场馆）
- 坐标已知（规范馆 / `venues` 命中 / 闪动自带）→ 跳过地理编码。
- `commute` 查 `(home, place_key)`、`(company, place_key)`：有则跳过；无则对家、公司各调驾车+公交，写入。
- 每个新场馆最多 1 次地理编码 + 4 次路径规划；老场馆 0 次。

**健壮性**
- 节流：多个新场馆时串行 + 小间隔，避开高德 QPS 限制。
- 失败不阻塞：某场馆地理编码/路径规划失败 → 通勤置 NULL，界面显示「通勤未知」，不影响其他球局。
- 出发地变更：`origins` 存地址原文；启动比对 `config.json` 地址，变了则清 `commute` 重算。

---

## 7. 筛选与前端

**后端**：`GET /api/games` 返回当前库里全部球局（每条带 `home/company × drive/transit` 四个通勤值 + 全部字段）。**筛选/排序全在前端 JS。**

**筛选项**（前端对拿到的数据做）

| 条件 | 筛法 |
|---|---|
| 日期 | 今/明/后/全部 |
| 开始时间 | 范围（如 18:00–21:00） |
| 时长 | `duration_min` 范围（如 ≥120） |
| 通勤 | 选 家/公司 + 驾车/公交 + 上限分钟；通勤未知默认保留（可开关隐藏） |
| 场馆 | 双向：只看 / 不看某些场馆（按 管理员名+规范名 包含匹配） |
| 价格 | ≤ 上限 或范围 |
| 已报名人数 | 范围；「还有空位」= 已报 < 总 |
| 总人数 | 范围 |
| 关键字 | 双向：只找 / 排除（对 `raw_text` 包含匹配） |

**排序**：开始时间 / 通勤 / 价格 / 剩余空位（总−已报）/ 已报名。点列头或下拉切换，前端即时排。

**界面**（`static/index.html`，原生 JS 单页）
- 顶栏：出发地选择（家/公司）、「刷新」、凭证状态条（失效变红 + 「抓取凭证」）。
- 左栏：上述筛选表单；场馆与关键字各有「只看/不看」两个输入框，初值从 `config.json` 默认预设填入、可临时改（如临时「只找练球场」）。
- 右栏：列表。每行：时间（日期+起止）、时长、场馆双名、当前出发地通勤（驾车/公交）、价格、已报/总、来源平台标、标签。点行展开详情：完整描述、组织者、俱乐部、联系方式、回原平台报名链接。
- `config.json` 存不变的东西（出发地地址、规范场馆表、端口、默认关键词预设）；界面改筛选是临时的，不回写（回写留后续迭代）。

---

## 8. 凭证半自动流程（方案 C）

- **存储**：`creds.json`（600 权限、`.gitignore`），存中羽联 `skey` + 闪动 `x-access-token` 及各自必要请求头。
- **失效检测**（刷新时）：中羽联 `ok:-2`（排除城市为空的情况）→ skey 失效；闪动 401 / 鉴权错误码 → token 过期。任一失效 → 刷新中断，`/api/refresh` 返回「凭证失效」，前端顶栏变红 + 「抓取凭证」。
- **临时抓取**（点按钮时，`creds.py`）：
  1. 后端拉起 `mitmdump --mode local:WeChat` 子进程，挂精简 addon：只认两平台域名，提取 `skey`/`x-access-token` + 请求头写入 `creds.json`，抓到即停。
  2. 前端提示用户打开失效的那个小程序刷一下。
  3. 抓到 → 停子进程 → 提示「已更新，可重新刷新」。
  4. 超时（~90s）→ 停子进程、提示重试。
- **前置检查**：首次抓取前确认 `mitmdump` 存在、CA 证书仍被信任（RESEARCH.md 记录的环境）；缺则提示如何装/信任，不硬失败。
- **注意**：抓取只在主动点按钮时发生，平时不挂代理。`sing-box`/VPN tun 若开着可能抢流量，抓不到时提示临时关一下。

---

## 9. 测试策略

纯逻辑、离线可测的部分留 runnable 自检（`assert` 式，无框架）：
- **归一化**：中羽联 `time` 拆分、`joinstate` 拆分、`day→date`、闪动 `showCost→数字`、`duration` 计算。
- **场馆归类**：`platform_ids` / `name_patterns` 匹配，命中与未命中。
- **筛选逻辑**：若筛选在前端 JS，留一组样例数据手测；关键匹配逻辑（关键字双向、场馆双向、通勤上限、空位）各一条断言。

涉及网络的（两平台接口、高德、mitmdump）不写自动化测试，用真实凭证手动验证一次刷新全流程。

---

## 10. 合规

用用户自己的登录态、看自己能看到的球局、频率极低。请求克制（加间隔、不并发猛拉）。`creds.json`、`games.db`、任何抓包文件都进 `.gitignore`，不入库。
