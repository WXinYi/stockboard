# StockBoard - 股票数据看板

个人投资数据追踪工具，定时拉取公开数据，生成可交互的 Web 分析看板。

## 功能

- **数据看板** — HTML 看板，含选手排行、持仓分析、调仓共识等模块
- **多维度排序** — 按总收益 / 年收益 / 月收益 / 周收益 / 日收益 / 净值 灵活切换
- **标的质量筛选** — 按运行时长 + 回撤过滤优质标的
- **重仓共识** — 按加权仓位发现市场重仓方向
- **增量更新** — 每天运行一次，数据按日期隔离存储
- **SQLite 存储** — 数据持久化到本地数据库，支持历史回溯

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
│   ├── crawl_data.db          # SQLite 数据库
│   ├── auction.db             # 竞价/情绪/涨停池（auction_scan 写，Actions 与本地共享）
│   ├── analysis.db            # 周期引擎判定（本地独享，不提交）
│   ├── intraday.db            # 盘中监控数据（已停用，本地独享）
│   └── dashboard.html         # 生成的看板页面
├── scripts/
│   ├── auction_scan.py        # 竞价扫描全流程（评分漏斗 + V5 周期闸门 + 钉钉）
│   ├── cycle_push.py          # 午盘/尾盘格局钉钉推送（每日三推之二/三）
│   ├── cycle_brief.py         # 当前超短格局报告 CLI
│   ├── backfill_emotion.py    # 市场宽度/涨停池历史回补
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
- **数据**：`market_breadth`（250 天涨停/炸板率）与 `limit_pool` 全字段（涨停时间/封单/主力净额）由 `backfill_emotion.py` 回补，`auction-label.yml` 收盘后自动续当日。
- **常用命令**：
  ```bash
  python scripts/cycle_brief.py                        # 当前格局报告
  python scripts/cycle_push.py --session eod --dry-run # 尾盘推送试跑
  python scripts/backfill_emotion.py --breadth         # 宽度数据回补
  ```

## 技术栈

- Python 3.11+
- SQLite（数据持久化）
- Chart.js（前端图表）
- 纯请求模式，无需浏览器

## License

MIT
