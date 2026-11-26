# StockBoard 采集端

当前生产链路由 cron-job.org 触发 GitHub Actions，采集端只负责生成静态 JSON 和发钉钉；不再使用 `main.py` / `export_json.py` / `notify_daily.py` 作为生产入口。

## 生产入口

| 脚本 | 用途 |
|---|---|
| `scripts/player_snapshot.py --rank/--midday/--close` | 生成 `players.json` / `stocks.json` / `today.json`；midday/close 走 no-token 12 worker |
| `scripts/em_contract_probe.py --live` | 写 `today.json.emContract`，控制前端 EM 直连是否可用 |
| `scripts/export_market.py --market-only` | 生成 4 个市场切片 JSON |
| `scripts/cycle_push.py --session midday/eod` | 午盘/尾盘格局 + A 股共识钉钉推送 |
| `scripts/watchdog.py` | 关注选手即时钉钉；单独 workflow，每 5 分钟 |
| `scripts/crawl_cancel_alert.py` | 每日 15:45 检查定时采集班是否被掐 |
| `scripts/release_db.py` | `auction.db` / 历史 `crawl_data.db` 的 Release 三层存储同步 |
| `scripts/fetch_db.py` | 从 Release 恢复/查询历史数据库，供回测与本地排障 |

详细调度、数据契约和运维见 [`docs/DATA_PIPELINE.md`](../docs/DATA_PIPELINE.md)；当前状态与待办见 [`docs/OPTIMIZATION_PROGRESS.md`](../docs/OPTIMIZATION_PROGRESS.md)。

## 数据输出

生产目录：`../stockboard-app/public/data/latest/`

| 文件 | 结构 |
|---|---|
| `players.json` | `{date, rankTime, players[]}` |
| `stocks.json` | `{date, snapshot, updatedAt, stats[]}` |
| `today.json` | `{date, updatedAt, tradedPlayerIds, emContract, tradeConsensus}` |
| 市场切片 | `lianban_bid.json` / `market_breadth.json`（`broken_pool.json` 11-20、`hot_rank.json` 11-25 先后下线删除） |

`tradeConsensus` 按股票聚合当日买/卖，人数按选手去重，笔数不去重，方向未知不猜测；每班全量覆盖，不做并集。

## 关注名单

单一真源：`src/config/watched.py`（当前 9 人）。修改名单时只改这里；必要时同步前端 `stockboard-app/src/composables/useDataV2.js` 的 `FALLBACK_WATCHED` 冻结兜底。

`HIDDEN_UIDS` 保留隐藏号的 zh→userId 映射，只用于 `watchdog.fetch_hidden_trades()` 的 rtV2 流水通道；旧条目可保留但不再遍历时无害。

## 本地开发

系统 `python3` 是 3.9，跑全量会因 `dict | None` 报 import error；使用 Python 3.10+，本机建议 3.12：

```bash
cd /Users/xywang/stockboard/jiarenmens
/opt/homebrew/bin/python3.12 -m unittest discover -s scripts -p 'test_*.py'
/opt/homebrew/bin/python3.12 -m py_compile scripts/*.py
```

如需恢复历史库做回测：

```bash
python scripts/fetch_db.py --latest          # Release 热层 → data/crawl_data.db
python scripts/fetch_db.py --auction         # auction-state 热层 → data/auction.db
python scripts/fetch_db.py --month 2026-07
python scripts/fetch_db.py --list
```

## 数据口径红线

1. midday/close 的 no-token payload 不带 `ctToken` / `utToken` / `randomCode` / `deviceId`。
2. EM rtV2 调仓日期必须 `YYYYMMDD`，客户端过滤仍用 `YYYY-MM-DD`。
3. 缺失值写 `null` / `None`，不能用 0 冒充。
4. A 股=6 位数字，港股=5 位；展示层过滤，数据层保留全量。
5. 榜单默认每榜 300 人，`EM_RANK_LIMIT=500` 可回滚；全天请求数按实测 7005 次口径收口。
6. 数据文件被 ignore 但已跟踪，提交必须 `git add -f <具体文件>`，不要 `git add -A`。
7. `crawl.yml` 的 `concurrency.cancel-in-progress: false` 不能改回 `true`。

## 技术栈

- Python 3.11+（本机用 3.12 跑测试）
- SQLite 用于历史库/auction.db；页面生产链路消费静态 JSON
- GitHub Actions + cron-job.org + GitHub Pages
- 钉钉 webhook 统一走 `src/notify/dingtalk.py`
