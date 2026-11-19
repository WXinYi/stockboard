# StockBoard 数据管线（当前状态）

> 最后更新：2026-11-17（北京时间）。本文件只描述**当前生产链路**与必要运维点；历史事故和过程见 `git log`。
> 代码仓库：<https://github.com/WXinYi/stockboard>；生产站点：<https://wxinyi.github.io/stockboard/>。

## 1. 全链路一句话

cron-job.org 触发 GitHub Actions → 生成静态 JSON → 提交到 `main` → GitHub Pages 部署；watchdog 和格局推送直接发钉钉，不依赖页面刷新。

当前生产不再跑 `main.py` / `export_json.py` / `notify_daily.py` / `prune_crawl_db.py`。核心产物是 4 契约 + 4 市场切片；`auction.db` 仍通过 Release 热层在竞价/市场类 workflow 间共享。

## 2. 当前调度

| 时间/频率 | 触发方式 | workflow | 作用 |
|---|---|---|---|
| 09:25 | cron-job.org `workflow_dispatch` | `auction.yml` | 竞价扫描、出击选股、钉钉推送、发版 |
| 09:26–14:51，20 班 | cron-job.org `workflow_dispatch crawl.yml`，body 含 `inputs.trigger_source=cron` | `crawl.yml` | 市场切片 + rank/midday/market 快照 |
| 15:15 | cron-job.org `repository_dispatch: crawl-eod` | `crawl.yml` | close 全量快照 + 收盘数据定稿 |
| 15:45 | cron-job.org job `8880763`，`repository_dispatch: crawl-cancel-check` | `crawl-cancel-alert.yml` | 掐班告警 |
| 09:26–15:01 每 5 分钟 | cron-job.org `repository_dispatch: watchdog` | `watchdog.yml` | 关注选手即时钉钉 |
| 09:30 | cron-job.org `repository_dispatch: watchdog-open` | `watchdog.yml` | 开盘冲刺，50 秒/轮至 09:35 |

`crawl.yml` 的 20 个定时 job 已统一带 `trigger_source=cron`，run-name 显示 `[cron]`；掐班告警只认这些定时班和 `crawl-eod`，不统计 push / 普通手动补跑。GitHub 原生 schedule 已停用（曾延迟到次日凌晨）。

## 3. `crawl.yml` 现行流程

### 3.1 守卫与班次

- 交易日历单一真源：`stockboard-app/src/utils/tradingCalendar.js`。
- 非交易日跳过采集/提交，只保留部署；push 触发仅在 09:00–15:15 采集，盘后/夜间 push 只发版。
- 班次判定：
  - `crawl-eod` → `close`
  - 09:25–09:35 → `rank`
  - 11:29–11:40 → `midday`
  - 其它 → `market`
- 核心时段（09:15–09:45、11:15–11:45）若落入 `market`，输出 `::warning::` 提示 cron 漂移/排队。
- `concurrency: pages-${{ github.ref }}`，`cancel-in-progress: false`：后续 market 班排队，不掐正在跑的 midday/close。

### 3.2 数据步骤

| 步骤 | 命令/脚本 | 说明 |
|---|---|---|
| 榜单快照 | `player_snapshot.py --rank --live` | 每榜 300 人（5 榜 × 75 次分页），写 `players.json` / `today.json` / `stocks.json` 基础字段 |
| 契约哨兵 | `em_contract_probe.py --live` | 写 `today.json.emContract`，浏览器 EM 直连是否可用；**网络异常（超时/SSL）归一为 ok=false 优雅降级，不打死整班**（2026-11-19 09:29 事故修复） |
| 午盘快照 | `player_snapshot.py --midday --live --no-token` | 12 worker，2N 次 rtV2 调用 |
| 收盘快照 | `player_snapshot.py --close --live --no-token` | 12 worker，3N 次 rtV2 调用 |
| 涨停池归档 | `backfill_emotion.py --pool <7天窗口> --skip-existing` | 15:00 后班执行，7 天滚动幂等自愈；**2026-11-19 修复**（重构删 auction-label.yml 时生产者被连带删除，limit_pool 断供 11-13~11-18 三个交易日，竞价班周期判定/连板名单静默用陈旧池；竞价班另有"池日落后>4 自然日 → ::warning::"守卫） |
| 市场切片 | `export_market.py --market-only` | 写 `hot_rank/lianban_bid/broken_pool/market_breadth.json` |
| 格局推送 | `cycle_push.py --session midday/eod` | 13:00–13:25 / 14:40–15:00，钉钉正文与失败标志 |
| auction.db | `release_db.py --what auction --download-latest/--upload-latest` | 每班恢复，sha 变更才上传，带 baseline-sha 并发守卫 |

**LLM 影子选股已下线（2026-11-19 拍板）**：`llm-1000.yml` 重构删除后自 11-14 起无新产出，9:25 回填永远停在旧存档，且 11-07 回测结论"不转正"。已删：`src/analysis/llm_review.py`、`scripts/llm_picks_review.py` 及测试、`data/llm_archive/`、auction.yml 的 DEEPSEEK env、前端大模型卡（`auctionError` 失败提示独立成节保留）；`auction.json` 的 `llm` 键与 auction.db 的 `llm_review` 表（历史数据）不再产出/读取。

`SB_DATA_V2=0` 时不产出 4 契约，用于灰度回滚。`midday/close` 的 no-token payload 不带 `ctToken/utToken/randomCode/deviceId`。

### 3.3 部署与提交

- 数据班只精确提交：`players/stocks/today/hot_rank/lianban_bid/broken_pool/market_breadth.json` + `jiarenmens/data/.cycle_push_state.json`。
- 提交信息：`📊 数据更新 YYYY-MM-DD [skip ci]`。
- 推送冲突：`git -c rebase.autoStash=true pull --rebase -X theirs`，最多重试 3 次；`-X theirs` 在 rebase 中表示保留本班重放提交（最新采集数据）。
- dist 壳按前端源码指纹缓存；代码未变时只 `rsync` 拼接最新 `public/data`。
- 任一格局推送环节异常写 `/tmp/cycle_push_failed`，班次末尾检查并判红。

## 4. 静态数据契约

生产目录：`stockboard-app/public/data/latest/`。

| 文件 | 结构 |
|---|---|
| `players.json` | `{date, rankTime, players[]}`；选手含 `id/name/userId/watched/prevClose/today` |
| `stocks.json` | `{date, snapshot: 1130|close, updatedAt, stats[]}`；`stats[] = {code,name,h,tp,ap}` |
| `today.json` | `{date, updatedAt, tradedPlayerIds, emContract, tradeConsensus}` |
| `hot_rank.json` | `{date, snap, rows[], src}` |
| `lianban_bid.json` | `{date, prev_day, top[], src}` |
| `broken_pool.json` | `{date, rows[], src}` |
| `market_breadth.json` | `{date, row, src}` |

`tradeConsensus` 当前代码写入 `totalTrades/totalBuyers/aTotalTrades/aTotalBuyers/stocks[]`。2026-11-17 生产快照暂为旧结构（缺 A 股字段），前端回退全市场口径；11-18 自然班后完整写入，契约守卫届时应收紧为“必须有”。

A 股判定：6 位数字代码；港股为 5 位。两个共识页只在展示层滤港股，数据文件保留全量。

## 5. 推送

### 5.1 格局推送

- 午盘 `13:00–13:25`：`cycle_push.py --session midday`，正文末尾追加 A 股调仓 Top5 + 重仓 Top5；正文打印到日志供核对。
- 尾盘 `14:40–15:00`：`cycle_push.py --session eod`。
- 去重状态：`jiarenmens/data/.cycle_push_state.json`，已纳入 `crawl.yml` 提交清单。
- 任一环节（接口/计算/构建正文/钉钉）异常都写失败标志并返回非零；`continue-on-error` 不阻断落库/部署，但失败检查会判红。

### 5.2 watchdog

- 每 5 分钟拉关注选手 rtV2 明细，新操作即时推钉钉；09:30–09:35 开盘冲刺。
- 去重键：`(选手, 股票, 方向) + 成交次数`；次数增加补推“追加”。
- 状态放 Actions cache `watchdog-state-*`，日报读同一状态，已推操作不重复响铃。
- 关注名单单一真源：`jiarenmens/src/config/watched.py`，当前 9 人；前端 `FALLBACK_WATCHED` 仅旧缓存兜底。
- 已删除过期 `fetch_base_player()` / 首班 seed 基线：per-player JSON 已下线，旧逻辑每早 9×3 次 404 空耗。
- 只在工作日 09:26–15:05 执行，午休 11:30–13:00 不开机；连续 3 班全员失败推降级告警。

### 5.3 掐班告警

- cron-job.org 15:45 触发 `repository_dispatch: crawl-cancel-check`。
- 脚本仅在北京时间 15:40–16:30 检查；只认 `repository_dispatch(crawl/crawl-eod)` 和 `[cron]` run-name。
- 已告警 run id 落 `.github/state/crawl-cancel-alert.json`；workflow 有并发组、状态提交三次重试、失败告警。
- 补发消息只列本次新增 cancelled run，旧 run 不重复出现。

## 6. 存储与恢复

- `auction.db`：不进 git；走 Release `auction-state` 热层 + 日快照（30 天）+ 周快照（26 周），每班下载/上传并做 baseline-sha 检查。
- `crawl_data.db`：历史回测库，当前页面生产链路不再依赖；仍可从 Release `db-state` 恢复。
- 数据 JSON 被 `.gitignore` 排除但已跟踪，提交必须 `git add -f <具体文件>`，不要用 `git add -A`。
- 常用恢复命令：
  ```bash
  cd jiarenmens
  python scripts/fetch_db.py --latest
  python scripts/fetch_db.py --auction
  python scripts/fetch_db.py --month 2026-07
  python scripts/fetch_db.py --list
  ```

## 7. 日常运维

| 场景 | 做法 |
|---|---|
| 看当天班表 | `gh run list --limit 100 --json number,createdAt,workflowName,status,conclusion,displayTitle` |
| 手动补跑采集 | `gh workflow run crawl.yml -f force_class=rank|midday|close|market` |
| 只看某班日志 | `gh run view <run-id> --log` |
| 改 cron-job.org | 用本机 API key；创建 `PUT /jobs`，更新 `PATCH /jobs/{id}`，body 包一层 `"job"` |
| 改关注名单 | 只改 `jiarenmens/src/config/watched.py`，必要时同步前端冻结兜底 `FALLBACK_WATCHED` |
| 数据契约体检 | 见 `docs/OPTIMIZATION_PROGRESS.md` 的命令速查与当前验证 |

禁止：手工编造 `tradeCount` / `tradedPlayerIds` / A 股统计；把 `market` 班当 `midday/close` 数据；用 `git add -A` 带进 untracked 文件。

## 8. 当前验证基线

- 前端：`npx vitest run` → 230 passed；`npx vite build` 成功。
- 后端：`/opt/homebrew/bin/python3.12 -m unittest discover -s scripts -p 'test_*.py'` → 276 tests OK (skipped=3)。
- 系统 `python3` 是 3.9，跑全量会因 `dict | None` 报 import error；必须用 3.12。
- CI：代码门禁 `test.yml` 覆盖 `jiarenmens/**/*.py`、`stockboard-app/src/**`、`scf/**`、`.github/workflows/*.yml`。

## 9. 剩余事项

1. 11-18 自然班验证：midday 写 A 股字段、13:05 推送正文与去重状态、15:45 新告警 workflow。
2. A 股字段到位后收紧 `test_qa_s1_contracts.py`。
3. S5/G4 证据链：K2、班时、20+21 班对账；R2/NR-2/NR-3 风险收口。
4. S6 归档：G4 后整理历史文档；本文件与 `docs/OPTIMIZATION_PROGRESS.md` 已是当前状态入口。
5. ~~cron-job.org 历史 `llm-1000` job `8489018`~~ **已确认删除**（2026-11-17 API 复查：cron-job 上已无任何 llm 任务，20 个采集班数量一一吻合；该 job 在重构 S3 外部 cron 清理时已删，本行此前记录滞后）。另有 4 个 disabled 的 AIStock 旧项目任务（7570116~7570130），与本项目无关，未动。

## 10. 回滚点

- 当前全量修复：`git revert f76a8b4e7b3`。
- watchdog 基线清理：`git revert 6820c28112a`。
- 掐班告警外部触发：`git revert 708253c86988`。
- 静态数据：按 4 契约对应数据 commit 回退；`auction.db` / `crawl_data.db` 由 Release 三层存储兜底。
