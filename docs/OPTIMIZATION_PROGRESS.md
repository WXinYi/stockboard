# StockBoard 当前进度

> 最后更新：2026-11-17（北京时间）
> 本文件只记录**当前状态、现行架构、剩余待办和关键红线**。历史过程见 `git log`；运维细节见 `docs/DATA_PIPELINE.md`。

## 一句话状态

生产链路稳定：数据采集、前端 4 契约、watchdog 即时推送、午/尾盘格局推送、掐班告警均已上线并通过 CI 与生产核验。当前没有阻塞上线的 bug；剩余是明日自然班观察和规格证据收尾。

当前代码 HEAD：`f76a8b4e7b3`（全量复测问题收口）。最近 CI：代码门禁 `44045910912` success；部署班 `44045910944` success；生产 bundle 已更新为 `index-CNk4sxs3.js` / `index-ChNhDtGK.css`。

## 当前页面

| 路由 | 页面 | 说明 |
|---|---|---|
| `/market` | 选股 | 今日定性、出击、周期/六情绪、盘面模块 |
| `/market/auction` | 竞价详情 | AuctionTab 在此复用，独立 `/auction` 路由已删除 |
| `/rankings` | 排行榜 | 收益/净值排序，今日操作筛选 |
| `/stocks` | 重仓共识 | A 股 Top20，只按合计仓位/人均口径展示 |
| `/flow` | 调仓共识 | A 股 Top20，净买入/买入人数/卖出人数三视图 |
| `/player/:id`、`/stock/:code` | 详情 | 选手详情、股票详情 |
| `/info/:code/:iid`、`/board/:bk_code` | 资讯/板块详情 | 辅助详情 |

已删除：`/copy` 抄作业页、独立 `/auction` 路由、纪律卡、V5/评分漏斗、per-player 静态详情页。

## 当前数据契约

生产静态数据位于 `stockboard-app/public/data/latest/`：

| 文件 | 当前结构/口径 |
|---|---|
| `players.json` | `{date, rankTime, players[]}`；选手含 `id/name/userId/watched/prevClose/today`。`prevClose` 5 字段，缺失写 `null`。 |
| `stocks.json` | `{date, snapshot: 1130|close, updatedAt, stats[]}`；`stats[]={code,name,h,tp,ap}`，数据保留全市场，前端展示时过滤 A 股。 |
| `today.json` | `{date, updatedAt, tradedPlayerIds, emContract, tradeConsensus}`。`tradeConsensus` 含全市场 `totalTrades/totalBuyers` 与 A 股口径 `aTotalTrades/aTotalBuyers`（11-18 自然班起写入；旧快照缺字段时前端回退全市场口径）。 |
| 市场 4 切片 | `hot_rank.json` / `lianban_bid.json` / `broken_pool.json` / `market_breadth.json`，均为平铺 JSON。 |

A 股判定：6 位数字代码；港股为 5 位。两个共识页只在展示层滤港股，数据文件仍保留全量，便于回退。

## 当前调度

| 时间/频率 | 触发 | 目标 |
|---|---|---|
| 09:25 | cron-job.org `workflow_dispatch: auction.yml` | 竞价扫描 + 推送 + 发版 |
| 09:26–14:51 共 20 班 | cron-job.org `workflow_dispatch: crawl.yml`，body 含 `inputs.trigger_source=cron` | 市场切片 + 选手 rank/midday/market 快照 |
| 15:15 | cron-job.org `repository_dispatch: crawl-eod` | close 全量快照 + Release sync |
| 15:45 | cron-job.org job `8880763`，`repository_dispatch: crawl-cancel-check` | 掐班告警，external trigger，去重状态落 `.github/state/crawl-cancel-alert.json` |
| 09:26–15:01 每 5 分钟 | cron-job.org `repository_dispatch: watchdog` / 09:30 `watchdog-open` | 关注选手即时钉钉 |
| 10:03 | cron-job.org `repository_dispatch: llm-1000` | 大模型盘中选股影子运行 |

`crawl.yml` 的 20 个定时 job 已统一带 `trigger_source=cron`，run-name 显示 `[cron]`；掐班告警只认这些定时班与 `crawl-eod`，push/手动补跑不再误报。

## 现行关键机制

### 采集与数据

- rank/midday/close 走 `player_snapshot.py --no-token`，payload 与前端一致，不带 token。
- 12 worker 并发，按玩家原始下标汇总，保持 2N/3N 调用序列。
- 当前榜单上限 300：`EM_RANK_LIMIT` 默认 300，回滚可设 500。
- 实测全天 4-JSON 链路请求数：`75 + 2772 + 4158 = 7005`（1386 人），按实测口径达标；6575 是按 1300 人估算的旧标称值，不再作精确验收线。
- 调仓共识写入 `today.json.tradeConsensus`，每班全量覆盖不做并集；人数按选手去重，笔数不去重，方向未知不猜测。
- EM rtV2 `beginDate/endDate` 必须传 `YYYYMMDD`，token/no-token 两条路径均已统一。

### 推送

- 13:05 午盘格局推送末尾附加 A 股调仓 Top5 + 重仓 Top5，只推 6 位代码；正文打印到日志供核对。
- 尾盘格局推送窗口放宽到 `1440–1460`，去重状态 `jiarenmens/data/.cycle_push_state.json` 已纳入 `crawl.yml` 精确提交清单。
- `cycle_push.py` 任一环节异常都会写 `/tmp/cycle_push_failed` 并返回非零，不再被 `continue-on-error` 静默吞掉。

### watchdog

- watchlist 单一真源：`jiarenmens/src/config/watched.py`，当前 9 人；前端 `FALLBACK_WATCHED` 仅为旧缓存兜底。
- 平台明细只有日粒度，“即时”= 当日该笔首次出现或成交次数增加的时刻。
- 去重键为 `(选手, 股票, 方向) + 成交次数`；次数增加补推“追加”。
- 状态放 Actions cache `watchdog-state-*`；日报读同一状态，跳过已即时推送的操作。
- 已删除过期 `fetch_base_player()` / 首班 seed 基线：per-player JSON 已下线，旧逻辑每早 9×3 次 404 空耗且无可推基线。
- 首班基线移除后，若 crawl 已部署当日操作但 watchdog 尚未推过，watchdog 会正常补推；日报仍按 watchdog state 去重，不会重复响铃。

### 掐班告警

- GitHub 原生 schedule 曾延迟到次日 01:28，已停用。
- 当前由 cron-job.org 15:45 触发 `crawl-cancel-check`；脚本限制北京时间 15:40–16:30 才检查。
- 只认 `repository_dispatch(crawl/crawl-eod)` 和带 `[cron]` 的 workflow_dispatch。
- workflow 有 `concurrency`、状态提交三次重试和 `alert-on-failure`；消息只列本次新增的 cancelled run。

## 当前验证

| 项 | 结果 |
|---|---|
| 前端单测 | `230 passed` |
| 前端构建 | `vite build` 成功，bundle `index-CNk4sxs3.js` / `index-ChNhDtGK.css` |
| 后端单测 | `276 tests OK (skipped=3)` |
| 语法/YAML | `py_compile`、workflow YAML、`git diff --check` 全绿 |
| IDE lint | 0 diagnostics |
| CI | 代码门禁 success，部署 success |
| 生产数据（11-17 close） | `players` 1381 人，`prevClose` 1381 全满，`stocks.snapshot=close`，`traded=820` |
| 掐班告警 | 15:45 自然触发 success，识别 18 个定时采集班全部完成 |

## 剩余待办 / 观察

| 项 | 状态 | 说明 |
|---|---|---|
| 11-18 midday 自然班 | 待观察 | 确认 `[cron]` run-name、`tradeConsensus` 非零、`aTotalTrades/aTotalBuyers` 写入 |
| 11-18 13:05 午盘推送 | 待观察 | 确认钉钉正文含 A 股共识段，日志打印共识正文，去重状态成功提交 |
| 11-18 15:45 掐班告警 | 待观察 | 最新 workflow（并发组/重试/失败告警/只列新增 run）上线后首次自然验证 |
| 收紧契约守卫 | 默认动作 | 新字段到位后，把 `test_qa_s1_contracts.py` 中 A 股字段从“可选兼容”改为“必须有” |
| S5/G4 证据链 | 待收口 | K2、班时、20+21 班对账，R2/NR-2/NR-3 |
| S6 归档 | 待收口 | G4 解锁后整理历史文档；本文件已是当前状态入口 |

没有需要立即拍板的生产阻塞项。可选专项：watchdog Actions cache 架构优化（当前不是已确认故障，暂不做）。

## 关键红线

1. `midday/close` 的 no-token payload 不能带回 `ctToken/utToken/randomCode/deviceId`。
2. 缺失值一律显示 `—`，不能用 0 冒充。
3. A 股=6 位数字，港股=5 位；展示层过滤，不改数据契约。
4. `tradeConsensus` 每班全量覆盖，不做并集。
5. `crawl.yml` 的 `concurrency.cancel-in-progress: false` 不能改回 `true`。
6. `crawl-eod` 的 `types: [crawl-eod]` 是 15:15 专班命脉，不能删。
7. `.codebuddy/` 不进 git，不删除；3 个 legacy 回测脚本保持只读归档，旧实施计划与 superpowers 旧设计稿已清理。
8. cron-job.org API key 只留在本地，不进聊天、日志、git 或静态产物。

## 回滚点

- 当前全量修复：`git revert f76a8b4e7b3`。
- watchdog 首班基线清理：`git revert 6820c28112a`。
- 掐班告警外部触发改造：`git revert 708253c86988`。
- 静态数据回退：4 契约文件被 git 跟踪，按对应数据 commit 回退；db 热层由 Release 三层存储兜底。
