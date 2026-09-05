# StockBoard - 股票数据看板

个人投资数据追踪工具，定时拉取公开数据，生成可交互的 Web 分析看板。

## 功能

- **数据看板** — HTML 看板，含选手排行、持仓分析、调仓共识等模块
- **多维度排序** — 按总收益 / 年收益 / 月收益 / 周收益 / 日收益 / 净值 灵活切换
- **标的质量筛选** — 按运行时长 + 回撤过滤优质标的
- **重仓共识** — 按加权仓位发现市场重仓方向
- **增量更新** — 每天运行一次，数据按日期隔离存储
- **SQLite 存储** — 数据持久化到本地数据库，支持历史回溯

## 数据持久化（2026-08-31 起）

`crawl_data.db` **不再进 git**，持久化走 GitHub Release 三层存储（热层 40 采集日 / 温层 12 周滚动 / 冷层永久），完整方案与运维手册见 [`docs/DATA_PIPELINE.md`](../docs/DATA_PIPELINE.md)。

本地取数（回测等场景）：

```bash
python3 scripts/fetch_db.py --latest          # 热层(最近40采集日) → data/crawl_data.db
python3 scripts/fetch_db.py --month 2026-07   # 单月冷层
python3 scripts/fetch_db.py --list            # 查看可用归档
```

⚠️ 每次 CI run 开头都会从热层恢复 db——本地手动跑 `export_json.py` 前先确保库已恢复（`fetch_db.py --latest`），空库导出会产出退化数据。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行数据采集
python main.py

# 生成看板
python scripts/dashboard.py
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--limit` | 100 | 每类数据获取数量 |
| `--workers` | 20 | 并发数 |
| `--test` | - | 测试模式（只处理 10 个） |
| `--no-skip` | - | 不跳过已有数据 |
| `--checkpoint-reset` | - | 重置进度记录 |

## 项目结构

```
stockboard/
├── main.py                    # 数据采集入口
├── requirements.txt           # 依赖
├── data/
│   ├── checkpoint.json        # 进度记录
│   ├── crawl_data.db          # SQLite 数据库(运行时从 Release 热层恢复, 永不进 git, 见 docs/DATA_PIPELINE.md)
│   ├── auction.db             # 竞价/情绪/涨停池（auction_scan 写，Actions 与本地共享）
│   ├── analysis.db            # 周期引擎判定（本地独享，不提交）
│   ├── intraday.db            # 盘中监控数据（已停用，本地独享）
│   ├── archive/               # fetch_db.py 回测产物（gitignore，勿 git add -A 误提交）
│   └── dashboard.html         # 生成的看板页面
├── scripts/
│   ├── auction_scan.py        # 竞价扫描全流程（评分漏斗 + V5 周期闸门 + 钉钉）
│   ├── cycle_push.py          # 午盘/尾盘格局钉钉推送（每日三推之二/三）
│   ├── cycle_brief.py         # 当前超短格局报告 CLI
│   ├── backfill_emotion.py    # 市场宽度/涨停池历史回补
│   ├── release_db.py          # ★ crawl_data.db ↔ GitHub Release 三层存储(热/温/冷)
│   ├── prune_crawl_db.py      # 保留窗口外采集日删除 + VACUUM（封顶 db 体积）
│   ├── fetch_db.py            # ★ 回测取数 CLI：Release 归档 → 本地(--latest/--week/--month/--range)
│   ├── intraday_monitor.py    # 盘中实时监控（已停用，代码保留）
│   ├── dashboard.py           # 看板生成脚本
│   └── ...
└── src/
    ├── config.py              # 配置
    ├── spiders/               # 数据获取模块（auction_spider 含 KPL 实时盘口/涨停池）
    ├── storage/               # 存储模块
    ├── analysis/              # 分析模块
    │   ├── auction_funnel.py  # 竞价评分漏斗
    │   ├── emotion_cycle.py   # ★ 超短情绪周期引擎（六段/主线/龙头谱系）
    │   └── stage_candidates.py# 阶段候选池（周期→战法模式→候选）
    └── utils/                 # 工具函数
```

## 超短情绪周期系统（2026-08 新增）

围绕「情绪周期 → 主线 → 龙头谱系 → 阶段候选」的超短决策辅助，交付形态是**钉钉每日三推**（09:25 竞价 / 13:05 午盘 / 14:40 尾盘，搭现有 crawl/auction workflow 便车）。

- **周期引擎** `src/analysis/emotion_cycle.py`：六段量化判定（冰点/启动/发酵/高潮/分歧/退潮）。⚠️ 涨停池 `PidType=5` 是"≥5板"封顶桶，真实连板高度按个股逐日连续在池反推。阈值在 `CYCLE_CFG`，**待回测校准**。
- **V5 周期闸门**（`auction_scan.py:screen_v5` 第五刀）：退潮/冰点 V5 静默、分歧仅主线内半仓、发酵/高潮仅主线板块内；闸外转 `v5_off_cycle` 照常落库，`v5_results.cycle_stage` 供按周期分组回测。
- **数据**：`market_breadth`（250 天涨停/炸板率）与 `limit_pool` 全字段（涨停时间/封单/主力净额）由 `backfill_emotion.py` 回补。⚠️ `auction-label.yml` 收盘只续 `--pool` **不含宽度**（2026-09-05 发现宽度停在 8/28 致 9/4 误判"分歧"），宽度日常更新已挂 `crawl.yml` 收盘班（≥15:00 班次），`cycle_brief.py` 计算前另有断档自愈兜底。
- **常用命令**：
  ```bash
  python scripts/cycle_brief.py                        # 当前格局报告
  python scripts/cycle_push.py --session eod --dry-run # 尾盘推送试跑
  python scripts/backfill_emotion.py --breadth         # 宽度数据回补
  ```

## 我的纪律卡（2026-09-05 新增）

盘面页入口卡 + `/market/discipline` 详情页：今日定性→仓位上限（`STAGE_RULES`，双实现同源 `emotionCycle.js`）、盘前五数、冰点确认四菜单（A+B 试错许可 / C+D 仓位恢复）、持仓处理价位表（触价高亮 + 板块涨停统计 + rtV2 调仓自动核对）、每日三行卡（localStorage `sb-discipline-log`）。

数据链（每天 12 班自动刷新）：
```
jiarenmens/data/my_positions.json   # 手编配置: 价位表/板块归属/weekly_focus, 每周复盘更新
  └─ export_json.py: build_my_positions()   # rtV2 调仓轧差 + GetPlateInfo_w38(HisLimitResumption) 板块统计 + 腾讯行情
       └─ stockboard-app/public/data/latest/my_positions.json   # 前端 loader.fetchMyPositions
```
板块名为 KPL 概念聚类（每日漂移，如"算力(液冷)"），配置里写关键词即可（包含匹配）；`其他`/`ST板块` 桶不参与判定。调仓核对为近 120 笔轧差估算，盘支持当日回转（T+0）。

## 出击列表选股（2026-09-05 升级）

盘面页「🎯 今日出击」Tab = 唯一出击展示位（周期详情页已移除该模块）：阶段闸门×九宫格 → 四池候选（龙头谱系/阶段扩展/半路/退潮火种）→ 评分排序（`leaderBattle.js` computeStrike，纯规则可回测）。每只候选带 定位标签（龙头/中军/补涨/跟风，跟风强制回避）、买点三件套（`candTipOf` 共用函数）、按闸门换算的建议仓位；启动期含首板试错池（早封+主力净买），退潮期火种入候选。Python 对偶 `src/analysis/stage_candidates.py` 同步候选范围与状态语义。

特殊标记「🔥 竞价换手TOP5」= 昨日连板股中今晨 9:25 竞价实际换手率前五（口径同 `scripts/lianban_bid_hs.py`：KPL turnover_ratio 优先，0值腾讯分时 0930 首行÷流通市值补算）：
```
export_json.py: build_lianban_bid()   # limit_pool 昨日连板(pid≥2) × bid_pool 竞价换手
  └─ stockboard-app/public/data/latest/lianban_bid.json   # 前端经 loadBattleData 注入 computeBattle 标 c.bidTop
```

## 技术栈

- Python 3.11+
- SQLite（数据持久化）
- Chart.js（前端图表）
- 纯请求模式，无需浏览器

## License

MIT
