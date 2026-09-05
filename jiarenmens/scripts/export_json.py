"""
导出 SQLite 数据为 JSON，供 Vue 看板使用

输出:
  latest/summary.json      — 全量聚合数据（调试参照 + verify 基准，前端不再 fetch）
  latest/core.json         — 日期/爬取时间/高手数/今日操作选手/上榜数 等核心元信息
  latest/copy.json         — 抄作业信号 (copyTradeSignals) + 卖出预警 + 疑似清仓
  latest/stocks.json       — 重仓共识 stockStats
  latest/name_map.json     — 当日被引用选手 name→id 子集映射
  latest/changes_summary.json — 持仓变动计数摘要（无明细）
  latest/players/*.json    — 选手详情（按需加载）

用法:
    python scripts/export_json.py [--date 2026-07-24] [--out ../stockboard-app/public/data]
"""
import sqlite3, json, os, argparse, hashlib, sys
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"


# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def safe_float(v, default=0.0):
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def safe_int(v, default=0):
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def parse_json(v, default=None):
    """解析 JSON 字符串为 Python 对象"""
    if default is None:
        default = []
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return default
    return v if v is not None else default


# ═══════════════════════════════════════════════
# 高手判定（与前端 isQuality 逻辑一致）
# ═══════════════════════════════════════════════

def is_quality(p):
    days = safe_int(p.get("days"))
    if days < 200:
        return False
    daily = safe_float(p.get("daily_return"))
    weekly = safe_float(p.get("weekly_return"))
    monthly = safe_float(p.get("monthly_return"))
    yearly = safe_float(p.get("yearly_return"))
    recent = monthly * 0.5 + weekly * 0.3 + daily * 0.2
    long_term = yearly * 0.6 + recent * 0.4
    drawdown = abs(safe_float(p.get("max_drawdown")))
    if drawdown < 0.01:
        return long_term > 0
    return (long_term / drawdown) >= 0.15


# ═══════════════════════════════════════════════
# 主导出函数
# ═══════════════════════════════════════════════

def export(db_path, crawl_date, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # ── 1. 读取基础数据 ──────────────────────
    all_players_raw = [dict(r) for r in conn.execute("SELECT * FROM players").fetchall()]
    for p in all_players_raw:
        p["labels"] = parse_json(p.get("labels"))
        p["ranks"] = parse_json(p.get("ranks"))

    # 当日持仓
    positions_raw = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE crawl_date=?", (crawl_date,)
    ).fetchall()]

    # 当日调仓（带选手名）
    trades_raw = [dict(r) for r in conn.execute(
        """SELECT t.*, pl.name as player_name
           FROM trades t LEFT JOIN players pl ON t.zh_id = pl.zh_id
           WHERE t.crawl_date=?""", (crawl_date,)
    ).fetchall()]

    # 所有日期的调仓（用于推测持仓）
    all_trades = [dict(r) for r in conn.execute(
        """SELECT t.*, pl.name as player_name
           FROM trades t LEFT JOIN players pl ON t.zh_id = pl.zh_id
           ORDER BY t.crawl_date DESC, t.trade_date DESC"""
    ).fetchall()]

    # 所有日期的持仓（用于变动追踪）
    all_positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions ORDER BY crawl_date"
    ).fetchall()]

    # 获取所有 crawl_date
    dates_rows = conn.execute(
        "SELECT DISTINCT crawl_date FROM positions WHERE crawl_date != '' ORDER BY crawl_date"
    ).fetchall()
    all_dates = sorted(set(r["crawl_date"] for r in dates_rows))
    if crawl_date not in all_dates:
        all_dates.append(crawl_date)
        all_dates.sort()

    # 选手 name 查找表
    player_names = {p["zh_id"]: (p.get("name") or p["zh_id"]) for p in all_players_raw}

    # ── 2. 构建衍生数据（选手总分仓 + quality 标记等）──
    # 计算每个选手的总仓位
    pos_by_player = defaultdict(float)
    for p in positions_raw:
        pos_by_player[p["zh_id"]] += safe_float(p.get("position_ratio"))

    # 为选手附加衍生字段
    # 按选手聚合持仓代码（用于反向查股票）
    player_stocks = {}
    for p in positions_raw:
        pid = p.get("zh_id", "")
        if pid not in player_stocks:
            player_stocks[pid] = []
        code = p.get("stock_code", "")
        if code and code not in player_stocks[pid]:
            player_stocks[pid].append(code)

    players_flat = []
    quality_ids = set()
    for p in all_players_raw:
        pid = p["zh_id"]
        tp = pos_by_player.get(pid, 0)
        entry = {
            "id": pid,
            "name": p.get("name") or "",
            "followers": safe_int(p.get("followers")),
            "total_return": safe_float(p.get("total_return")),
            "daily_return": safe_float(p.get("daily_return")),
            "weekly_return": safe_float(p.get("weekly_return")),
            "monthly_return": safe_float(p.get("monthly_return")),
            "yearly_return": safe_float(p.get("yearly_return")),
            "net_value": safe_float(p.get("net_value")),
            "max_drawdown": safe_float(p.get("max_drawdown")),
            "win_rate": safe_float(p.get("win_rate")),
            "days": safe_int(p.get("days")),
            "labels": p.get("labels") or [],
            "ranks": p.get("ranks") or [],
            "concept": (p.get("concept") or "")[:100],
            "intro": p.get("intro") or "",
            "total_position": round(tp, 1),
            "quality": is_quality(p),
            "stocks": player_stocks.get(pid, []),
        }
        if entry["quality"]:
            quality_ids.add(pid)
        players_flat.append(entry)

    # Quality lookup dict
    quality_map = {p["id"]: p for p in players_flat if p["quality"]}

    # ── 3. 持仓聚合 → stockStats ────────────
    stock_stats_map = {}
    for p in positions_raw:
        code = p.get("stock_code", "")
        if not code:
            continue
        if code not in stock_stats_map:
            stock_stats_map[code] = {
                "code": code,
                "name": p.get("stock_name", ""),
                "holders": 0,
                "total_position": 0.0,
                "total_profit": 0.0,
                "count": 0,
            }
        s = stock_stats_map[code]
        s["holders"] += 1
        s["total_position"] += safe_float(p.get("position_ratio"))
        s["total_profit"] += safe_float(p.get("profit_ratio"))
        s["count"] += 1
    stock_stats = sorted(
        [{"c": s["code"], "n": s["name"],
          "h": s["holders"], "tp": round(s["total_position"], 1),
          "ap": round(s["total_profit"] / s["count"], 2) if s["count"] else 0}
         for s in stock_stats_map.values()],
        key=lambda s: s["tp"], reverse=True
    )

    # ── 4. 抄作业信号 → copyTradeSignals ────
    stock_signals = {}  # code -> signal entry
    # 持仓信号（高手持有）
    for p in positions_raw:
        pid = p.get("zh_id", "")
        if pid not in quality_ids:
            continue
        code = p.get("stock_code", "")
        if not code:
            continue
        if code not in stock_signals:
            stock_signals[code] = {
                "code": code,
                "name": p.get("stock_name", ""),
                "score": 0.0,
                "totalPosition": 0.0,
                "holderCount": 0,
                "holders": [],
                "buyers": [],
                "sellers": [],
                "buyer_names": [],
                "seller_names": [],
            }
        s = stock_signals[code]
        ratio = safe_float(p.get("position_ratio")) / 100.0
        s["score"] += ratio
        s["totalPosition"] += safe_float(p.get("position_ratio"))
        s["holderCount"] += 1
        pn = quality_map[pid]["name"]
        if pn not in s["holders"]:
            s["holders"].append(pn)

    # 调仓信号（仅当日 trade_date == crawl_date 的实际交易）
    for t in trades_raw:
        pid = t.get("zh_id", "")
        if pid not in quality_ids:
            continue
        if t.get("trade_date", "") != crawl_date:
            continue
        code = t.get("stock_code", "")
        if not code:
            continue
        if code not in stock_signals:
            stock_signals[code] = {
                "code": code,
                "name": t.get("stock_name", ""),
                "score": 0.0,
                "totalPosition": 0.0,
                "holderCount": 0,
                "holders": [],
                "buyers": [],
                "sellers": [],
                "buyer_names": [],
                "seller_names": [],
            }
        s = stock_signals[code]
        weight = safe_int(t.get("trades_count"), 1) * 2
        pn = quality_map[pid]["name"]
        if t.get("direction") == "买入":
            s["score"] += weight
            if pn not in s["buyer_names"]:
                s["buyer_names"].append(pn)
            s["buyers"].append(pid)
        else:
            s["score"] -= weight
            if pn not in s["seller_names"]:
                s["seller_names"].append(pn)
            s["sellers"].append(pid)

    all_signals = list(stock_signals.values())
    for s in all_signals:
        s["score"] = round(s["score"], 1)
        s["totalPosition"] = round(s["totalPosition"], 1)

    buy_signals = sorted(
        [s for s in all_signals if s["buyers"]],
        key=lambda s: len(s["buyers"]), reverse=True
    )
    core_holdings = sorted(
        [s for s in all_signals if s["holderCount"] >= 2],
        key=lambda s: s["holderCount"], reverse=True
    )
    sell_warnings = sorted(
        [s for s in all_signals if s["sellers"] and not s["buyers"]],
        key=lambda s: len(s["sellers"]), reverse=True
    )
    high_quality_signals = sorted(
        [s for s in all_signals if s["score"] >= 3],
        key=lambda s: s["score"], reverse=True
    )

    copy_trade_signals = {
        "bs": [{"c": s["code"], "n": s["name"], "s": s["score"],
                "tp": s["totalPosition"], "h": s["holderCount"],
                "b": s["buyer_names"], "sl": s["seller_names"]}
               for s in buy_signals],
        "ch": [{"c": s["code"], "n": s["name"], "s": s["score"],
                "tp": s["totalPosition"], "h": s["holderCount"],
                "hd": s["holders"]}
               for s in core_holdings],
        "sw": [{"c": s["code"], "n": s["name"], "s": s["score"],
                "tp": s["totalPosition"], "h": s["holderCount"],
                "sl": s["seller_names"]}
               for s in sell_warnings],
        "hq": []  # highQuality 数据前端只用 .length, 不需要具体内容
    }

    # ── 8. 今日卖出预警 + 疑似清仓 ──────────
    trade_alerts_map = {}
    for t in trades_raw:
        if t.get("trade_date", "") != crawl_date or t.get("direction") != "卖出":
            continue
        code = t.get("stock_code", "")
        if not code:
            continue
        if code not in trade_alerts_map:
            trade_alerts_map[code] = {"stock_name": t.get("stock_name", ""),
                                       "stock_code": code, "players": []}
        pn = t.get("player_name") or t.get("zh_id", "")
        pid = t.get("zh_id", "")
        if not any(p[1] == pid for p in trade_alerts_map[code]["players"]):
            trade_alerts_map[code]["players"].append([pn, pid])    # [name, zh_id]
    trade_alerts = sorted(trade_alerts_map.values(),
                          key=lambda a: len(a["players"]), reverse=True)

    # 疑似清仓
    # 先建立买入历史索引
    buy_history = {}
    for t in all_trades:
        if t.get("direction") != "买入":
            continue
        key = f"{t['zh_id']}_{t['stock_code']}"
        if key not in buy_history or t.get("trade_date", "") > buy_history[key].get("trade_date", ""):
            buy_history[key] = t

    suspected_clears = []
    for t in trades_raw:
        if t.get("trade_date", "") != crawl_date or t.get("direction") != "卖出":
            continue
        key = f"{t['zh_id']}_{t['stock_code']}"
        buy = buy_history.get(key)
        if not buy:
            continue
        # 7天内有买入
        try:
            buy_date = datetime.strptime(buy.get("trade_date", ""), "%Y-%m-%d")
            sell_date = datetime.strptime(t.get("trade_date", ""), "%Y-%m-%d")
            diff_days = (sell_date - buy_date).days
        except ValueError:
            continue
        if diff_days < 0 or diff_days > 7:
            continue
        buy_level = (buy.get("position_ratio") or "").strip()
        sell_level = (t.get("position_ratio") or "").strip()
        if not buy_level or not sell_level or buy_level != sell_level:
            continue
        suspected_clears.append({
            "player_name": t.get("player_name") or t.get("zh_id", ""),
            "zh_id": t.get("zh_id", ""),
            "stock_name": t.get("stock_name", ""),
            "stock_code": t.get("stock_code", ""),
            "level": buy_level,
            "buyDate": buy.get("trade_date", ""),
            "sellDate": t.get("trade_date", ""),
        })

    # ── 10. 持仓变动 → changes_data（供 changes_summary.json）──
    # 需要最近两个日期的数据
    changes_data = None
    if len(all_dates) >= 2:
        today = all_dates[-1]
        yesterday = all_dates[-2]

        # 前一日持仓
        y_positions = [dict(r) for r in conn.execute(
            "SELECT * FROM positions WHERE crawl_date=?", (yesterday,)
        ).fetchall()]

        y_map = defaultdict(dict)  # zh_id -> {stock_code: position}
        for p in y_positions:
            y_map[p["zh_id"]][p["stock_code"]] = p

        t_map = defaultdict(dict)  # zh_id -> {stock_code: position}
        for p in positions_raw:
            t_map[p["zh_id"]][p["stock_code"]] = p

        yesterday_players = {p["zh_id"]: p for p in [dict(r) for r in conn.execute(
            "SELECT zh_id, name FROM players"
        ).fetchall()]}

        changes = []
        all_pids = set(list(y_map.keys()) + list(t_map.keys()))
        for pid in all_pids:
            today_stocks = t_map.get(pid, {})
            yesterday_stocks = y_map.get(pid, {})
            all_codes = set(list(today_stocks.keys()) + list(yesterday_stocks.keys()))
            for code in all_codes:
                t = today_stocks.get(code)
                y = yesterday_stocks.get(code)
                today_ratio = safe_float(t.get("position_ratio")) if t else 0
                yesterday_ratio = safe_float(y.get("position_ratio")) if y else 0
                delta = today_ratio - yesterday_ratio
                if abs(delta) < 1:
                    continue
                if not y and t:
                    change_type, emoji = "新进", "🆕"
                elif y and not t:
                    change_type, emoji = "清仓", "🚫"
                elif delta > 0:
                    change_type, emoji = "加仓", "📈"
                else:
                    change_type, emoji = "减仓", "📉"
                ref = t or y
                # [zh_id, player_name, stock_code, stock_name, type, emoji, delta, yesterdayRatio, todayRatio]
                changes.append([
                    pid,
                    (player_names.get(pid) or pid),
                    code,
                    (ref.get("stock_name") or "") if ref else "",
                    change_type,
                    emoji,
                    round(delta, 1),
                    round(yesterday_ratio, 1),
                    round(today_ratio, 1),
                ])
        changes.sort(key=lambda c: abs(c[6]), reverse=True)  # sort by delta
        changes_data = {
            "hasHistory": True,
            "yesterday": yesterday,
            "today": today,
            "changes": changes,
            "added": [c for c in changes if c[4] == "新进"],
            "cleared": [c for c in changes if c[4] == "清仓"],
        }

    # ── 11. 今日有调仓的选手 ID ──────────────
    traded_player_ids = list(set(
        t["zh_id"] for t in trades_raw
        if t.get("trade_date", "") == crawl_date and t.get("zh_id")
    ))

    # ── 12. 构建 summary 分片（按页面切片，前端按需加载）──
    # summary.json 仍全量输出作为调试参照 + verify 基准
    summary_slices = {
        "core": {
            "date": crawl_date,
            "crawl_time": "",  # 下面填入
            "qualityPlayerCount": len(quality_ids),
            "tradedPlayerIds": traded_player_ids,
            "fullRankCount": sum(1 for p in players_flat if len(p.get("ranks") or []) >= 5),
        },
        "copy": {
            "copyTradeSignals": copy_trade_signals,
            "tradeAlerts": trade_alerts,
            "suspectedClears": suspected_clears,
        },
        "stocks": {"stockStats": stock_stats},
    }

    # name_map 只保留当日实际被引用的名字（copy 信号 + alerts 里出现的名字）
    referenced_names = set()
    for sig in all_signals:
        referenced_names.update(sig.get("holders") or [])
        referenced_names.update(sig.get("buyer_names") or [])
        referenced_names.update(sig.get("seller_names") or [])
    for alert in trade_alerts:
        for name, _pid in alert.get("players", []):
            referenced_names.add(name)
    for sc in suspected_clears:
        referenced_names.add(sc["player_name"])
    name_map = {p["name"]: p["id"] for p in players_flat
                if p["name"] and p["name"] in referenced_names}

    # 全量参照文件（字段=各分片并集 + 精简后的 name_map）
    summary = {**summary_slices["core"], **summary_slices["copy"],
               **summary_slices["stocks"], "playerNameMap": name_map}

    # ── 13. 构建选手详情文件 ──────────────────
    # positions/trades by player
    pos_by_pid = defaultdict(list)
    for p in positions_raw:
        pos_by_pid[p["zh_id"]].append({
            "sn": p.get("stock_name", ""),
            "sc": p.get("stock_code", ""),
            "cp": safe_float(p.get("cost_price")),
            "np": safe_float(p.get("current_price")),
            "pr": safe_float(p.get("profit_ratio")),
            "rr": safe_float(p.get("position_ratio")),
        })

    # 导出全部历史调仓（all_trades 含所有 crawl_date），按唯一交易去重
    trades_by_pid = defaultdict(list)
    seen = set()
    for t in all_trades:
        key = (t["zh_id"], t.get("stock_code",""), t.get("trade_date",""), t.get("direction",""))
        if key in seen:
            continue
        seen.add(key)
        trades_by_pid[t["zh_id"]].append({
            "td": t.get("trade_date", ""),
            "dr": t.get("direction", ""),
            "sn": t.get("stock_name", ""),
            "sc": t.get("stock_code", ""),
            "tc": safe_int(t.get("trades_count"), 1),
            "rr": t.get("position_ratio", ""),
            "pr": safe_float(t.get("price")),
            "_id": t["id"],  # 仅日内排序(db id=API原始顺序, 跨run会变, 勿用作增量键)
            # 稳定增量键: 内容哈希, 重采/换id后不变, 供 notify_daily 判"新增调仓"。
            # (db id 每次重采都重新分配, 曾致钉钉每班 run 全部误判新增→重复推送+全标🆕)
            "_k": hashlib.md5("|".join([
                t.get("trade_date", ""), t.get("stock_code", ""), t.get("direction", ""),
                str(safe_float(t.get("price"))), t.get("position_ratio", "") or "",
                str(safe_int(t.get("trades_count"), 1)),
            ]).encode()).hexdigest()[:12],
        })

    # 调仓按日期倒序，同日内按 API 原始顺序（先买后卖，_id 升序）
    for pid in trades_by_pid:
        trades_by_pid[pid].sort(key=lambda x: x.get("_id", 0))
        trades_by_pid[pid].sort(key=lambda x: x.get("td", ""), reverse=True)

    # 推测持仓
    def compute_inferred_positions(zh_id, confirmed_codes):
        # 缩写键名: sn=stock_name, sc=stock_code, b=buys, s=sells, l=level, d=date
        stock_state = defaultdict(lambda: {"sn": "", "sc": "", "b": [], "s": []})
        for t in all_trades:
            if t.get("zh_id") != zh_id:
                continue
            code = t.get("stock_code", "")
            if not code or code in confirmed_codes:
                continue
            ss = stock_state[code]
            ss["sn"] = t.get("stock_name", "")
            ss["sc"] = code
            lv = t.get("position_ratio") or "?"
            if t.get("direction") == "买入":
                ss["b"].append({"l": lv, "d": t.get("trade_date", "")})
            else:
                ss["s"].append({"l": lv, "d": t.get("trade_date", "")})

        result = []
        for code, ss in stock_state.items():
            if not ss["b"]:
                continue
            b_sorted = sorted(ss["b"], key=lambda x: x["d"], reverse=True)
            s_sorted = sorted(ss["s"], key=lambda x: x["d"], reverse=True)
            latest_buy = b_sorted[0]
            has_sells = len(s_sorted) > 0

            if not has_sells:
                st, cf = "持续买入", "mid" if len(ss["b"]) >= 2 else "low"
            elif latest_buy["d"] > s_sorted[0]["d"]:
                st, cf = "近期加仓", "mid"
            elif s_sorted[0]["d"] > latest_buy["d"] and latest_buy["l"] == s_sorted[0]["l"]:
                continue  # skip — likely cleared
            else:
                st, cf = "可能减持", "low"

            result.append({
                "sn": ss["sn"], "cd": code,
                "le": latest_buy["l"],
                "st": st, "cf": cf,
                "bc": len(ss["b"]), "sc": len(ss["s"]),
            })
        return sorted(result, key=lambda r: 0 if r["cf"] == "mid" else 1)

    # ── 14. 写文件 ───────────────────────────

    # 读取 crawl_time
    crawl_start_file = ROOT / "data" / "crawl_start.txt"
    if crawl_start_file.exists():
        crawl_time = crawl_start_file.read_text().strip()
    else:
        crawl_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    summary["crawl_time"] = crawl_time
    summary_slices["core"]["crawl_time"] = crawl_time

    # 15. 新格式输出
    latest_dir = out_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    # summary.json
    # players_index.json (独立文件，前端并行加载)
    # 体积优化：数字保留 2 位小数（net_value 保留 3 位）、labels 只存数量（前端仅用 .length）
    # 导出范围收窄：players 表只增不减(累计 23192 人, 多为早已跌榜的冻结数据), 全量导出曾致
    # players/ 目录 92MB 随 git 无限累积。目录/详情只保留"当前有意义"的选手:
    #   优质(quality) ∪ 当日有持仓/调仓(活跃) ∪ name_map 被引用 ∪ 关注名单 —— 跌出该集合的自然淘汰。
    # 关注名单必须强制包含: 清仓空仓日不在活跃集合, 缺 JSON 会让钉钉卡片显示"数据缺失"。
    sys.path.insert(0, str(ROOT))
    from main import WATCHED_PLAYERS  # noqa: E402
    watched_ids = {wid for wid, _ in WATCHED_PLAYERS}
    active_ids = ({p.get("zh_id") for p in positions_raw}
                  | {t.get("zh_id") for t in trades_raw}
                  | set(traded_player_ids)
                  | set(name_map.values())
                  | watched_ids)
    export_ids = quality_ids | {i for i in active_ids if i}
    export_players = [p for p in players_flat if p["id"] in export_ids]
    players_list = [
        [p["id"], p["name"], p["followers"],
         round(p["total_return"], 2), round(p["daily_return"], 2),
         round(p["weekly_return"], 2), round(p["monthly_return"], 2),
         round(p["yearly_return"], 2), round(p["net_value"], 3),
         round(p["max_drawdown"], 2), round(p["win_rate"], 2),
         p["days"], len(p["labels"] or []), p["ranks"],
         p["total_position"], p["quality"],
         p["stocks"]]
        for p in export_players
    ]
    with open(latest_dir / "players_index.json", "w", encoding="utf-8") as f:
        json.dump(players_list, f, ensure_ascii=False, separators=(",", ":"))

    with open(latest_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, separators=(",", ":"))

    # 分片文件（前端按需加载）
    with open(latest_dir / "core.json", "w", encoding="utf-8") as f:
        json.dump(summary_slices["core"], f, ensure_ascii=False, separators=(",", ":"))
    for slice_name in ("copy", "stocks"):
        with open(latest_dir / f"{slice_name}.json", "w", encoding="utf-8") as f:
            json.dump(summary_slices[slice_name], f, ensure_ascii=False, separators=(",", ":"))

    # name_map.json（只含当日被引用名字）
    with open(latest_dir / "name_map.json", "w", encoding="utf-8") as f:
        json.dump(name_map, f, ensure_ascii=False, separators=(",", ":"))

    # changes_summary.json（/copy 摘要栏用，不含明细）
    if changes_data:
        changes_summary = {
            "hasHistory": True,
            "yesterday": changes_data["yesterday"],
            "today": changes_data["today"],
            "addedCount": len(changes_data["added"]),
            "clearedCount": len(changes_data["cleared"]),
            "changeCount": len(changes_data["changes"]),
        }
    else:
        changes_summary = {"hasHistory": False, "yesterday": "", "today": "",
                           "addedCount": 0, "clearedCount": 0, "changeCount": 0}
    with open(latest_dir / "changes_summary.json", "w", encoding="utf-8") as f:
        json.dump(changes_summary, f, ensure_ascii=False, separators=(",", ":"))

    # my_positions.json（个人纪律卡: 持仓价位表 + rtV2 自动核对 + 板块涨停统计 + 操作点评）
    build_my_positions(latest_dir, crawl_date)

    # lianban_bid.json（出击列表特殊标记: 昨日连板 × 今晨竞价实际换手 Top5）
    build_lianban_bid(latest_dir, crawl_date)

    # players/{zh_id}.json
    players_out_dir = latest_dir / "players"
    players_out_dir.mkdir(parents=True, exist_ok=True)
    for p in export_players:
        pid = p["id"]
        # 仅保留 id+name 用于识别，持仓/调仓/推测使用缩写键名
        detail = {
            "id": pid,
            "name": p["name"],
            "p": pos_by_pid.get(pid, []),
            "t": trades_by_pid.get(pid, []),
            "i": compute_inferred_positions(pid, set(
                pp.get("cd", pp.get("sc", "")) for pp in pos_by_pid.get(pid, [])
            )),
        }
        with open(players_out_dir / f"{pid}.json", "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, separators=(",", ":"))

    # 清理不在导出集合的旧 JSON: 跌出"优质∪当日活跃∪被引用"集合的选手文件删除,
    # 避免目录只增不删(曾累积 23192 个/92MB 随 git 提交膨胀)。
    # 前端按 players_index/name_map 引用, 集合外文件无消费方, 可安全删除。
    exported_ids = export_ids
    removed_players = 0
    for f in players_out_dir.glob("*.json"):
        if f.stem not in exported_ids:
            f.unlink()
            removed_players += 1

    # 15c. index.json（不变）
    index_path = out_dir / "index.json"
    existing_dates = []
    if index_path.exists():
        existing_dates = json.loads(index_path.read_text(encoding="utf-8")).get("dates", [])
    if crawl_date not in existing_dates:
        existing_dates.append(crawl_date)
        existing_dates.sort()
    index_path.write_text(
        json.dumps({"dates": existing_dates, "crawl_time": crawl_time},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8"
    )

    # ── 16. 报告 ─────────────────────────────
    conn.close()

    n_players = len(list(players_out_dir.glob("*.json")))
    summary_size = (latest_dir / "summary.json").stat().st_size / 1024

    print(f"✅ 导出完成 ({crawl_date})")
    print(f"   summary.json → {summary_size:.0f}KB")
    print(f"   players/ → {n_players} 个选手详情文件 (清理旧文件 {removed_players} 个)")
    print(f"   选手: {len(all_players_raw)} | 持仓: {len(positions_raw)} | 调仓: {len(trades_raw)} | 高手: {len(quality_ids)}")


def build_my_positions(latest_dir: Path, crawl_date: str):
    """个人纪律卡数据源 data/latest/my_positions.json

    配置: data/my_positions.json(手编, 价位表/板块归属/weekly_focus)
    自动: rtV2 近6页调仓(轧差估净持仓+今日卖出检测) / GetPlateInfo_w38(HisLimitResumption)
          近2池日板块涨停统计 / 腾讯行情补价 → 触价(rebound/break)判定
          / 最新交易日操作逐笔点评(ops_review, 按已立之法执法)
    任一外部依赖失败只降级本文件, 不影响其余导出。
    """
    cfg_path = ROOT / "data" / "my_positions.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️ my_positions.json 解析失败: {e}")
        return
    positions = cfg.get("positions") or []
    if not positions:
        return
    meta = cfg.get("meta") or {}

    # ── 1) rtV2 调仓(近6页=120笔, 轧差为估算值) ──
    net_qty, buy_amt, buy_qty = {}, defaultdict(float), defaultdict(int)
    today_buy, today_sell = defaultdict(int), defaultdict(int)
    all_rows = []
    trades_asof = ""
    zh, uid = str(meta.get("zhId", "")), str(meta.get("userId", ""))
    if zh and uid:
        try:
            from src.spiders.api_detail import _call_api  # noqa: E402
            for page in range(1, 7):
                d = _call_api("CombinationRelocatePositionHandler", {
                    "userId": uid, "combinationId": zh, "pageNum": page,
                    "pageSize": 20, "beginDate": "", "endDate": ""})
                rows = (d or {}).get("pages") or []
                all_rows.extend(rows)
                for r in rows:
                    code = str(r.get("stkCode", ""))
                    q = safe_int(r.get("relocateQty"))
                    px = safe_float(r.get("relocatePrice"))
                    is_buy = r.get("bsMark") == "B"
                    trades_asof = max(trades_asof, str(r.get("bizDate", "")))
                    net_qty[code] = net_qty.get(code, 0) + (q if is_buy else -q)
                    if is_buy:
                        buy_amt[code] += q * px
                        buy_qty[code] += q
                        if str(r.get("bizDate")) == trades_asof:
                            today_buy[code] += q
                    elif str(r.get("bizDate")) == trades_asof:
                        today_sell[code] += q
                if len(rows) < 20:
                    break
        except Exception as e:
            print(f"  ⚠️ rtV2 持仓核对失败(跳过自动核对): {e}")

    # ── 2) 板块涨停统计(近2个池日) ──
    board_stats, board_asof = {}, ""
    try:
        from src.spiders.auction_spider import KPLSpider  # noqa: E402
        from src.config import KPL_HOST_HIS  # noqa: E402
        from src.utils.market import market_prefix as _mpfx  # noqa: E402
        sp = KPLSpider()
        days = [str(crawl_date)]
        try:
            with sqlite3.connect(f"file:{ROOT / 'data' / 'auction.db'}?mode=ro", uri=True) as c:
                for (d,) in c.execute(
                        "SELECT DISTINCT date FROM limit_pool ORDER BY date DESC LIMIT 3"):
                    if d not in days:
                        days.append(d)
        except Exception:
            pass
        stats_by_day = {}
        for d in days[:2]:
            j = sp._get({"a": "GetPlateInfo_w38", "c": "HisLimitResumption", "st": 1000,
                         "Index": 0, "Date": d}, KPL_HOST_HIS)
            cur = {}
            for g in (j or {}).get("list") or []:
                name = g.get("ZSName") or ""
                stocks = g.get("StockList") or []
                lv = 0
                for row in stocks:
                    try:
                        lv = max(lv, int(row[9] or 1))
                    except (ValueError, TypeError):
                        lv = max(lv, 1)
                if name and stocks:
                    cur[name] = {"count": len(stocks), "max_lv": lv}
            if cur:
                stats_by_day[d] = cur
        ds = sorted(stats_by_day, reverse=True)
        if ds:
            today_s = stats_by_day[ds[0]]
            prev_s = stats_by_day[ds[1]] if len(ds) > 1 else {}
            for name, s in today_s.items():
                board_stats[name] = {"zt": s["count"], "zt_prev": prev_s.get(name, {}).get("count", 0),
                                     "max_lv": s["max_lv"]}
            for name, s in prev_s.items():
                board_stats.setdefault(name, {"zt": 0, "zt_prev": s["count"], "max_lv": 0})
            board_asof = ds[0]
    except Exception as e:
        print(f"  ⚠️ 板块统计失败(跳过): {e}")

    # ── 3) 行情(腾讯 qt.gtimg 批量, 与 notify_daily 同源) ──
    quotes = {}
    codes = [str(p.get("code", "")) for p in positions if p.get("code")]
    try:
        import re as _re
        import urllib.request as _ur
        for i in range(0, len(codes), 30):
            batch = codes[i:i + 30]
            symbols = ",".join(f"{_mpfx(c)}{c}" for c in batch)
            with _ur.urlopen(f"http://qt.gtimg.cn/q={symbols}", timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
            for line in text.splitlines():
                m = _re.match(r'v_s[hz](\d+)="(.*)"', line.strip())
                if not m:
                    continue
                fields = m.group(2).split("~")
                if len(fields) <= 32:
                    continue
                try:
                    quotes[m.group(1)] = {"price": float(fields[3]), "pct": float(fields[32])}
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        print(f"  ⚠️ 行情获取失败(跳过实时价): {e}")

    # ── 3b) 今日作战指引(前置导航: 定性/许可/挂单清单/主线观察) ──
    battle_plan = {}
    STAGE_RULES_PY = {
        "退潮": ("≤2成", "只卖不买：执行清仓价位单，禁止一切新开仓", "今日指令：只卖不买。挂好持仓价位单，然后看盘不动手。"),
        "冰点": ("≤2成", "等试错许可(A+B)后才允许 1 成仓试主线首板", "今日指令：等待冰点确认。许可灯不亮，一分钱不出手。"),
        "分歧": ("≤2成", "断崖分歧只卖不买；高位分歧可抱团龙头", "今日指令：只卖不买。龙头再诱人也只是反弹，不是反转。"),
        "高潮": ("新仓≤2成", "持仓兑现为主：只做最强主流，杂毛禁碰", "今日指令：兑现为主。卖在放量滞涨，新仓只给最强主流。"),
        "发酵": ("6-8成", "五板定龙头：上龙头/同梯队强者，弱转强打板龙头、低吸中后排", "今日指令：主线内做多。上龙头或同梯队强者，杂毛一分钱不配。"),
        "启动": ("6-8成", "打低位首板/1进2 为主；情绪低点做龙头", "今日指令：打首板/1进2。低位敢打板，高位不追。"),
    }
    try:
        from src.analysis.emotion_cycle import compute_cycle as _cc  # noqa: E402
        cy = _cc(None)
        stage = cy.get("stage", "")
        mtr = cy.get("metrics", {})
        cap, act, order_line = STAGE_RULES_PY.get(stage, ("—", "周期计算中", ""))
        # 许可灯: A跌停萎缩 B昨日涨停溢价(腾讯批量) C高度板梯队 D情绪≥50
        today_df = prev_df = strong = None
        try:
            from src.spiders.auction_spider import KPLSpider as _KS2  # noqa: E402
            from src.config import KPL_HOST_HIS as _HH  # noqa: E402
            mj = _KS2()._get({"a": "ChangeStatistics", "st": 5, "c": "HisHomeDingPan",
                              "Index": 0}, _HH)
            mood_rows = (mj or {}).get("info") or []
            if mood_rows:
                today_df = safe_int(mood_rows[0].get("df_num"))
                prev_df = safe_int(mood_rows[1].get("df_num"))
                strong = safe_float(mood_rows[0].get("strong"))
        except Exception:
            pass
        lic_a = today_df is not None and prev_df is not None and today_df < prev_df
        lic_c = bool(mtr and mtr.get("height", 0) >= 3 and mtr.get("height", 0) >= (mtr.get("heightPrev") or 0))
        lic_d = strong is not None and strong >= 50
        lic_b = None
        try:
            with sqlite3.connect(f"file:{ROOT / 'data' / 'auction.db'}?mode=ro", uri=True) as c:
                row = c.execute("SELECT MAX(date) FROM limit_pool WHERE date < ?",
                                (str(crawl_date),)).fetchone()
            prev_pool_day = row[0] if row else None
            if prev_pool_day:
                pcodes = [r[0] for r in sqlite3.connect(
                    f"file:{ROOT / 'data' / 'auction.db'}?mode=ro", uri=True).execute(
                    "SELECT code FROM limit_pool WHERE date=?", (prev_pool_day,))]
                import re as _re2
                import urllib.request as _ur3
                pcts = []
                for i in range(0, len(pcodes), 30):
                    b = pcodes[i:i + 30]
                    symbols = ",".join(f"{'sh' if c.startswith(('6', '9')) else 'sz'}{c}" for c in b)
                    with _ur3.urlopen(f"http://qt.gtimg.cn/q={symbols}", timeout=8) as resp:
                        text = resp.read().decode("gbk", errors="ignore")
                    for line in text.splitlines():
                        m = _re2.match(r'v_s[hz](\d+)="(.*)"', line.strip())
                        if not m:
                            continue
                        f = m.group(2).split("~")
                        if len(f) > 32:
                            try:
                                pcts.append(float(f[32]))
                            except (ValueError, TypeError):
                                pass
                if pcts:
                    lic_b = sum(pcts) / len(pcts) > 0
        except Exception:
            pass
        # 挂单清单
        orders = []
        for p in positions:
            if p.get("status") == "done":
                continue
            rb, brk = p.get("exit_rebound"), p.get("exit_break")
            st = p.get("status", "holding")
            if st == "keep":
                action = f"持有：破 {brk} 减半，连板则持有上移止损"
            elif rb and brk:
                action = f"竞价挂反抽卖单 {rb[0]}-{rb[1]}；跌破 {brk} 无条件走"
            elif rb:
                action = f"挂反抽卖单 {rb[0]}-{rb[1]}"
            elif brk:
                action = f"跌破 {brk} 无条件走"
            else:
                action = "无价位单（观察仓）"
            orders.append({"code": p.get("code"), "name": p.get("name", ""), "action": action})
        # 主线观察(今日板块 ≥2只, 带板块内最高标)
        watch = []
        for name, s in sorted(board_stats.items(), key=lambda kv: -kv[1]["zt"])[:3]:
            if name in ("其他", "ST板块") or s["zt"] < 2:
                continue
            watch.append({"board": name, "zt": s["zt"], "max_lv": s["max_lv"]})
        battle_plan = {
            "stage": stage, "cap": cap, "act": act, "order_line": order_line,
            "licenses": {
                "a": {"ok": lic_a, "txt": f"跌停 {today_df}(昨 {prev_df})" if today_df is not None else "—"},
                "b": {"ok": bool(lic_b), "txt": ("昨日涨停溢价转正" if lic_b else "昨日涨停溢价未转正") if lic_b is not None else "—"},
                "c": {"ok": lic_c, "txt": f"高度 {mtr.get('height')}B(昨 {mtr.get('heightPrev')})" if mtr else "—"},
                "d": {"ok": lic_d, "txt": f"情绪 {strong}"},
                "trial": lic_a and bool(lic_b), "recover": lic_c and lic_d,
            },
            "orders": orders,
            "watchlist": watch,
        }
    except Exception as e:
        print(f"  ⚠️ 作战指引生成失败(跳过): {e}")
        battle_plan = {}

    # ── 3c) 人气榜排名(散户注意力=养家的"散户情绪"量化, hot_rank.db am/pm 快照) ──
    hot = {}
    try:
        with sqlite3.connect(f"file:{ROOT / 'data' / 'hot_rank.db'}?mode=ro", uri=True) as c:
            dates = [r[0] for r in c.execute(
                "SELECT DISTINCT date FROM hot_rank ORDER BY date DESC LIMIT 2")]
            for i, d in enumerate(dates):
                snap = ("pm" if c.execute(
                    "SELECT COUNT(*) FROM hot_rank WHERE date=? AND snap='pm'", (d,)).fetchone()[0]
                    else "am")
                for code, rank in c.execute(
                        "SELECT code, MIN(rank) FROM hot_rank WHERE date=? AND snap=? GROUP BY code",
                        (d, snap)):
                    if i == 0:
                        hot.setdefault(code, {})["rank"] = rank
                    else:
                        hot.setdefault(code, {}).setdefault("prev", rank)
    except Exception as e:
        print(f"  ⚠️ 人气榜读取失败(跳过): {e}")

    # ── 4) 操作点评(最新交易日逐笔, 按已立之法执法) ──
    ops_review = {"date": trades_asof, "stage": "", "items": [], "bad": 0, "warn": 0, "ok": 0}
    try:
        from src.analysis.emotion_cycle import compute_cycle  # noqa: E402
        ops_review["stage"] = compute_cycle(None).get("stage", "")
    except Exception:
        pass
    try:
        import urllib.request as _ur2

        def _day_bar(code):
            """腾讯日K近12根: 返回 (当日bar{open,high,low,close}, 昨收) 或 (None, None)"""
            pfx = _mpfx(code)
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pfx}{code},day,,,12,qfq"
            with _ur2.urlopen(url, timeout=8) as resp:
                j = json.loads(resp.read().decode("utf-8", "ignore"))
            bars = (j.get("data", {}).get(pfx + code, {}) or {}).get("qfqday") \
                or (j.get("data", {}).get(pfx + code, {}) or {}).get("day") or []
            for i, b in enumerate(bars):
                if str(b[0]).replace("-", "") == trades_asof:
                    prev = float(bars[i - 1][2]) if i > 0 else None
                    return {"open": float(b[1]), "close": float(b[2]),
                            "high": float(b[3]), "low": float(b[4])}, prev
            return None, None

        _RANK = {"ok": 0, "warn": 1, "bad": 2}
        exit_cfg = {p.get("code"): p for p in positions if p.get("code")}
        day_rows = sorted([r for r in all_rows if str(r.get("bizDate")) == trades_asof
                           and r.get("stkCode")], key=lambda r: r.get("relocateTime", ""))
        bar_cache, last_sell = {}, {}
        day_buy_qty, day_buy_amt = defaultdict(int), defaultdict(float)
        bans = ("退潮", "冰点", "分歧")
        for r in day_rows:
            code = str(r["stkCode"])
            bs, qty = r.get("bsMark"), safe_int(r.get("relocateQty"))
            price = safe_float(r.get("relocatePrice"))
            t = str(r.get("relocateTime", ""))[11:16]
            name = str(r.get("stkName", ""))
            checks = []
            if code not in bar_cache:
                bar_cache[code] = _day_bar(code)
            bars, prev_close = bar_cache[code]

            if bs == "B":
                if ops_review["stage"] in bans:
                    checks.append(("bad", "周期禁买", f"{ops_review['stage']}期只卖不买，仍开新仓"))
                if prev_close and price < prev_close:
                    checks.append(("bad", "当日收跌禁买",
                                   f"买价低于昨收 {(price / prev_close - 1) * 100:.1f}%（接刀）"))
                if bars and bars["high"] > bars["low"] \
                        and price >= bars["low"] + (bars["high"] - bars["low"]) * 0.7:
                    checks.append(("warn", "追高",
                                   f"买在当日振幅 {(price - bars['low']) / (bars['high'] - bars['low']) * 100:.0f}% 分位"))
                avg0 = buy_amt[code] / buy_qty[code] if buy_qty.get(code) else None
                if net_qty.get(code, 0) - qty > 0 and avg0 and price < avg0 * 0.995:
                    checks.append(("bad", "错了不许摊", f"低于持仓均价 {avg0:.3f} 补仓"))
                ls = last_sell.get(code)
                if ls:
                    mins = (int(t[:2]) * 60 + int(t[3:])) - (int(ls["t"][:2]) * 60 + int(ls["t"][3:]))
                    if 0 <= mins <= 30:
                        extra = f"，且高于卖出价 {(price / ls['price'] - 1) * 100:.1f}%" if price > ls["price"] else ""
                        checks.append(("bad", "黑名单回买", f"卖出 {ls['t']} 后 {mins} 分钟回买{extra}"))
                day_buy_qty[code] += qty
                day_buy_amt[code] += qty * price
                if not checks:
                    checks.append(("ok", "买入未踩线", "非收跌股·非追高分位"))
            else:
                avg_buy = day_buy_amt[code] / day_buy_qty[code] if day_buy_qty.get(code) else None
                cfgp = exit_cfg.get(code) or {}
                rb, brk = cfgp.get("exit_rebound"), cfgp.get("exit_break")
                if rb and rb[0] <= price <= rb[1]:
                    checks.append(("ok", "价位单执行", f"反抽区 {rb[0]}-{rb[1]} 离场"))
                elif brk and price <= brk:
                    checks.append(("ok", "破位执行", f"≤{brk} 无条件走"))
                elif avg_buy and price <= avg_buy * 0.95:
                    checks.append(("warn", "止损过晚", f"较当日买价已 {(price / avg_buy - 1) * 100:.1f}%"))
                elif avg_buy and price < avg_buy:
                    checks.append(("ok", "当日止损", f"较当日买价 {(price / avg_buy - 1) * 100:.1f}% 离场"))
                elif avg_buy and price >= avg_buy * 1.03:
                    checks.append(("ok", "止盈离场", f"较当日买价 +{(price / avg_buy - 1) * 100:.1f}%"))
                else:
                    checks.append(("ok", "离场", ""))
                last_sell[code] = {"t": t, "price": price}

            verdict = "ok"
            for v, _, _ in checks:
                if _RANK[v] > _RANK[verdict]:
                    verdict = v
            ops_review[verdict] += 1
            ops_review["items"].append({
                "time": t, "code": code, "name": name, "bs": bs, "qty": qty, "price": price,
                "verdict": verdict, "rules": [c[1] for c in checks],
                "msg": "；".join(f"{c[1]}{('：' + c[2]) if c[2] else ''}" for c in checks),
            })
        ops_review["items"].reverse()  # 最新在前
    except Exception as e:
        print(f"  ⚠️ 操作点评失败(跳过): {e}")
        ops_review = {"date": trades_asof, "stage": ops_review.get("stage", ""),
                      "items": [], "bad": 0, "warn": 0, "ok": 0}

    # ── 5) 组装 ──
    def verdict(n):
        return "主线" if n >= 5 else ("次主线" if n >= 3 else ("观察" if n == 2 else "死线/非主线"))

    def _find_board(kw):
        """关键词匹配板块名(板块名每日漂移, 如『算力(液冷)』); 其他/ST 为全能桶, 不参与判定"""
        for name, s in board_stats.items():
            if name in ("其他", "ST板块"):
                continue
            if kw in name or name in kw:
                return name, s
        return kw, None

    out_positions, touch_count = [], 0
    for p in positions:
        code = str(p.get("code", ""))
        q = quotes.get(code) or {}
        price, pct = q.get("price"), q.get("pct")
        cost = safe_float(p.get("cost"))
        profit = round((price / cost - 1) * 100, 2) if price and cost > 0 else None
        rb, brk = p.get("exit_rebound"), p.get("exit_break")
        touch, dist_rb, dist_brk = None, None, None
        if price:
            if rb and rb[0] <= price <= rb[1]:
                touch = "rebound"
            elif brk and price <= brk:
                touch = "break"
            if rb:
                dist_rb = round((price - rb[0]) / price * 100, 2)
            if brk:
                dist_brk = round((price - brk) / price * 100, 2)
        if touch:
            touch_count += 1
        status = p.get("status", "holding")
        auto = None
        if net_qty:
            nq = net_qty.get(code)
            if nq is not None and nq <= 0 and status not in ("keep", "done"):
                auto = "exited?"
            elif today_sell.get(code, 0) > today_buy.get(code, 0):
                auto = "selling"
        boards_out, primary = [], None
        for b in p.get("boards", []):
            real_name, s = _find_board(b)
            if s:
                item = {"name": real_name, "zt": s["zt"], "zt_prev": s["zt_prev"],
                        "max_lv": s["max_lv"], "verdict": verdict(s["zt"]), "matched": True}
            else:
                item = {"name": b, "zt": 0, "zt_prev": 0, "max_lv": 0,
                        "verdict": "死线/非主线", "matched": False}
            boards_out.append(item)
            if primary is None or item["zt"] > primary["zt"]:
                primary = item
        avg_cost = round(buy_amt[code] / net_qty[code], 3) if net_qty.get(code, 0) > 0 and buy_amt.get(code) else None
        out_positions.append({
            "code": code, "name": p.get("name", ""), "weight": p.get("weight", ""),
            "cost": cost, "cfg_avg_cost": avg_cost,
            "price": price, "pct": pct, "profit_pct": profit,
            "exit_rebound": rb, "exit_break": brk,
            "dist_rebound_pct": dist_rb, "dist_break_pct": dist_brk,
            "touch": touch, "status": status, "auto_status": auto,
            "net_qty_est": net_qty.get(code) if net_qty else None,
            "hot": {"rank": hot.get(code, {}).get("rank"),
                    "prev": hot.get(code, {}).get("prev"),
                    "delta": (hot[code]["prev"] - hot[code]["rank"])
                             if code in hot and hot[code].get("prev") and hot[code].get("rank") else None},
            "boards": boards_out, "primary_board": primary,
            "note": p.get("note", ""),
        })

    as_of = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    out = {
        "as_of": as_of, "trades_asof": trades_asof, "board_asof": board_asof,
        "meta": meta, "weekly_focus": cfg.get("weekly_focus", ""),
        "touch_count": touch_count, "positions": out_positions, "ops_review": ops_review,
        "battle_plan": battle_plan,
    }
    with open(latest_dir / "my_positions.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"   my_positions.json → {len(out_positions)} 只 (触价 {touch_count}, "
          f"板块口径 {board_asof or '—'}, 调仓核对至 {trades_asof or '—'}, "
          f"点评 {ops_review['date']}: ❌{ops_review['bad']} ⚠️{ops_review['warn']} ✅{ops_review['ok']})")


def _tencent_auction_amt(code: str):
    """腾讯分时首行 0930 行 = 09:25 集合竞价成交打印 → 竞价成交额(元), 取不到返回 None。
    口径同 scripts/lianban_bid_hs.py tencent_auction(09-03 双源验证); 分时接口只回当天,
    历史日期/盘外自然拿不到 → 调用方降级用 KPL 行。"""
    import urllib.request
    pfx = "sh" if code.startswith("6") else ("bj" if code[0] in "48" else "sz")
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={pfx}{code}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            rows = json.load(resp)["data"][pfx + code]["data"]["data"]
        first = rows[0].split()
        return float(first[3]) if first and first[0] == "0930" else None
    except Exception:
        return None


def build_lianban_bid(latest_dir: Path, crawl_date: str):
    """data/latest/lianban_bid.json —— 出击列表"昨日连板×竞价实际换手Top5"标记数据源。
    连板名单 = limit_pool 前一交易日 pid_type>=2; 换手 = 竞价成交额/流通市值,
    优先 KPL bid_pool.turnover_ratio, 0值腾讯分时补算(口径=lianban_bid_hs.py)。"""
    out = {"date": crawl_date, "prev_day": None, "top": []}
    try:
        with sqlite3.connect(f"file:{ROOT / 'data' / 'auction.db'}?mode=ro", uri=True) as c:
            prev = c.execute("SELECT MAX(date) FROM limit_pool WHERE date < ?", (crawl_date,)).fetchone()[0]
            out["prev_day"] = prev
            lianban = {}
            if prev:
                for code, name, pid, mv in c.execute(
                        "SELECT code, name, pid_type, circ_mv FROM limit_pool WHERE date=? AND pid_type>=2", (prev,)):
                    lianban[code] = {"code": code, "name": name, "prev_pid": pid, "mv_prev": mv}
            bid = {}
            for code, ratio, mv in c.execute(
                    "SELECT code, turnover_ratio, circ_mv FROM bid_pool WHERE date=?", (crawl_date,)):
                if code not in bid or (ratio or 0) > (bid[code][0] or 0):
                    bid[code] = (ratio, mv)
    except Exception as e:
        print(f"⚠️ lianban_bid: auction.db 不可读({e}), 输出空标记")
        with open(latest_dir / "lianban_bid.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        return
    if lianban:
        _np = os.environ.get("NO_PROXY", "")
        if "gtimg.cn" not in _np:
            os.environ["NO_PROXY"] = _np + ("," if _np else "") + "gtimg.cn,.gtimg.cn,ifzq.gtimg.cn"
    for code, s in lianban.items():
        ratio, mv = bid.get(code, (None, None))
        src = "KPL"
        if not ratio:
            mv = mv or s["mv_prev"]
            amt = _tencent_auction_amt(code) if mv else None
            if not amt:
                continue
            ratio, src = amt / mv * 100, "腾讯补算"
        out["top"].append({**s, "hs": round(ratio, 2), "src": src})
    out["top"].sort(key=lambda x: -x["hs"])
    out["top"] = out["top"][:5]
    with open(latest_dir / "lianban_bid.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    top_txt = " / ".join(f"{t['name']} {t['hs']:.2f}%" for t in out["top"]) or "无"
    print(f"   lianban_bid.json → {out['prev_day']}连板×{out['date']}竞价换手Top5: {top_txt}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出 SQLite 数据为 JSON（双轨输出）")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD)，默认最新")
    parser.add_argument("--out", type=str,
                        default=str(ROOT.parent / "stockboard-app" / "public" / "data"),
                        help="输出目录")
    args = parser.parse_args()

    if not args.date:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT MAX(crawl_date) FROM positions WHERE crawl_date != ''"
        ).fetchone()
        conn.close()
        args.date = (row[0] if row and row[0] else date.today().isoformat())

    export(DB_PATH, args.date, args.out)
