# StockBoard 优化进度与待办

> 最后更新：2026-11-11（北京）
> 依据：2026-11-10 架构与代码复查报告、2026-11-11 优化方案（均在对话中给出，未单独落盘）
> 关联：`docs/DATA_PIPELINE.md`（数据管线与事故史）

## 状态总览

| 阶段 | 状态 | commit | 说明 |
|---|---|---|---|
| 前置 P0 watchdog 静默漏推修复 | 已完成 | `88bbc010da9` | 分离 `api_ok` / `data_empty` |
| 前置 P1 采集提速 | 已上线、**待生产验证** | `b5c97232d0d` | 取消首班全员 full + 盘中持仓改复制校准快照 |
| 波 1 正确性护栏 | 已完成 | `9c760449859` | 本轮主体，见下 |
| 波 1.5 使用者时点体验 | 已完成 | 本轮提交（见 `git log`） | 持仓时点标注 / 竞价页自动刷新 / 尾盘推送去重 |
| 进度文档 | 已落盘 | `a5c6eadc676` | 本文件 |
| 波 2 观察 + 提速收尾 | **未开始** | — | 硬前置＝11-11 09:26 首班日志 |
| 波 3 收敛 | 未开始 | — | 依赖波 2 稳定 3 个交易日 |
| 波 4 数据模型 | 未开始 | — | 单独立项，不与提速混发 |

## 已完成明细

### 波 1（`9c760449859`）

1. **`_k` 稳定键 + 平滑迁移**：新增 `jiarenmens/src/utils/trade_key.py`。键只由 `zh_id|交易日|代码|方向` 决定（16 位 hex），去掉会变的 `price/trades_count/仓位档`；导出同时输出 `_k_legacy`，`notify_daily` 判定「新键或旧键任一命中历史 seen 即已推过」，写入只用新键 → 上线第一班不会全量重推。
2. **叠加文件口径统一**：`watchdog.merge_player` 今日行也带同源 `_k`/`_k_legacy`。
3. **导出确定性缺陷**：`all_trades_dedup` 一次去重供 `buy_history`/`trades_by_pid`/推测持仓共用；推测持仓改按选手分组遍历；`name_map` 疑似清仓循环从「卖出预警」循环内移出。
4. **Release 上传事务化**：`upload_asset` 改为「删前备份 → 删旧 → 传新 → 失败回滚」；备份读不回来则不删旧（宁可不更新，不断链）。修复 11-10 演练实测的热层断链事故。
5. **prune 小修**：采集日取 `trades ∪ positions` 并集、排除空串/NULL、`try/finally` 关连接。
6. **代码门禁**：新增 `.github/workflows/test.yml`（仅代码提交/PR 触发，不挂 75+ runs/日 数据班）；修复 `test_llm_review` 顶层 `sys.exit` 与 `test_api_requests` 顶层 playwright 导入。
7. **可观测最小集**：补 `auction-db-maintenance` 失败告警 job；日报/共识推送失败加 `::warning::`；`notify_failure` 发送失败升级 `::error::` 注解但不判红。
8. **更正过时注释**：三处「胜率源不可得」→ 新协议 `dayWinRate` 可得（近 20 日上涨天数占比），并标注「真 0% 与源缺失仍不可区分」的已知限制。

### 验证证据（已完成）

| 项目 | 结果 |
|---|---|
| 单元测试 | 波 1：本地 `89 tests OK`、GitHub Actions `test.yml` run `42865490695` 同为 `89 tests OK`；波 1.5 后本地 `99 tests OK` |
| 基线更正 | 改造前是 `69 tests + 1 error`（`test_llm_review` 顶层 `sys.exit`）；此前口头说的「9 通过 0 失败」是脚本自定义汇总，不准确 |
| 语法 / lint | `py_compile` 全通过；IDE lint 0 诊断；前端 `vite build` exit=0（101 模块 / 2.07s） |
| 真实库导出 | 6106 个 `players/*.json`；3577 名选手新键与共享模块算法一致 |
| `_k` 迁移模拟 | 真实产物 3577 名选手的历史旧键全部命中，不会重推 |
| 失败注入 | 上传成功不回滚 / POST 失败回滚旧字节 / 备份失败不删旧，3 场景单测通过 |
| 去重量化 | 本地库 `213646 → 140818` 行（1.52× 冗余）；某选手买入行 `62 → 23`（旧口径放大 2.7×） |
| YAML | 13 个 workflow 全部解析通过（曾发现并修复 2 处本轮引入的 `run: echo ": ${{ }}"` 语法错误） |
| push 班 | 盘后 `crawl.yml` 数据采集/导出/上传/提交均 `skipped`，仅部署 |
| 尾盘去重 | `test_cycle_push.py` 10 个单测：state 命中跳过钉钉、首次推送落状态、`--force` 补推、`--dry-run` 无副作用、推送失败不占状态 |
| 北京时间窗口 | `Intl en-GB + h23` 实测 `01:25Z→925`(窗口内)、`01:39Z→939`(窗口内)、`06:51Z→1451`(窗口外)、`16:00Z→0`(窗口外) |

### 波 1.5：使用者时点体验（不依赖首班验证）

1. **持仓/收益时点标注**：抄作业页变动摘要、核心重仓、重仓共识页、选手详情页统一标注「持仓/收益为最近收盘校准值，盘中不刷新，15:15 后更新当日」；`App.vue` 页脚 title 补全三类数据口径（盘面 KPL 实时 / 选手收盘校准 / 竞价 09:25 快照）。解决 P1 之后「页面标着今天、数字其实是昨收」与「变动 0 看起来像今日无操作」的误导。纯文案，不改数字。
2. **竞价页自动刷新**：`useUX.js` 在 09:20–09:40（北京）窗口额外探测 `auction.json` 的 `date|generated_at`，变化即触发全局刷新；`AuctionTab.vue` inject/watch `refreshTick` 重新 `load()`（配合 `clearDataCache()`）。修复「竞价班 09:28 已上线、已打开页面因 KeepAlive 停在昨日快照」。不改 `index.json`，避免与采集班并发写。
3. **午盘/尾盘格局推送**：`crawl.yml` 尾盘窗口由 `[1430,1455]` 收窄到 `[1448,1455]`（只命中 14:51 末班）；`cycle_push.py` 增加按 `(交易日, session)` 的幂等（state 落 `data/.cycle_push_state.json`，随 `git add jiarenmens/data/` 持久化）；`cycle-eod.yml` 增加 `contents: write` 与状态提交步骤（`[skip ci]`，文件不存在时安全跳过）。`--force` 可手动补推，推送失败不占状态。
   ⚠️ **11-11 复核更正**：这一步当时是在给一条**从未运行的链路**加去重 —— 见下方「cron 核对与首班实测」。

### cron 核对与首班实测（2026-11-11，三份证据交叉验证）

证据：GitHub `repository_dispatch` / `workflow_dispatch` run 历史（近 9 个交易日）、本地 `~/.claude/reference/cron-job-org-api.md` 的 jobId 清单、历史 run 步骤级结论。

**真实触发方式**（此前文档/注释一直写错）：

| 时刻 | 触发方式 | 目标 |
|---|---|---|
| 09:25 竞价、09:26–14:51 采集 20 班、15:05 涨停池回补 | **`workflow_dispatch`**（body `{"ref":"main"}`，无 event_type） | `auction.yml` / `crawl.yml` / `auction-label.yml` |
| 09:30 `watchdog-open`、09:36–15:00 每 5 分钟 `watchdog`、10:03 `llm-1000`、15:15 `crawl-eod` | `repository_dispatch`（带 event_type） | `watchdog.yml` / `llm-1000.yml` / `crawl.yml` |

- 20 班精确时刻：`09:26 09:38 09:55 10:00 10:15 10:30 10:45 11:00 11:15 11:30 13:05 13:15 13:30 13:45 14:00 14:10 14:20 14:30 14:40 14:51`；`llm-1000` 11-07 已由 10:00 错峰到 10:03。
- **`crawl.yml` 午盘/尾盘推送步骤原条件 `github.event_name == 'repository_dispatch'`** 与 20 个 `workflow_dispatch` 班不匹配 → 11-10 13:05 run `42759878580` 实测两步均 `skipped`。`cycle-eod.yml` 在 cron 清单里**没有 14:45 job**，历史仅 1 次演练手工触发 → 午盘/尾盘格局钉钉**从未自动发出**。
- 因此更正：复查报告里「尾盘最多推 2–3 条、需去重」判断错误，实际是 **0 条**；波 1.5 的去重改动当时无实际效果（无害，但也没解决问题）。
- 契约冗余（不影响功能）：`crawl.yml` 的 `types: [crawl]`、`auction.yml` 的 `[auction]`、`auction-label.yml` 的 `[label]`、`cycle-eod.yml` 的 `[cycle-eod]` 都没有对应 cron job 发送。
- **11-11 已修**（用户拍板保留并推送）：两个推送步骤的 `if` 改为 `github.event_name != 'push' && trading != 'false'`；午盘窗口收窄到 `[1300,1314]`（只命中 13:05 班，避开 13:15）、尾盘 `[1448,1455]`（只命中 14:51）。验证：20 班中两窗口各命中 1 班 + 幂等 state 双保险。

**首班实测（不符合预期）**：11-11 09:26 run `42938935321` → `cancelled`（09:38:27 被 09:38 班按 `concurrency.group: pages-${{ github.ref }}` + `cancel-in-progress: true` 掐掉）。被取消前只到 **1338/2270** 人，导出/提交/钉钉全部 `skipped`，今日截至核查时**没有任何 crawl 班的数据落盘**。

- 正确部分：档位 `trades` ✓、检查点跨日清空 ✓、持仓复制校准快照日志「校准 2026-11-10 快照」✓、竞价班 09:25 success ✓。
- 不符合部分：P1 预测首班 6–8 分钟，实测 >12 分钟且被取消；日志有大量 `spzhapi.dfcfs.cn … Network is unreachable` 重试（01:31:35 起）拖慢；班距仅 12 分钟（09:26→09:38）。
- 影响：波 2 的 EOD 拆档**不能按原计划直接做**，需先解决「班时 > 班距」与 EM 网络失败放大。优先项调整为波 2 第 4 步（`requests.Session` 按线程隔离、`timeout` 20→8s、重试 3→2），并评估把新选手初始化的 `full` 档拆轻（当前首班有选手走 6 调用 full）。
- 未擅自改 `concurrency`：去掉 `cancel-in-progress` 会让 20 班排队累积或并发写同一热层/git，风险大于收益，待单独评估。

### watchdog 中午巡检后的三项修复（2026-11-11 12:59，全部单文件 `watchdog.py`）

巡检实证：月儿兰当日 3 笔（10:21 班推 2 笔 + 11:16 班推 1 笔，叠加名单可证）**都被推送过**，用户感知"没推送"的根源是 09:30–10:16 全员误标隐藏静默事故（已由 10:19 提交修复）+ 推送延迟 45 分钟。但巡检暴露两个机制性缺陷 + 一个通道失效：

1. **seed 防剧透守卫**（机制缺陷一）：`fetch_base_player` 顺带返回底稿 `Last-Modified`，新增 `base_is_spoiled()`——底稿为**今日（北京）部署**时跳过 `seed_baseline`，交由 `decide_pushes` 正常比对。原因：crawl 盘中班可能先于 watchdog 首次成功把当日操作部署到页面，seed 会把这些"未经钉钉"的操作永久静默（今天月儿兰 2 笔靠 Pages 部署延迟侥幸逃脱）。昨日底稿 seed 语义不变（防与日报重复）；无 header/解析失败保持现行为（宁可误吞不误推，与现状一致）。**dry-run 实测**：线上底稿为今日 11:35 部署、含全部 22 笔，修复前会被 seed 吞成 0 笔推送，修复后正确检出 22 笔。
2. **推送摘要落日志**（机制缺陷二）：推送后逐笔打印 `选手+方向+股票(tc)`，解决"消息体只发钉钉不进日志、事后无法对账"（当日 15 笔的构成只能间接推断）。
3. **隐藏号 9061 显式失败**（通道失效）：巡检发现武研琳（900450475）的 relocate 接口持续报 `code=9061 该组合不存在`（09:36 班起多班出现，本地直连复现 0 条流水）。原代码把 `_call_api` 返回 None 静默折算成"今日暂无操作"，掩盖通道失效。现 `fetch_hidden_trades` 对 None 显式抛错：每班打印 🚨 提示（含处置建议）+ 消息脚注"拉取失败"。**另两个隐藏号（我嘚财富/我要再来一次）接口正常，流水最新到当日**。✅ **11-11 13:5x 已决**：经 userId 铁证（采集库 `6487375946169702 ≠ 1407047413230182`）排除"改名混淆"后，用户确认移出名单。同步清理 7 处：`main.py` 名单、`watchdog.py` `HIDDEN_UIDS`、`api_detail.py` `NEW_USER_IDS`、`notify_daily.py` 文案、`useData.js` `FALLBACK_WATCHED`、`screen_dragon.py` `TRACKED`/显示列表、`test_watchdog.py` 数据。名单 11→10 人；历史 `players/900450475.json` 保留可查；`9061` 提示随之消失。

验证：`104 tests OK`（新增 `TestBaseSpoiled` 5 例：今日剧透/昨日正常/空 header/解析失败/GMT 跨日按北京日期）；dry-run 复测 9061 三重可见；lint 0 诊断。未动：`_call_api`（共享模块，采集链路共用）、`concurrency`、`HIDDEN_UIDS` 名单、watchdog 调度频率。

### 业务口径变化（需知情）

- **推测持仓 `bc/sc` 计数与置信度会变准**（旧值被日快照重复放大），页面数值与改造前不同 —— 是修正缺陷，不是新口径。
- `_k` 算法变更但有迁移兼容；`players/*.json` 多一个 `_k_legacy` 字段（前端不消费，体积略增）。
- **波 1 未改变**：抄作业 `score`、重仓共识、`is_quality`、`watchdog.yml` 调度/频率/状态语义、`workers=20`、所有采集档位与调用数。
- **波 1.5 改变**：竞价页在盘前窗口会自动刷新；持仓类页面新增时点说明（纯文案，数字未变）；`cycle_push.py` 增加幂等 state、`cycle-eod.yml` 新增 `contents: write`。⚠️ 午盘/尾盘推送的实际启用是 11-11 修 `if` 条件后才生效（此前是 0 条，不是去重后的 1 条）。

## 明确未做及原因（防止误以为遗漏）

| 未做 | 原因 |
|---|---|
| per-connection `PRAGMA foreign_keys=ON` | 实测孤儿行=0，但开启后「潜在 INSERT 失败」会变成整班硬崩溃；代码全是手动 DELETE，收益极小、风险不对称 |
| `positions` 唯一索引 | 需先改写入去重，风险叠加，留波 4 |
| `buyers/sellers` 去重 | 按笔数 append 是与 `weight=trades_count×2` 配套的有意口径，改了会变 score 排序 |
| `seen` 按日清理 | 会改变去重语义，需单独评估 |
| `notify_failure` 判红 | 钉钉单点故障会刷红全部 12 个 workflow |
| `timeout-minutes` | 需按各 workflow 历史耗时定值，拍脑袋会误杀班次 |
| 胜率「真 0%」与「源缺失」区分 | 要改 `_opt_rate` + `export_json` 两处展示口径，属业务变化 |
| 波 2/3/4 | 波 2 必须等首班验证；现在动就是叠加未验证改动 |

## 待办（按依赖顺序）

### A. 首班验证 — 波 2 的硬前置（11-11 09:26，已实测：不符合预期）

- [x] 首班耗时：预测 6–8 分钟 → **实测 12 分 18 秒仍被取消**（1338/2270 人），见上方「首班实测」
- [x] 档位 `trades`、快照复制、竞价班 09:25 success、watchdog 各班 success：均符合预期
- [ ] watchdog 让位日志次数、有无「盯盘已降级」告警（今日继续观察）
- [ ] 日报是否正常、有无重复推送 —— `_k` 迁移的直接验收（首班被取消，未产生日报，需看后续成功班）
- [ ] 页面盘中显示昨收持仓、15:15 EOD 后恢复当日真值
- [ ] 波 1.5 验收：09:28 竞价页上线后已打开页面能否 60s 内自动刷新；持仓页时点说明；午盘 13:05 / 尾盘 14:51 各 1 条钉钉
- [ ] 首班与竞价页的错位窗口（今日因首班被取消而拉长）
- [ ] **新增前置**：解决「班时 > 12 分钟班距」后再谈 EOD 拆档；优先做波 2 第 4 步（Session / timeout 8s / 重试 2）与新选手初始化档位拆轻

### B. 波 2：提速收尾

- [ ] EOD 拆校准档 `level="eod"`（收益 + 真实持仓 + 调仓 1 页 = 3 调用/人），`full` 保留给新选手初始化/手动全量
- [ ] `dimensions` 降频 + **「缺字段不覆盖」合并逻辑**（复用 `main.py` 已为 `hourly` 预留的旧档案合并），否则 `max_drawdown`/`win_rate` 会被默认值清空、优质选手集合漂移
- [ ] 拍板 `win_rate` 口径：回撤周级 + 胜率诚实置 `None`（推荐），或胜率仅对优质选手日级拉取
- [ ] `requests.Session` **按线程隔离**（`asyncio.to_thread` 多线程，全局 Session 非线程安全）+ `HTTPAdapter(pool_maxsize=20)`
- [ ] `timeout` 20→8s、重试 3→2、分页 `sleep(0.2)` 仅在确有下一页时
- [ ] 所有提速改动加 env 开关（`EM_EOD_LEVEL` / `EM_USE_SESSION`）便于灰度回滚
- [ ] **收盘空窗**（使用者视角）：15:00 收盘到约 15:41 EOD 部署之间页面仍是盘中数据。除压缩 EOD 外，可评估「15:05 轻量定稿班」——只拉真实持仓+收益（不拉 info/dimensions），让页面 15:10 左右先有当日真值，EOD 班再全量归档
- [ ] watchdog 解耦：cron 错峰 +2 分钟 → 独立 EM token → 仅 EOD 班单独提 workers（按效果递进）
- [ ] **明确不做**：`workers` 上调（与 watchdog 共用 EM token，收益远小于风险）

### C. 波 3：收敛

- [ ] `players/*.json` 写前比较内容 hash，只重写变化文件；全表 SQL 下推日期
- [ ] 19 处 `except Exception` 改计数汇总、超阈值非零退出，阻止降级数据随 `[skip ci]` 上线
- [ ] 状态载体收敛：Release 热层唯一真源，git 只放静态产物，cache 只放构建物；共写资源加 `concurrency`
- [ ] 「预期班次未到」心跳；按历史耗时定值加 `timeout-minutes`；静默跳过步骤判红
- [ ] 代理最小加固（先「只记录不拦截」灰度 1 天）：Origin 白名单 + 限流 + OPTIONS + Worker 补 `/kpl-lhb`；SCF URL 与 KPL token 外置
- [ ] 口径修复：`weekly_return` 恒 0 使 `is_quality` 周权重失效；`hq` 恒空数组使「综合高分」恒 0
- [ ] **`max_drawdown` 窗口偏差**：实测 `20d=12.22` vs `all=45.37`，而 `is_quality` 把它当「历史最大回撤」作风险除数 → 会改变优质选手集合，优先级高于胜率标签
- [ ] 前端标签「胜率」改「近20日上涨天数」；`win_rate` 真 0% 与源缺失区分

### D. 波 4：数据模型（单独立项）

- [ ] `trades` 改「按 `(zh_id, trade_date, stock_code, direction)` UPSERT 事件表 + 最近快照表」，消除 1.5× 冗余
- [ ] `positions` 唯一索引（先改写入去重）
- [ ] 迁移脚本 + 新旧口径对账；牵动 `export_json` / `notify_daily` / watchdog 流水通道 / 回测 / 采集覆盖守卫

## 明确不做（避免过度工程）

- `workers` 上调到 30–40
- 引入 Pinia / 后端服务 / 换数据库
- 恢复 21 分漏斗 / v5 / 打标回测（2026-09-06 已整链删除，按旧文档优化是做无用功）
- 盘中追全员持仓实时性（业务已确认「收盘后更新即可」）
- 波 4 与波 2 混发

## 需拍板

1. `dimensions` 降频后 `win_rate` 的展示口径（影响 `is_quality` 与页面）
2. 代理加固的灰度策略（Origin 白名单可能误伤 dev / 自定义域名 / PWA）
3. 波 4 立项时机

## 回滚点

- `git revert 9c760449859` 可整体回退波 1；`trade_key.py` + 三处调用方为一组，回退后历史 `seen` 仍以旧键命中。
- 前置提交 `88bbc010da9` / `b5c97232d0d` 各自独立可回退。
- 生产热层由 Release 热/温/冷三层快照兜底；11-10 演练用的 `/tmp/sb_drill/*` 是临时文件，**不作为长期回滚依据**。
