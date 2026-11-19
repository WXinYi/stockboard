# StockBoard 全站审计报告（2026-11-18 闲时任务）

> 执行时间：2026-11-18 19:39–20:10（北京时间，交易日盘后）。
> 审计基线 HEAD：本地 `2ad85bb05a7` / 远端 `origin/main`（fetch 至 191701ee568，今日数据提交）。
> 性质：**只读审计**。本文件为唯一产出，未改任何源码、未 commit/push。
> 审计执行时生产已包含昨夜 5 个新提交（960e150…~2ad85bb…，即《项目复核报告_2026-11-17.md》的修复落地 + 死代码删除 + 回测脚本入库），本报告不重复该报告已修项。

---

## 摘要

- **验证基线全绿**：前端 vitest **282 passed**（25 文件；较 11-17 基线 230 增加 52 例，来自昨夜 MarketTab 下线区块回归等新用例）、后端 unittest **OK (skipped=3)**、py_compile OK、8 个 workflow YAML 全部解析通过。
- **11-18 三个自然班观察全部通过**（详见 §1.2）。
- **9 条红线逐条取证全部通过**（详见 §1.5）。
- **发现 1 个 P0**：`limit_pool`/`broken_pool` 两张表自 **11-13** 起停更（重构删除 auction-label.yml 时涨停池生产者未在 crawl.yml 重建），09:25 竞价班的连板名单/周期判定/LLM 回填已连续 3 个交易日使用 5 天前的池子。
- P1 × 3、P2 × 6（清单见 §4 汇总表）。**全程未动手修复**，等拍板。

| 项 | 结果 |
|---|---|
| 测试（前/后端） | 282 passed / OK(skipped=3) ✅ |
| 生产 7 JSON 契约 | 全部符合，A股共识字段已写入 ✅ |
| 生产站资源 | index.html 引用 2 资源均 200，无已删特性字样 ✅ |
| 今日 60 班 Actions | **60/60 success**（watchdog 46 + crawl 13 + 告警 1）✅ |
| 11-18 观察项①②③ | 全过 ✅ |
| 红线 9 条 | 全过 ✅ |
| 班次时长 | 盘中班 0–1min / midday 4min / EOD 6min（基线 5.2–7.4 / 24.4）✅ 远超 ≤50% 目标 |
| 请求量 | close 班实测 4164 = 1388×3，与 75+2N+3N 口径吻合 ✅ |
| P0 / P1 / P2 | **1 / 3 / 6** |

---

## 一、正确性核查

### 1.1 代码与测试

| 项 | 命令 | 结果 |
|---|---|---|
| 前端 | `npx vitest run` | **25 files / 282 passed**（0 fail）✅ |
| 后端 | `python3.12 -m unittest discover` | **OK (skipped=3)** ✅ |
| 语法 | `python3.12 -m compileall -q src scripts` | OK ✅ |
| YAML | `ruby -ryaml` 解析 8 个 workflow | OK ✅ |
| 构建 | `vite build --outDir /tmp/sb-audit-build` | 成功，**7 precache entries**（与基线一致）✅ |

> 基线说明：前端 230 → 282 源于昨夜提交新增用例（bb21d1fa75b 的 MarketTab 5 例等），**282 为新基线**，建议下次更新文档时同步。

### 1.2 生产数据契约（线上实抓 7 个 JSON + 热层 DB 实证）

| 文件 | 校验 | 结果 |
|---|---|---|
| today.json | date=11-18、updatedAt=15:16、emContract.ok=true(09:26:43) | ✅ |
| — tradeConsensus | **非零**：totalTrades=1654/totalBuyers=427，**aTotalTrades=1321/aTotalBuyers=366** | ✅ **观察项①通过** |
| players.json | date=11-18、rankTime=09:26、1388 人、**watched 9 人与 watched.py 逐一吻合**、缺 userId=0 | ✅ |
| stocks.json | snapshot=close、1839 行、stats 字段并集恰为 {ap,code,h,name,tp}、无 h/tp=None | ✅ |
| hot_rank / lianban_bid / market_breadth | date=11-18、结构平铺、market_breadth.zhaban=24 | ✅ |
| broken_pool.json | **date=2026-11-13** ← 唯一异常，见 P0 | ❌ |

缺失值红线抽查：stocks 无 h==0/tp==0 冒充（tp==0 的 95 行均为真实零持仓合计，h 同步为 0 口径一致）；players prevClose 无 null 残留（11-17 后全满）。

**11-18 自然班观察（handoff 待办三项）**：

| 观察项 | 证据 | 结论 |
|---|---|---|
| ① midday A股字段 | today.json 含 aTotalTrades/aTotalBuyers（上表） | ✅ |
| ② 13:05 午盘推送 | run 44241748151 日志：`[cycle_push] 午盘共识段: **📊 A股共识（收盘后快照…）** - 调仓 Top5…` + `✅ 推送成功`；远端 `.cycle_push_state.json` 含 `2026-11-18|midday 13:05:40` 与 `|eod 14:40:34` 且随 154ae5484f3/a9799c911b2 提交 | ✅ |
| ③ 15:45 掐班告警 | run 44261329259：`✅ 当日 20 个定时采集班全部完成` + `无新增告警状态` | ✅ |

> 审计过程中的一个自我纠偏（记录供后续审计参考）：本地 `.cycle_push_state.json` 停在 11-13 曾疑似"推送状态未落盘"，fetch 后确认远端正常——**本地工作区视角会落后于生产，生产状态判定一律以 origin/main 为准**。

### 1.3 前端界面（程序化检查）

- 生产 `index.html` 引用 `index-4bcCxltp.js`(347KB)/`index-Co9NboO5.css`(89KB)，HEAD 均 200 ✅。
- 生产 bundle 残留字样：`players_index`/`name_map`/`changes_summary`/`抄作业`/`qualityOnly` 均 **0 处**；`stockStats` 2 处为 useDataV2 **有意保留的同名派生接口**（导出名与旧 useData 对齐方便组件迁移，注释明示），非契约残留 ✅。
- 注：`docs/OPTIMIZATION_PROGRESS.md` 中记录的 bundle 指纹 `index-CNk4sxs3.js` 已过时（生产现为 `4bcCxltp`）——该行已改为"验证基线"语义，但 bundle 名建议改为不写死或下次更新。

### 1.4 钉钉推送链路（今日全班次）

- **60/60 班 success**，无 failure、无 cancelled（watchdog 46 / crawl 13 / 掐班告警 1；60 条为 API 上限，09:25 auction 班 44215651420 单查亦 success）。
- alert-on-failure 覆盖：7/8 workflow 有配置；**test.yml 无**（见 P2-1）。
- watchdog 今日班全 success；推送笔数属钉钉黑盒，本次只读审计无法验证收端，未收到报障（注：run log 的"盯盘检测"行抓取受日志接口限制未取到样本，班次结论已覆盖）。
- auction 班（44215651420）诚实打印了陈旧池来源（见 P0 引文），无假数据伪装。

### 1.5 红线抽查（9 条逐条）

| # | 红线 | 证据 | 结论 |
|---|---|---|---|
| 1 | no-token payload 不带 token 四件套 | `player_snapshot.py:344-346` 注释明示"绝不携带 ctToken/utToken"，NO_TOKEN_METHODS 三方法无凭证参数 | ✅ |
| 2 | A股 6 位/港股 5 位仅展示层过滤 | `FlowTab.vue:19` `onlyAStock(consensus.stocks)`，注释"港股（5 位代码）滤除"；StockTab 同构 | ✅ |
| 3 | tradeConsensus 每班全量覆盖 | `player_snapshot.py:654-665` docstring + 代码 `trade_consensus if not None else 兜底` | ✅ |
| 4 | crawl.yml `cancel-in-progress: false` | crawl.yml:57、watchdog.yml:21 | ✅ |
| 5 | `types: [crawl-eod]` 保留 | crawl.yml:29 `types: [crawl, crawl-eod]` | ✅ |
| 6 | `.codebuddy/` 不进 git | `git ls-files .codebuddy` 空 + 已被 38f01b202d7 gitignore | ✅ |
| 7 | 无密钥硬编码 | 全仓 grep（py/js/yml/vue）jbuwEV/ghp_/github_pat_ 无命中 | ✅ |
| 8 | 缺失值 null/— 不冒充 0 | §1.2 抽查 + 昨夜 960e150380a 已系统性收紧（双实现） | ✅ |
| 9 | 数据提交精确 add | 今日数据提交均只含 7 JSON + state（如 154ae5484f3） | ✅ |

---

## 二、效率评估（只评估不动手）

### 2.1 班次时长（今日实测 vs 改造前基线）

| 班型 | 今日实测 | 旧基线 | 相对 | 达标（≤50%） |
|---|---|---|---|---|
| 盘中 market 班 ×17 | **0–1 min** | 5.2–7.4 min | ~15% | ✅ 大幅超额 |
| rank 班（09:26） | 1 min | 同上 | — | ✅ |
| midday 班（11:30） | 4 min | 14.3–15.1 min（EM-jitter） | ~28% | ✅ |
| EOD 班（15:15） | **6 min** | 24.4 min | 25% | ✅ |

瓶颈已不在采集：EOD 6min 中约 5min 为 backfill_broken 重试等待（3×60s）与 Release 上传。**无紧迫优化项**。

### 2.2 请求量口径

- 今日 EOD 班日志：`[apiCalls] close 本班 rtV2 调用 4164 次 (选手 1388 人 × 3 = 4164)` —— 与 75+2N+3N 口径精确自洽（N=1388，全天 ≈75+2776+4164=7015）。✅
- 进一步降量空间（**建议，均未拍板**）：① 榜单上限 EM_RANK_LIMIT 300→200 可省 midday/close 各 ~N/3 请求，代价是名单覆盖变窄、共识口径漂移，不建议在无回测支持下降；② midday 班若只服务调仓共识，理论上可只调 trades 接口（N 次而非 2N）——但持仓快照是 FlowTab/StockTab 两页的盘中数据源，动它=改产品语义，需单独立项。

### 2.3 前端

- bundle 347KB（gzip 后 precache 438KB 含 SW 资产）单 chunk 无分包——当前体量下加载无感，**不建议**为分包引入构建复杂度。
- 三层缓存（内存/Pages 600s/SW 24h）设计自洽；`no-store` 统一走 loader.js 已验证（复核报告 P0-3 修复）。

### 2.4 CI

- test.yml 已补 vitest 门禁（昨夜 P0-2 修复落地确认）✅。
- 建议（P2-1）：test.yml 补 alert-on-failure；门禁红目前只有 pusher 自己可见。

---

## 三、无用代码与残留清单（等拍板，未删除）

### 3.1 后端脚本（jiarenmens/scripts）

| 脚本 | 引用证据 | 定性建议 |
|---|---|---|
| `six_parity_fixture.py` | workflow=0、docs=0；对拍对象 sixEmotion 前端已 0 引用（见 3.2） | **删除候选** |
| `verify_slices.py` | workflow=0；**读已删除的 name_map.json/copy.json，一跑即 FileNotFoundError** | **删除候选（高置信）** |
| `llm_picks_review.py` | workflow=0；LLM v3 已结案不转正（11-07 结论） | 删除候选（结案脚本族） |
| `calibrate_probability.py` / `strike_gate_backtest.py` | workflow=0、docs=0；概率校准已被证不可行（09-12）、strike 回测"等存档累积"已 3 个月未跑 | 删除候选（回测漏网，与 10-16"回测工具全删"拍板精神一致） |
| `divergence_anchor_backtest.py` / `follow_backtests.py` / `quality_consensus_backtest.py` | **昨夜 2ad85bb05a7 刚入库（预注册规格）** | **不列**（用户已拍板保留） |
| `fetch_db.py`（docs=2）/ `lianban_bid_hs.py` | 本地一键工具（任务书明示） | 保留 |
| `data_reconcile.py` / `diagnose.py` / `dashboard.py` | 本地运维工具，无引用但属手动排障入口 | 保留（可在 README 归类"本地工具"段） |
| `intraday_monitor.py` | 2026-08-29 停用有明示"保留代码随时可重启" | 保留 |
| `backfill_broken.py` | crawl.yml:227 在用 | 保留（P0 修复依赖它） |

### 3.2 前端（stockboard-app/src）

| 文件 | 引用证据 | 定性建议 |
|---|---|---|
| `utils/sixEmotion.js` | **0 处非测试引用**（sixLive 已随 bb21d1fa75b 删除） | **删除候选**（连带下两条） |
| `utils/__tests__/sixEmotion.test.js` | 仅测 sixEmotion | 随上条删 |
| `utils/__tests__/sixParity.test.js` | 对拍 six_history.json（生产者已下线、文件冻结） | 随上条删 |
| bundle 内 `stockStats` 命名 | useDataV2 有意同名派生（注释明示） | 保留（非残留） |

### 3.3 文档与配置

| 位置 | 问题 | 建议 |
|---|---|---|
| `docs/OPTIMIZATION_PROGRESS.md` | bundle 指纹 CNk4sxs3 已过时（现产 4bcCxltp）；前端基线 230 应更新为 282 | 下次顺手改，或 bundle 行改"以线上为准" |
| `CLAUDE.md`（本地 gitignore） | **架构段整体停在重构前**：架构树仍列 auction-label.yml/db_upload.yml（32/36 行）、cron 编排清单仍列 5 workflow dispatch（326-334 行）、§十表格仍列 backfill_positions.py（237 行）、§九第 6/13/16/19 条引用已删文件 | 本地文档，建议按 DATA_PIPELINE.md 重写架构段（P1-3） |
| 根目录 `项目复核报告_2026-11-17.md` | 昨夜入库的 368 行复核报告在仓库根目录而非 docs/ | 可移 docs/ 或留档（P2） |

### 3.4 生产数据

| 项 | 现状 | 建议 |
|---|---|---|
| `broken_pool.json` | 前端 **0 消费**，但每日仍导出+提交（内容陈旧） | 随 P0 修复恢复新鲜度；若确认前端永久不消费，可讨论从 4 切片除名（属契约变更需拍板） |

---

## 四、问题汇总与建议

### P0（生产正确性，建议尽快修）

**P0-1　limit_pool / broken_pool 双表自 11-13 停更，竞价推送已连续 3 个交易日使用 5 天前的池子**

- **证据链**：
  1. 热层 auction.db（本次实拉）：`limit_pool MAX(date)=2026-11-13`、`broken_pool MAX(date)=2026-11-13`（market_breadth/bid_pool/strike_pool 均 11-18 正常）；
  2. 线上 broken_pool.json date=11-13，每日照常提交；
  3. 今日 09:25 竞价班（run 44215651420）日志原话：`(9:26 口径: 当日池未落 → 用池内最近日 2026-11-13 的周期判定 + 今日竞价)`、`昨日(2026-11-13)连板 7 只 → 竞价换手 Top5`、`llm 存档回填(regime=观察, prompt_ver=v3, 数据日=2026-11-13)`；
  4. 今日 15:15 EOD 班 backfill_broken 日志：窗口 2026-11-05~**11-13**（窗口上限=limit_pool 日期，源头断供导致自愈窗口也够不到新日期）。
- **根因**：重构 Commit-R1 删除 auction-label.yml（15:05 涨停池回补班）后，**写 limit_pool 的生产者（backfill_emotion --pool）没有在 crawl.yml 重建**。crawl.yml 现仅跑 `--breadth`（15:00 后）与 `--breadth-rt`。11-13 是 auction-label.yml 删除前最后一个交易日，与停更时点精确吻合。
- **影响面**（grep 实证消费方）：`auction_scan.py`（09:25 竞价班尺1接力健康/尺2/连板名单/换手——**出击选股与钉钉推送输入已陈旧 3 天**）、`llm_review.py`、`emotion_cycle.py`、`stage_candidates.py`、`backfill_broken.py`（窗口）、`strike_pool`（11-18 仍在写=基于陈旧池产出）。前端周期/六情绪区块已空态化（N6 已接受退化），**用户可见层无报错**，故三日无人察觉。
- **修复建议**（未动手）：crawl.yml 在 15:15 crawl-eod 班的 backfill_broken 步骤前（或独立步骤）加 `python scripts/backfill_emotion.py --pool`（7 天滚动窗口脚本现成，幂等），并加"limit_pool MAX(date) < 今日-1 → ⚠️"守卫；当日即可自愈回补 11-16/17/18 三天，竞价班明日恢复正确池。修复后 broken_pool/broken_pool.json 自动跟随恢复。
- **附带**：15:05 原班还有 7 天全字段滚动回补语义，删除清单（Sprint 计划 T2.3.2）将"limit_pool 链"一并划入删除，但 limit_pool 是库表非前端切片，N6"已接受退化"仅覆盖周期/六情绪展示，**未覆盖竞价班输入**——属重构遗漏而非有意取舍。

### P1（应尽快处理）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| P1-1 | 竞价班对"池子陈旧"仅注释级诚实，无告警 | 日志明示 11-13 但班次 success、无 ::warning:: | 随 P0 修复加守卫（`周期判定日期 < 昨交易日 → ::warning::`），避免下次断供再静默 3 天 |
| P1-2 | OPTIMIZATION_PROGRESS / handoff 声明的"11-18 观察项"已全过但文档未回写，且前端基线 230→282、bundle 指纹过时 | §1.2/§1.3 | 下次提交顺手回写：观察项打勾、基线更新 |
| P1-3 | CLAUDE.md（本地）架构段/cron 编排/§九多条仍停在重构前（auction-label/db_upload/backfill_positions/name_map 等已删对象） | §3.3 | 按 DATA_PIPELINE.md 重写架构段；带日期历史条目可留 |

### P2（可选）

| # | 问题 | 建议 |
|---|---|---|
| P2-1 | test.yml 无 alert-on-failure（门禁红无钉钉通知） | 照其余 7 个 workflow 模式补一个 job |
| P2-2 | 死码删除候选 7 文件（sixEmotion.js + 2 测试 + verify_slices.py + llm_picks_review.py + calibrate_probability.py + strike_gate_backtest.py）+ six_parity_fixture.py 共 8 | 见 §3.1/3.2，删除时同步跑两侧测试 |
| P2-3 | 根目录《项目复核报告_2026-11-17.md》位置 | 移 docs/ 或维持留档 |
| P2-4 | verify_slices.py 必炸（读已删文件） | 删除（并入 P2-2） |
| P2-5 | npm 未用依赖未盘点（package.json vs import 全扫） | 下次专项；当前 bundle 体积无压力 |
| P2-6 | broken_pool.json 前端 0 消费仍每日提交 | P0 修复后若确认无消费方，讨论除名（契约变更需拍板） |

### 建议删除清单汇总表（等拍板，本次一律未动）

| 文件 | 置信 | 连带 |
|---|---|---|
| `jiarenmens/scripts/verify_slices.py` | 高（必炸死码） | 无引用 |
| `jiarenmens/scripts/six_parity_fixture.py` | 高（对拍对象已删） | 无引用 |
| `stockboard-app/src/utils/sixEmotion.js` + `__tests__/sixEmotion.test.js` + `__tests__/sixParity.test.js` | 高（0 引用） | vitest 基线 282→约 27x，文档同步 |
| `jiarenmens/scripts/llm_picks_review.py` | 中（v3 已结案） | 建议先归档 llm_archive 结论 |
| `jiarenmens/scripts/calibrate_probability.py` | 中（结论已证不可行） | 无引用 |
| `jiarenmens/scripts/strike_gate_backtest.py` | 中（回测漏网） | 无引用 |

### 前 3 条建议（按优先级）

1. **修 P0-1**：crawl.yml 补 `backfill_emotion --pool` 步骤 + 陈旧守卫，一次修复竞价班三处陈旧输入，明日恢复；历史 11-16/17/18 由 7 天滚动窗口自动回补。
2. **P1-1 告警闭环**：竞价班对"周期判定日期落后"打 ::warning::（配合既有 alert-on-failure），让下一次数据断供当天可见，而非三天后审计才发现。
3. **P2-2 死码批删**：8 个文件零风险删除（verify_slices 必炸、sixEmotion 0 引用），同步刷新测试基线与文档，缩小后续会话的误读面。

---

*审计方法备注：生产状态判定以 origin/main 与线上产物为准（本地工作区可能落后，本次实证一次）；热层 auction.db 经 release_db --download-latest 实拉校验；全程未调用视觉模型，界面检查为程序化手段（构建/HTTP/bundle 字符串扫描）。*
