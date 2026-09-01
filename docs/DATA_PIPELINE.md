# StockBoard 数据管道全景文档（取数 / 存数 / 长期存储方案）

> 用途：交接文档 + 日常运维手册 + **实施进度实时登记**（见 §0）。2026-08-30 梳理，08-31 开始实施。
> 涉及目录：`jiarenmens/`（采集端 Python）、`stockboard-app/`（前端 Vue）、`.github/workflows/`（调度）。

---

## §0 实施进度（随时更新，交接先看这里）

| 事项 | 状态 | 说明 |
|---|---|---|
| 文档 | ✅ 完成 | 本文件；含实施调试记录 |
| ① 存量数据上云 | ✅ **完成并验证** | Release `db-state`：crawl-latest.db.gz 22.1MB（trades 203100 / positions 166045 / players 23192，范围 2026-07-22~08-30，integrity ok）；`db-m2026-07`：5.4MB。匿名 `curl -L` 下载实测通过。`db-m2026-08` 按设计当月不封版（9 月首个收盘 run 自动封入冷层） |
| ② crawl.yml 改造 | ✅ **全链路实战验证通过** | 09-01 15:52 run 完成首次真实 sync：热层(09-01, trades 207046) + 温层 db-w2026-W36 + 冷层 db-m2026-08(159315 trades) 全部就位，manifest integrity ok。⚠️ 同时发现并修复调度缺口（闸门 15:10 > cron 末班 14:51，08-31 db 丢一天，见事故记录②）：新增 `crawl-eod` 收盘专班 + 14:45 兜底闸门 |
| ③ fetch_db.py 回测取数 | ✅ **端到端验证通过** | `--list` / `--latest`(85MB 全量) / `--month 2026-07`(43785 trades, integrity ok) / `--range` 合并 全部实测；大陆 SSL 掐流已用 3 次退避重试 + curl 兜底解决 |
| ⑤ players 导出收窄 | ✅ **远端已验证** | 23192 个/92MB → 5133 个（优质 3901 ∪ 当日持仓/调仓 ∪ name_map 引用）；08-31 01:07 run 后远端目录实测 5133，core.json 完整(quality 3895)。之后每日导出自动淘汰跌榜冻结选手 |
| ④ git 历史重写(filter-repo) | ⏸ 待用户确认 | 前置条件①已满足；会重写全部 commit hash，需 force push。详见下方待办 C |
| 观察期 ⑥ | ⏳ 进行中(第1/5天) | 已核对: 00:56 与 01:07 两次 run 数据完整、导出收窄生效；余项见下方待办 B |

---

## 待办清单（按优先级，含验收标准；随进度更新）

### A.【✅ 已完成】首次 sync 上传验证 — 09-01 15:52 run（09-01 复核通过）

- Release 三层全部就位：热层 `db-state` crawl-latest.db.gz 22.5MB（trades 207046，range 含 09-01，integrity ok）；温层 `db-w2026-W36`；冷层 `db-m2026-08`（159315 trades，08-01~08-30）——**月初自动封版按设计触发**。
- 复核中发现**调度缺口**（见事故记录②）：闸门 15:10 高于 cron 末班 14:51，08-31 的 db 数据从未持久化而丢失一天。已修复：`crawl-eod` 收盘专班事件 + 14:45 兜底闸门。

### B.【连续 5 个交易日】观察期每日检查（09-02 ~ 09-08）

每天收盘 run 后核对四项：
1. `crawl-latest.manifest.json` 行数较昨日单调不减、date_range 尾部=当日；
2. 页面选手数据模块正常（重仓共识/抄作业有数据，quality ~3900 量级）；
3. 钉钉日报正常（无重复推送、无漏推）；
4. 周五出现当周温层 tag；**配好 cron-eod 后每日 15:15 的收盘专班 run 应含"上传 Release 存储"步骤 success**。

任一异常 → 先查 Actions 日志，再按 §8 运维手册处理。

### C.【需用户明确确认】④ git 历史重写（filter-repo）

- 现状：`.git` 772MB（size-pack 748MB），历史里有 275 份 db blob（打包前 13.7GB）；不改写则 clone 永远拖着 700MB+ 死重。
- 操作：`git filter-repo --invert-paths --path jiarenmens/data/crawl_data.db`（可选顺带清理 latest/players/ 历史大目录）→ `git push --force`。
- 影响：所有 commit hash 重写；本地旧 clone/fork 需重新 clone；旧 PR 引用失效。数据零损失（db 已在 Release 三层）。
- 效果：`.git` 预计 772MB → <100MB。
- 前置条件已满足（①归档可匿名下载已验证）。**等用户一句"做④"即可执行。**

### D.【安全】PAT 更换

WXinYi 的 classic PAT 出现过在会话/配置记录中，稳定运行后建议 GitHub → Settings → Developer settings → Tokens 里 **revoke 并换新**；日常 CI 全部用仓库自带 GITHUB_TOKEN，本地调试才需要 PAT。

### E.【可选优化】后续观察项

- **09-02（或下次复盘）确认**：`db-w2026-W36` 周档只含 09-01 而缺 08-31——08-31 为休市日还是采集空转待确认（index.json 有 08-31 但 trades/positions 为 0 行；若为采集问题需查 rtV2 当日返回）。
- `summary.json`（117KB 全量参照）前端已不 fetch，观察一个月后可考虑停写，进一步减小每次提交体积；
- 温层 `--retain-weeks 12` 与 prune 40 采集日的衔接：若出现周档覆盖不到的边角日期（跨月边界），用 `fetch_db.py --range` 合并月档兜底。

---

### 📌 需用户操作：cron-job.org 增配收盘专班（一次性，2 分钟）

现有 cron 末班 14:51 北京时间，早于 15:00 收盘——sync 只能靠 14:45 兜底闸门拿到"准收盘"快照。要拿到**真收盘后**的数据（含 15:00 收盘竞价与最终持仓），请在 cron-job.org 增配一个专班：

1. 登录 cron-job.org → 复制现有的 crawl 任务；
2. 执行时间改为**工作日 15:15（Asia/Shanghai）**；
3. 请求体中 `event_type` 从 `crawl` 改为 **`crawl-eod`**，URL/token 不变。

该专班触发的 run 会无条件执行 Release sync（不走时间闸门），且不影响午盘/尾盘推送（时间窗守卫各自独立）。**未配置时也不影响系统运行**——14:45 兜底闸门保证每天仍有落盘。

---

### ⚠️ 事故记录（08-31 凌晨，已修复，后人必读）

**空库导出污染页面**：迁移切换当晚，一次白天逻辑的 run 在 db 已移出 git、热层下载又被"白天跳过"闸门挡住的情况下，用**空库**跑了 export_json，产出退化数据（index 2148 人/quality 886/无持仓变动）提交并部署。

- **根因**：白天跳过下载的设计假设"白天 run 不需要历史库"，但 export_json 的持仓变动/卖出预警/高手判定全部依赖 40 日历史，空库导出即数据污染。
- **修复**（commit `🐛 白天run也必须恢复热层`）：**每次 run 一律恢复热层**（下载失败且无本地 db 才终止）；只有"上传"保留收盘闸门(当时≥15:10, 后降为14:45)。`--download-latest` 加 3 次退避重试。
- **教训**：改存储架构时，"读路径"（每次 run 都要）和"写路径"（收盘一次）必须分开考虑闸门。

**② 收盘 sync 闸门高于 cron 末班，08-31 db 数据丢失一天（09-01 复核时发现，已修复）**

- **现象**：09-01 复核首日 sync 时发现热层 trades 无 2026-08-31 行（08-30 直接跳 09-01），温层 W36 只含 09-01。
- **根因**：sync 闸门设为 ≥15:10，但 cron-job.org 末班 dispatch 是 14:51（08-29 前的课表含晚间班次，08-31 起被裁剪）——**收盘 sync 从未被 cron 自动触发过**。08-31 的采集数据只存在于当天 runner 临时磁盘，run 结束即蒸发。恰逢迁移切换当晚，巧合掩盖了问题；09-01 的 sync 只是碰巧被 15:52 的一次 push 触发。
- **修复**：①新增 `crawl-eod` 事件类型（专用收盘 cron 15:15 触发，无条件 sync）；②兜底闸门降为 ≥14:45（末班车也能落盘）。两条路独立生效。
- **代价**：~~08-31 的 positions/trades 快照无法回补~~ → **09-01 已回填完成**（见下 ②-b）
- **教训**：时间闸门必须对照**实际触发源的时刻表**校验；"理论上会有一班车"不等于"课表里有这一班"。

**②-b 08-31 断档已回填（09-01 完成）**

源接口只反映当前、无法重采 08-31，但**每次 run 的导出 JSON 都进了 git**——用 08-31 最后一次导出（commit `e35897d2ef`，北京 14:57）完成重建：
- 工具：`scripts/backfill_positions.py --commit <sha> --date <日>`（通用化，未来断档可复用）。从 git archive 解出当时 players/*.json，`p` 数组→positions、`t` 中 td==当日→trades（字段与 schema 一一对应），先删后插幂等，并给 149 名"当日新面孔"补最小 players 档案（外键/名称查询需要）
- 回填量：positions 3054 行 + trades 1760 行；integrity ok；采集日恢复连续（07-22~09-01）
- **三层已刷新**：热层（208808 trades）、温层 W36（08-31~09-01）、冷层 M08（08-01~08-31 完整封版）——一年后回测 `fetch_db.py --month 2026-08` 可完整取到 08-31
- 局限：快照时点为 14:57（差收盘 3 分钟）；update_time/position_value 等无源字段置空/0；players 表未回填历史收益（回测读各日 index.json）
- 防复发：`release_db.py` 已加**内容指纹跳过**——已完成月/周内容不变则跳过上传（冷层不再每晚全量重传，5 年后每晚真实上传恒定 ~25MB）；回填改变指纹时对应归档自动重传

### 调试记录（db_upload.yml 首次上云踩坑，供后人参考）

1. **同一 commit 删库导致 checkout 无 db** → init 工作流改为"优先热层恢复，否则从 git 历史最后一个含 db 提交检出"（`git rev-list | cat-file -e` 探测）。
2. **读操作强制要 token** → `release_db.py` 拆分 `_token()`（写）/`_opt_token()`（读，公开仓匿名 GET）。
3. **actions/checkout@v4 默认 shallow**（fetch-depth:1）→ rev-list 查不到历史，init 工作流加 `fetch-depth: 0`。
4. **cd 子目录后 git pathspec 失效**：`cd jiarenmens` 后 `git rev-list -- jiarenmens/data/...` 相对 cwd 解析不到 → fatal，`bash -e` 直接终止步骤。git 命令必须在仓库根目录执行。
5. 本机 keychain 里的 GitHub token 属于另一账号（无本仓写权限），不可用；改用仓库 GITHUB_TOKEN 跑 init。

### 环境备忘

- 仓库实测为 **public**（`private=false`），Release 资产可匿名下载（已验证）；用户配置标注"私有"以 API 实测为准。
- WXinYi 的 classic PAT 已提供（用于 API 调试与 gh CLI）。**安全建议**：该 token 已出现在会话记录中，稳定运行后建议在 GitHub → Settings → Developer settings 里 revoke 并换新。
- 大陆直连 GitHub：API 域名偶发 SSL 瞬断（重试可过），S3 资产主机（objects.githubusercontent.com 302 跳转后）对 Python urllib 的 TLS 指纹掐流较狠，curl 通常能过 → fetch_db/release_db 均已带重试，fetch_db 另有 curl 兜底。
- 回测归档产物在 `jiarenmens/data/archive/`（已 .gitignore），勿 `git add -A` 误提交（08-31 曾误提交 2 个 19MB 文件，已及时移除）。

---

## 一、全流程总览（✅ 08-31 起为现状）

```
cron-job.org (交易日 12 次/天, 每次 dispatch)
        │ repository_dispatch: crawl
        ▼
GitHub Actions crawl.yml (job: crawl)
        │
        ├─ ① 午盘格局推送   (13:00–13:20 窗口, cycle_push.py --session midday)
        ├─ ② 下载热层库     release_db.py --download-latest (每次run必做, 3次重试;
        │      失败且无本地db → run终止, 防空库断链)
        ├─ ③ 数据采集       main.py --checkpoint-reset
        │      取数: 东财大赛 rtV1 榜单 + rtV2 选手详情/持仓/调仓
        │      存数: jiarenmens/data/crawl_data.db (SQLite)
        ├─ ④ 人气榜快照     auction_scan.py --hot-rank → hot_rank.db
        ├─ ⑤ 导出 JSON      export_json.py → stockboard-app/public/data/
        ├─ ⑥ 构建 Vue       npm ci && npm run build
        ├─ ⑦ 钉钉通知       notify_daily.py (增量, 状态写 last_notify_state.json)
        ├─ ⑧ 清理过期采集日 prune_crawl_db.py --apply (保留 40 个采集日 + VACUUM)
        ├─ ⑨ 上传Release    [crawl-eod专班 或 ≥14:45收盘run] release_db.py --sync
        │      热层覆盖 + 当周温层 + 已完成月冷层 + 温层滚动清理; 失败即run失败
        ├─ ⑩ 提交数据       git add → reset crawl_data.db(db永不进git) → push JSON+状态
        └─ ⑪ Upload Pages artifact
        ▼
deploy job → GitHub Pages (https://wxinyi.github.io/stockboard)
```

另有独立机器：
- **竞价扫描** `auction_scan.py`（09:25 cron 单独触发，写 `auction.db`，钉钉推候选）；
- **盘中监控** `intraday_monitor.py`（本机 LaunchAgent 09:26–15:10，写 `intraday.db`，本地独享不提交）；
- **尾盘格局** `cycle_push.py --session eod`（14:30–14:55 窗口，搭 crawl dispatch 便车）。

---

## 二、取数（数据从哪来）

### 2.1 触发机制

| 触发方式 | 说明 |
|---|---|
| `repository_dispatch: crawl` | 主通道。cron-job.org 在交易日定时调 GitHub API，**每天 12 次**（盘前竞价~收盘后） |
| `push: main` | 每次推送也会跑一遍（数据照采，JSON 照导出） |
| `workflow_dispatch` | 手动触发（Actions 页面 Run workflow） |

> 并发控制：`concurrency: pages-${{ github.ref }}`，新 run 会取消正在跑的旧 run。白天高频 dispatch 是设计使然（盯盘时效），非异常。

### 2.2 数据源清单

| 数据 | 接口 | 文件 | 说明 |
|---|---|---|---|
| 选手榜单 | `https://emdcspzhapi.dfcfs.cn/rtV1` (`rt_get_rank`) | `src/spiders/player_list.py` | 总/年/月/周/日 5 类榜单，每榜 `--limit`(默认500)名，页大小20 |
| 选手详情/持仓/调仓 | `https://emdcspzhapi.eastmoney.com/rtV2` (POST, appKey=eastmoney) | `src/spiders/api_detail.py` | 一次调用拿全三样；无鉴权，固定 timestamp；重试3次 |
| 竞价候选 | 开盘啦实时接口 (apphwhq: GetBKJJ_W36 / RealRankingInfo / MorningBiddingList 等) | `scripts/auction_scan.py` | 当天走实时路径，历史回放走 His 路径 |
| 人气榜 | 东财人气榜 TOP100 | `scripts/auction_scan.py --hot-rank` | am/pm 每(date,snap)去重，最多两份/天 |
| 实时涨停池/格局 | 开盘啦 + 东财实时 | `scripts/cycle_push.py` | 只推钉钉，不落数据库 |
| 监控行情 | 盘口五档轮询 | `scripts/intraday_monitor.py` | 本机独享 |

**关注选手名单**：`main.py` 顶部 `WATCHED_PLAYERS`（11 人，硬编码 zh_id+name）。每次采集强制重抓、置于队列最前，且不参与 checkpoint 跳过。改名单只改这一处。

### 2.3 采集过程（main.py）

1. 写 `data/crawl_start.txt`（北京时间起点，供导出/通知引用）。
2. 拉榜单 → 合并去重 → 强制插入 `WATCHED_PLAYERS` 至最前。
3. `asyncio` 并发 20 (`--workers`)，`--test` 只跑 10 人。
4. 每完成 50 人 flush 一批入库 + 存 checkpoint（`data/checkpoint.json`）；SIGINT/SIGTERM 时也先落库再退。
5. **Workflow 传 `--checkpoint-reset`**：每次 dispatch 全量重采（不做断点跳过），保证盘中每次都是最新快照。

---

## 三、存数（数据存到哪）

### 3.1 主库 `jiarenmens/data/crawl_data.db`（SQLite, WAL 模式）

| 表 | 内容 | 写法 | 增长 |
|---|---|---|---|
| `players` | 选手档案（收益/净值/labels/ranks 等，详情 JSON 串） | UPSERT by zh_id | 慢（23192 人 ≈7MB） |
| `positions` | 每选手每采集日的持仓快照 | **幂等**：先 `DELETE WHERE zh_id=? AND crawl_date=?` 再 INSERT | ~40MB/40日 |
| `trades` | 调仓流水（带 _id） | 同上幂等 | ~40MB/40日 |

- 当前行数（2026-08-30）：trades 20.3万 / positions 16.6万 / players 23192；库 85MB，gzip 后 **22MB**（压缩比 3.8:1）。
- 每天新增约 2.5~3MB（≈40 万行级采集日快照）。
- **`prune_crawl_db.py --apply`**：按 trades 的 crawl_date 排序，保留最近 **40 个采集日**（KEEP_DATES），窗口外 DELETE + `VACUUM`（否则不缩文件）。players 表不删（被外键级联引用且很小）。db 封顶 ≈90MB。

### 3.2 辅助库（均在 `jiarenmens/data/`）

| 库 | 写入方 | 内容 | 是否提交 git |
|---|---|---|---|
| `auction.db` | auction_scan.py | 竞价候选池/漏斗/连板梯队/情绪/结果标签/日K因子 8 张表 | **是**（随 crawl 提交，8MB） |
| `hot_rank.db` | auction_scan --hot-rank | 东财人气榜 am/pm 快照 | 是 |
| `intraday.db` / `analysis.db` | intraday_monitor.py | 盘中信号快照 | **否**（.gitignore，本机独享） |
| `crawl_data.db-shm/-wal` | SQLite WAL | — | 否（.gitignore） |

### 3.3 导出 JSON（存数的"前端镜像"，`scripts/export_json.py`）

每次 run 从 crawl_data.db 导出到 `stockboard-app/public/data/`（前端 Pages 直接 fetch）：

```
index.json              — 有效日期列表
latest/core.json        — 日期/爬取时间/高手数 等元信息
latest/copy.json        — 抄作业信号 + 卖出预警 + 疑似清仓
latest/stocks.json      — 重仓共识
latest/name_map.json    — 被引用选手 name→id
latest/changes_summary.json — 持仓变动计数
latest/summary.json     — 全量聚合(调试参照, 前端不再 fetch)
latest/players/<id>.json — 选手详情, 前端按需加载
  (✅ 08-31 起: 只导出"优质∪当日活跃∪被引用"集合并自动清理集合外旧文件,
   曾累积 23192 个/92MB 的问题已根治, 远端实测已降至 5133 个)
latest/auction.json     — 竞价扫描快照(intraday_monitor 也读)
latest/players_index.json
```

### 3.4 Git 提交策略（✅ 08-31 已切换）

- 每次 run `git add -f stockboard-app/public/data/ jiarenmens/data/`（-f 覆盖部分 ignore），但**随即 `git reset` 掉 `crawl_data.db`——db 永不进 git**（.gitignore 已加，`git rm --cached` 已做）。
- db 的持久化走 Release 三层存储（§5）：每次 run 开头从热层恢复，收盘 run（`crawl-eod` 专班或 ≥14:45 兜底）末尾 `--sync` 回传。
- 状态 JSON（last_notify_state/checkpoint 等）**必须每次提交**，否则钉钉增量推送会因状态回退而白天重复推送。
- 提交信息 `📊 数据更新 YYYY-MM-DD [skip ci]`（防止 push 再触发 workflow）；push 失败重试 3 次（pull --rebase）。
- `jiarenmens/data/archive/`（fetch_db 回测产物）已 ignore，勿 `git add -A` 误提交。

### 3.5 前端消费链路

前端只读 `stockboard-app/public/data/` 下的静态 JSON（Pages 无后端）；个股行情/K线/分时走浏览器直连东财/腾讯 JSONP（`stockboard-app/src/utils/eastmoney.js`、`stockSearch.js`），与采集管道无关。**改采集/存储不影响页面行情功能；只影响"重仓共识/抄作业"等选手数据模块的刷新。**

---

## 四、问题量化（为什么必须改造）

| 指标 | 实测值 | 趋势 |
|---|---|---|
| `.git` 体积 | **721MB** | 随每日 db commit 线性增长 |
| 历史中 >1MB blob 总量（打包前） | **13.7GB**（620 个） | 主要是 crawl_data.db 的历史快照 |
| crawl_data.db 历史 blob 份数 | 275 份 | 08-13 起每天 1~12 份 |
| 按当前"每天 1 份 22MB gz"外推 | 年 +8GB（打包前）/ pack 每年 +数百MB | **5 年 40GB+，不可持续** |
| `latest/players/` 累积 | ~~23192 文件 / 92MB~~ | ✅ 已根治(§0 ⑤, 5133 并自动淘汰) |
| db 本体 | 85MB，40 采集日封顶 | ✅ prune 已解决 |

**结论**：db 本体已封顶；真正的黑洞是 **git 历史里的二进制快照**。git 不适合做时序数据仓库——需要把"长期数据"搬出 git。

---

## 五、目标方案：git 轻量化 + Release 三层存储

### 5.1 分层设计

| 层 | 载体 | 内容 | 更新 | 保留 | 容量(5年) |
|---|---|---|---|---|---|
| **热层** | Release tag `db-state` 资产 `crawl-latest.db.gz` | 最新一个采集日全量 db | 每交易日收盘后覆盖上传（Release 资产可重复上传替换） | 永远最新一份 | 恒定 ~22MB |
| **温层** | Release tag `db-w2026W35`… 资产 `crawl-<week>.db.gz` | 按周聚合的归档 db | 每周五收盘后从"当周每日增量"合并导出 | 滚动 12 周（≈3个月，与 prune 窗口匹配） | ≈12×7MB |
| **冷层** | Release tag `db-m2026-08` 资产 `crawl-<month>.db.gz` | 按月聚合归档 | 每月首个交易日，把上月温层周档合并成月档 | **永久** | ~30MB/年×5年 ≈ 150MB |

> Release 资产单文件限 2GB、总仓限远超需求；匿名可下载（公开仓库）；完全在 GitHub 免费额度内。

### 5.2 取数/存数闭环（✅ 已上线，08-31 全链路验证通过）

```
每个 crawl run(白天 12 次 + 收盘, 一律执行):
  1. 下载热层 crawl-latest.db.gz → 解压为 data/crawl_data.db   ← 取数(状态回放, 3次重试)
     失败且本地无 db → run 终止(宁可停, 不可空库断链)
  2. main.py 采集 → 幂等写入当日数据                            ← 存数
  3. export_json.py 导出 → Build → Pages
  4. git 提交 JSON+状态文件(db 被 reset, 永不进 git)

仅收盘 run(crawl-eod 专班无条件; 常规班次北京时间≥14:45 兜底)追加:
  5. prune_crawl_db.py --apply(保留40采集日)
  6. release_db.py --sync: 快照(backup API+integrity_check+manifest)
     → 热层覆盖上传 + 当周温层 + 已完成月冷层 + 温层滚动清理    ← 存数
     上传失败 → workflow 立即失败(告警可见)
```

关键设计：**读路径每次执行、写路径收盘闸门**（08-31 空库导出事故的教训，详见 §0 事故记录）。db 内 40 采集日滚动 + 温层 12 周 + 冷层永久，三段窗口首尾相接，**任意历史日期都可取出**。

### 5.3 回测取数（满足"经常拉远端数据到本地"）

新增 `jiarenmens/scripts/fetch_db.py`：

```bash
python scripts/fetch_db.py --latest        # 热层 → data/crawl_data.db (回测最近40日)
python scripts/fetch_db.py --week 2026-W34 # 温层某周
python scripts/fetch_db.py --month 2026-07 # 冷层某月 → data/archive/
python scripts/fetch_db.py --range 2026-03 2026-08   # 拉多个月, 本地合并
```

匿名可下（公开仓），本机/任何机器无需 token。现有 `backtest_factors.py`、`verify_slices.py` 等脚本读 db 的方式不变。

### 5.4 git 历史瘦身（可选第③步，需明确授权后执行）

- `git filter-repo --path jiarenmens/data/crawl_data.db --invert-paths` 剥离全部历史 db blob（顺带 `latest/players/`），再 `--force` push。
- 前置条件：**冷/温/热层归档确认可下载后**才执行（先传后删，不留裸窗口）。
- 效果：`.git` 721MB → 预计 <100MB；clone 从 700MB+ 级降到常规水平。
- 注意：filter-repo 重写所有 commit hash，若有 fork/本地旧 clone 需重新 clone；公开仓 force push 后旧 PR 引用会失效。

---

## 六、实施清单（按序执行，做完①②即可观察运行）

- [x] **① 存量数据上云**：当前 crawl_data.db 已上传 Release `db-state`（热层）+ `db-m2026-07`（冷层首档）；匿名下载 + integrity_check 已验证。✅ 2026-08-31
- [x] **② 改造 crawl.yml**：下载热层（失败且有 git 内 db 则过渡放行）、`--sync` 上传（失败即终止）、integrity_check+manifest；提交数据步骤 `git reset` db（git 永不提交）。✅ 代码已推送
- [x] **③ fetch_db.py** 回测取数 CLI（--latest/--week/--month/--range/--list，重试+curl 兜底）。✅ 端到端验证
- [ ] **④ （可选）filter-repo 历史重写** + force push（需用户确认；前置条件①已满足）
- [x] **⑤ 导出收窄**：players/ 只导出"优质∪当日活跃∪被引用"集合并自动清理集合外旧文件。✅ 远端实测 23192→5133
- [~] **⑥ 观察期**：第 1/5 天(09-01)——首日 sync 三层就位已核对；09-02 起每日核对 manifest 行数单调、热层资产日期=当日、页面选手数据模块正常、钉钉正常。

## 七、影响分析

| 面向 | 影响 |
|---|---|
| 前端页面 | **零影响**。JSON 导出链路不动，行情/K线/竞价本就不走这条管道 |
| 钉钉推送 | 零影响。notify_daily 依赖的 last_notify_state.json 照常提交 |
| 竞价扫描/盘中监控 | 零影响。auction.db/hot_rank.db 独立于 crawl_data.db |
| 白天 12 次采集 | 零影响。只有收盘后那次做"下载→采集→上传"；白天 run 依旧只采+导出 JSON |
| 回测 | **变好**：fetch_db.py 按周/月直取，不再依赖 git 历史里的 db 快照；支持任意历史日期 |
| 数据安全 | **变好**：三层窗口首尾相接 + manifest + integrity_check + 失败即告警；git 历史不再是唯一备份 |
| 仓库体积 | 721MB → <100MB（做完④）；之后 git 增量仅 JSON，恒定低速 |
| 风险点 | ①Release 资产覆盖上传 API 需先删旧资产再传（脚本内处理）；②filter-repo 重写 hash——放最后且需授权；③迁移当天先传归档、再改 workflow，顺序不可颠倒 |

## 八、日常运维手册

```bash
# 手动触发一次全流程
gh workflow run "数据采集 + 部署"   # 或 Actions 页面点 Run workflow
export GH_TOKEN=ghp_xxx             # 本地 gh 需要仓库所有者账号的 PAT

# 回测取数(已上线)
cd jiarenmens && python3 scripts/fetch_db.py --latest        # 热层(最近40采集日)
python3 scripts/fetch_db.py --list                           # 查看全部可用归档
python3 scripts/fetch_db.py --range 2026-03 2026-08          # 多月合并(冷层)

# Release 存储手动维护(上传需 GITHUB_TOKEN=PAT)
GITHUB_TOKEN=ghp_xxx python3 scripts/release_db.py --sync    # 手动补一次三层同步
GITHUB_TOKEN=ghp_xxx python3 scripts/release_db.py --init    # 重建(灾备; 或 Actions 里跑 db_upload.yml)

# 本地跑一次采集(不动远端)
cd jiarenmens && python main.py --checkpoint-reset --test
# ⚠️ 本地库过期/不存在时先恢复: python3 scripts/fetch_db.py --latest
# ⚠️ 空库跑 export_json.py 会产出退化数据(08-31 事故, 见 §0)

# 查看库状态
sqlite3 data/crawl_data.db "SELECT COUNT(*), MIN(crawl_date), MAX(crawl_date) FROM trades;"
python3 scripts/prune_crawl_db.py          # dry-run 看会删什么

# 查看三层存储状态
curl -s https://api.github.com/repos/WXinYi/stockboard/releases | \
  python3 -c "import json,sys; [print(r['tag_name'], [a['name'] for a in r['assets']]) for r in json.load(sys.stdin)]"

# 常见故障
# - 页面选手数据不更新 → 看 crawl.yml「下载热层库/导出 JSON/提交数据」步骤日志
# - 页面数据退化(quality 数量异常小) → 疑似空库导出, 确认"下载热层库"步骤是否成功
# - Release 上传失败 → 看对应 run 日志; 修复后手动 --sync 或重跑 db_upload.yml
# - 钉钉重复推送 → 检查 last_notify_state.json 是否被漏提交/回退
# - 采集大量失败 → 东财 rtV2 限流或改版, 跑 python scripts/diagnose.py
# - crawl_data.db 损坏 → python3 scripts/fetch_db.py --latest 从热层覆盖恢复
```
