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
| 单元测试 | 本地 `89 tests OK / exit=0`；GitHub Actions `test.yml` run `42865490695` 同为 `89 tests OK` |
| 基线更正 | 改造前是 `69 tests + 1 error`（`test_llm_review` 顶层 `sys.exit`）；此前口头说的「9 通过 0 失败」是脚本自定义汇总，不准确 |
| 语法 / lint | `py_compile` 全通过；IDE lint 0 诊断 |
| 真实库导出 | 6106 个 `players/*.json`；3577 名选手新键与共享模块算法一致 |
| `_k` 迁移模拟 | 真实产物 3577 名选手的历史旧键全部命中，不会重推 |
| 失败注入 | 上传成功不回滚 / POST 失败回滚旧字节 / 备份失败不删旧，3 场景单测通过 |
| 去重量化 | 本地库 `213646 → 140818` 行（1.52× 冗余）；某选手买入行 `62 → 23`（旧口径放大 2.7×） |
| YAML | 13 个 workflow 全部解析通过（曾发现并修复 2 处本轮引入的 `run: echo ": ${{ }}"` 语法错误） |
| push 班 | 盘后 `crawl.yml` 数据采集/导出/上传/提交均 `skipped`，仅部署 |

### 业务口径变化（需知情）

- **推测持仓 `bc/sc` 计数与置信度会变准**（旧值被日快照重复放大），页面数值与改造前不同 —— 是修正缺陷，不是新口径。
- `_k` 算法变更但有迁移兼容；`players/*.json` 多一个 `_k_legacy` 字段（前端不消费，体积略增）。
- **未改变**：抄作业 `score`、重仓共识、`is_quality`、钉钉推送触发条件、`watchdog.yml` 调度/频率/状态语义、`workers=20`、所有采集档位与调用数。

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

### A. 首班验证 — 波 2 的硬前置（11-11 09:26）

- [ ] 首班耗时（期望 6–8 分钟，含约 220 名新选手初始化；**目前仍是预测，未实测**）
- [ ] watchdog 让位日志次数、有无「盯盘已降级」告警
- [ ] 日报是否正常、有无重复推送 —— 这是 `_k` 迁移的直接验收
- [ ] 页面盘中显示昨收持仓、15:15 EOD 后恢复当日真值
- [ ] 若不符：先归因（新选手数量 / EM 限流 / 快照复制是否生效），**不动 EOD 拆档**

### B. 波 2：提速收尾

- [ ] EOD 拆校准档 `level="eod"`（收益 + 真实持仓 + 调仓 1 页 = 3 调用/人），`full` 保留给新选手初始化/手动全量
- [ ] `dimensions` 降频 + **「缺字段不覆盖」合并逻辑**（复用 `main.py` 已为 `hourly` 预留的旧档案合并），否则 `max_drawdown`/`win_rate` 会被默认值清空、优质选手集合漂移
- [ ] 拍板 `win_rate` 口径：回撤周级 + 胜率诚实置 `None`（推荐），或胜率仅对优质选手日级拉取
- [ ] `requests.Session` **按线程隔离**（`asyncio.to_thread` 多线程，全局 Session 非线程安全）+ `HTTPAdapter(pool_maxsize=20)`
- [ ] `timeout` 20→8s、重试 3→2、分页 `sleep(0.2)` 仅在确有下一页时
- [ ] 所有提速改动加 env 开关（`EM_EOD_LEVEL` / `EM_USE_SESSION`）便于灰度回滚
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
