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
import sqlite3, json, os, argparse
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"

WATCHED_IDS = {"900240956", "900354116", "900438148", "900376763", "900013608", "900429191", "900369020", "900223455", "900372673"}


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
            "_id": t["id"],  # 用于二级排序（不渲染）
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
    players_list = [
        [p["id"], p["name"], p["followers"],
         round(p["total_return"], 2), round(p["daily_return"], 2),
         round(p["weekly_return"], 2), round(p["monthly_return"], 2),
         round(p["yearly_return"], 2), round(p["net_value"], 3),
         round(p["max_drawdown"], 2), round(p["win_rate"], 2),
         p["days"], len(p["labels"] or []), p["ranks"],
         p["total_position"], p["quality"],
         p["stocks"]]
        for p in players_flat
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

    # players/{zh_id}.json
    players_out_dir = latest_dir / "players"
    players_out_dir.mkdir(parents=True, exist_ok=True)
    for p in players_flat:
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
    print(f"   players/ → {n_players} 个选手详情文件")
    print(f"   选手: {len(all_players_raw)} | 持仓: {len(positions_raw)} | 调仓: {len(trades_raw)} | 高手: {len(quality_ids)}")


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
