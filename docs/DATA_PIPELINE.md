# StockBoard 数据管道全景文档（取数 / 存数 / 长期存储方案）

> 用途：交接文档 + 日常运维手册。2026-08-30 梳理，含现状、问题量化与五年可持续存储方案。
> 涉及目录：`jiarenmens/`（采集端 Python）、`stockboard-app/`（前端 Vue）、`.github/workflows/`（调度）。

---

## 一、全流程总览

```
cron-job.org (交易日 12 次/天, 每次 dispatch)
        │ repository_dispatch: crawl
        ▼
GitHub Actions crawl.yml (job: crawl)
        │
        ├─ ① 午盘格局推送   (13:00–13:20 窗口, cycle_push.py --session midday)
        ├─ ② 数据采集       main.py --checkpoint-reset
        │      取数: 东财大赛 rtV1 榜单 + rtV2 选手详情/持仓/调仓
        │      存数: jiarenmens/data/crawl_data.db (SQLite)
        ├─ ③ 人气榜快照     auction_scan.py --hot-rank → hot_rank.db
        ├─ ④ 导出 JSON      export_json.py → stockboard-app/public/data/
        ├─ ⑤ 构建 Vue       npm ci && npm run build
        ├─ ⑥ 钉钉通知       notify_daily.py (增量, 状态写 last_notify_state.json)
        ├─ ⑦ 清理过期采集日 prune_crawl_db.py --apply (保留 40 个采集日 + VACUUM)
        ├─ ⑧ 提交数据       git add → 白天 reset crawl_data.db / ≥15:10 全量提交 → push
        └─ ⑨ Upload Pages artifact
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
latest/auction.json     — 竞价扫描快照(intraday_monitor 也读)
latest/players_index.json
```

⚠️ **已知问题**：`latest/players/` 只增不删——选手跌出榜单后旧 JSON 永远留在目录里。现已累积 **23192 个文件 / 92MB**，随 git 提交持续膨胀。处理见 §6 步骤⑤。

### 3.4 Git 提交策略（当前折中）

- 每次 run `git add -f stockboard-app/public/data/ jiarenmens/data/`（-f 覆盖部分 ignore）。
- **北京时间 <15:10**：`git reset` 掉 `crawl_data.db`，只提交 JSON + 状态文件；**≥15:10**（当天最后一次）才提交 db。
  - 原因：db 是 85MB 二进制，SQLite 无 delta 意义，每个 commit 都是一份近全量 blob；每天 12 次提交会把仓库撑爆。
  - 幂等性保证白天不提交也安全：trades/positions 按 (zh_id, crawl_date) 先删后写，当日重采覆盖。
  - 其余状态 JSON（last_notify_state/checkpoint 等）**必须每次提交**，否则钉钉增量推送会重复轰炸。
- 提交信息 `📊 数据更新 YYYY-MM-DD [skip ci]`（防止 push 再触发 workflow）；push 失败重试 3 次（pull --rebase）。

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
| `latest/players/` 累积 | 23192 文件 / 92MB | 只增不删 |
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

### 5.2 取数/存数闭环（改造后）

```
收盘后(≥15:10) crawl run:
  1. 下载热层 crawl-latest.db.gz → 解压为 data/crawl_data.db   ← 取数(状态回放)
  2. main.py 采集 → 幂等写入当日数据                            ← 存数
  3. prune_crawl_db.py --apply (保留40采集日) + PRAGMA integrity_check
  4. 生成 manifest(行数/日期范围/sha256) 打进 commit message
  5. gzip → 上传 Release db-state / crawl-latest.db.gz          ← 存数(热层)
     失败 → workflow 立即失败 + 钉钉告警（宁可停，不可断链）
  6. 周五: 合并当周 → 上传温层; 每月首日: 上月温层 → 冷层
  7. git 只提交 JSON + 状态文件 (crawl_data.db 永不再进 git)
```

关键设计：**"先下载再采集、上传失败即终止"**——热层始终是最新状态，任何一天失败链路立刻暴露，不存在"静默丢一天数据"；下载失败也终止（不允许从空库起采，否则会静默丢失"推 pull 时未下载成功的历史"）。db 内 40 采集日滚动 + 温层 12 周 + 冷层永久，三段窗口首尾相接，**任意历史日期都可取出**。

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

- [ ] **① 存量数据上云**：把当前 crawl_data.db（含 07-22 以来全部历史）gzip 后上传 Release `db-state`（热层）+ `db-archive-2026-07/08`（温/冷层初始档）；匿名 URL 验证下载、解压、`integrity_check ok`。
- [ ] **② 改造 crawl.yml**：加"下载热层→失败终止"、"上传热层→失败终止"、integrity_check+manifest；提交数据步骤删掉 db（git 永不再提交 db）；周五/月初归档步骤。
- [ ] **③ fetch_db.py** 回测取数 CLI（--latest/--week/--month/--range）。
- [ ] **④ （可选）filter-repo 历史重写** + force push（需用户确认）。
- [ ] **⑤ 导出清理**：export_json.py 结束时删除 `latest/players/` 中不在本次导出集合的旧文件（止血 23k 文件累积）；`db_watched_players` 等不变。
- [ ] **⑥ 观察期**：连续 5 个交易日核对——每日 manifest 行数单调、热层资产日期=当日、页面选手数据模块正常、钉钉正常。

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

# 回测取数(方案落地后)
cd jiarenmens && python3 scripts/fetch_db.py --latest

# 本地跑一次采集(不动远端)
cd jiarenmens && python main.py --checkpoint-reset --test

# 查看库状态
sqlite3 data/crawl_data.db "SELECT COUNT(*), MIN(crawl_date), MAX(crawl_date) FROM trades;"
python3 scripts/prune_crawl_db.py          # dry-run 看会删什么

# 常见故障
# - 页面选手数据不更新 → 看 crawl.yml「导出 JSON/提交数据」步骤日志
# - 钉钉重复推送 → 检查 last_notify_state.json 是否被漏提交/回退
# - 采集大量失败 → 东财 rtV2 限流或改版, 跑 python scripts/diagnose.py
# - crawl_data.db 损坏 → 从 Release db-state 重新下载热层覆盖
```
