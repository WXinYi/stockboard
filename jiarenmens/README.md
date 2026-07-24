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
│   └── dashboard.html         # 生成的看板页面
├── scripts/
│   └── dashboard.py           # 看板生成脚本
└── src/
    ├── config.py              # 配置
    ├── spiders/               # 数据获取模块
    ├── storage/               # 存储模块
    ├── analysis/              # 分析模块
    └── utils/                 # 工具函数
```

## 技术栈

- Python 3.11+
- SQLite（数据持久化）
- Chart.js（前端图表）
- 纯请求模式，无需浏览器

## License

MIT
