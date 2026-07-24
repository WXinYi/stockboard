"""
导出 SQLite 数据为 JSON，供 Vue 看板使用

输出:
  latest/summary.json      — 所有 Tab 聚合数据
  latest/players/*.json    — 选手详情（按需加载）
  history/*.json           — 选手历史时间序列
  latest/changes.json      — 持仓变动 + 预警

用法:
    python scripts/export_json.py [--date 2026-07-24] [--out ../stockboard-app/public/data]
"""
import sqlite3, json, os, argparse
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"

WATCHED_IDS = {"900240956", "900354116"}


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

    # 所有日期的调仓（用于推测持仓 + 历史序列）
    all_trades = [dict(r) for r in conn.execute(
        """SELECT t.*, pl.name as player_name
           FROM trades t LEFT JOIN players pl ON t.zh_id = pl.zh_id
           ORDER BY t.crawl_date, t.trade_date"""
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
        [{"code": s["code"], "name": s["name"],
          "holders": s["holders"], "total_position": round(s["total_position"], 1),
          "avg_profit": round(s["total_profit"] / s["count"], 2) if s["count"] else 0}
         for s in stock_stats_map.values()],
        key=lambda s: s["total_position"], reverse=True
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
        "buySignals": [{"code": s["code"], "name": s["name"], "score": s["score"],
                         "totalPosition": s["totalPosition"], "holderCount": s["holderCount"],
                         "buyers": s["buyer_names"], "sellers": s["seller_names"]}
                        for s in buy_signals],
        "coreHoldings": [{"code": s["code"], "name": s["name"], "score": s["score"],
                           "totalPosition": s["totalPosition"], "holderCount": s["holderCount"],
                           "holders": s["holders"]}
                          for s in core_holdings],
        "sellWarnings": [{"code": s["code"], "name": s["name"], "score": s["score"],
                           "totalPosition": s["totalPosition"], "holderCount": s["holderCount"],
                           "sellers": s["seller_names"]}
                          for s in sell_warnings],
        "highQuality": [{"code": s["code"], "name": s["name"], "score": s["score"],
                          "totalPosition": s["totalPosition"], "holderCount": s["holderCount"],
                          "buyers": s["buyer_names"]}
                         for s in high_quality_signals],
    }

    # ── 5. 调仓共识 → tradeConsensus ────────
    trade_cons_map = {}
    for t in trades_raw:
        if t.get("trade_date", "") != crawl_date:
            continue
        code = t.get("stock_code", "")
        if not code:
            continue
        if code not in trade_cons_map:
            trade_cons_map[code] = {
                "code": code, "name": t.get("stock_name", ""),
                "buy_players": [], "sell_players": [],
                "buy_count": 0, "sell_count": 0,
            }
        tc = trade_cons_map[code]
        pn = t.get("player_name") or t.get("zh_id", "")
        if t.get("direction") == "买入":
            tc["buy_count"] += safe_int(t.get("trades_count"), 1)
            if pn not in tc["buy_players"]:
                tc["buy_players"].append(pn)
        else:
            tc["sell_count"] += safe_int(t.get("trades_count"), 1)
            if pn not in tc["sell_players"]:
                tc["sell_players"].append(pn)

    trade_consensus = []
    for tc in sorted(trade_cons_map.values(),
                     key=lambda tc: len(tc["buy_players"]) + len(tc["sell_players"]),
                     reverse=True):
        n = len(tc["buy_players"]) + len(tc["sell_players"])
        if n >= 10:
            strength, color = "🔥 强烈", "#e74c3c"
        elif n >= 5:
            strength, color = "📈 一般", "#e67e22"
        elif n >= 2:
            strength, color = "💡 微弱", "#2980b9"
        else:
            strength, color = "单一", "#999"
        trade_consensus.append({**tc, "strength": strength, "strengthColor": color})

    # ── 6. 行业板块 → sectorStats ────────────
    sector_map = {}
    for p in players_flat:
        labels = p.get("labels") or []
        if not labels:
            continue
        for lb in labels:
            if lb not in sector_map:
                sector_map[lb] = {"name": lb, "count": 0, "total_return": 0.0,
                                   "daily_return": 0.0, "total_position": 0.0,
                                   "qualityCount": 0}
            sm = sector_map[lb]
            sm["count"] += 1
            sm["total_return"] += p["total_return"]
            sm["daily_return"] += p["daily_return"]
            sm["total_position"] += p["total_position"]
            if p["quality"]:
                sm["qualityCount"] += 1
    sector_stats = sorted([
        {**v, "avg_return": round(v["total_return"] / v["count"], 2),
         "avg_daily": round(v["daily_return"] / v["count"], 2),
         "avg_position": round(v["total_position"] / v["count"], 1)}
        for v in sector_map.values()
    ], key=lambda s: s["count"], reverse=True)

    # ── 7. 仓位分布 → positionDist ───────────
    position_dist = {"9成以上": 0, "7-9成": 0, "5-7成": 0, "3-5成": 0,
                      "1-3成": 0, "1成以下": 0, "空仓": 0}
    for p in players_flat:
        tp = p["total_position"]
        if tp == 0:
            position_dist["空仓"] += 1
        elif tp < 10:
            position_dist["1成以下"] += 1
        elif tp < 30:
            position_dist["1-3成"] += 1
        elif tp < 50:
            position_dist["3-5成"] += 1
        elif tp < 70:
            position_dist["5-7成"] += 1
        elif tp < 90:
            position_dist["7-9成"] += 1
        else:
            position_dist["9成以上"] += 1

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
        if not any(p["zh_id"] == pid for p in trade_alerts_map[code]["players"]):
            trade_alerts_map[code]["players"].append({"name": pn, "zh_id": pid})
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

    # ── 8b. 盈亏分布 → profitDist ──────────
    profit_bins = {"<-10%": 0, "-10%~-5%": 0, "-5%~0%": 0, "0%~5%": 0,
                   "5%~10%": 0, "10%~20%": 0, ">20%": 0}
    stock_profits = {}
    for p in positions_raw:
        code = p.get("stock_code", "")
        if not code:
            continue
        if code not in stock_profits:
            stock_profits[code] = []
        stock_profits[code].append(safe_float(p.get("profit_ratio")))
    for code, profits in stock_profits.items():
        avg = sum(profits) / len(profits) if profits else 0
        if avg < -10:
            profit_bins["<-10%"] += 1
        elif avg < -5:
            profit_bins["-10%~-5%"] += 1
        elif avg < 0:
            profit_bins["-5%~0%"] += 1
        elif avg < 5:
            profit_bins["0%~5%"] += 1
        elif avg < 10:
            profit_bins["5%~10%"] += 1
        elif avg < 20:
            profit_bins["10%~20%"] += 1
        else:
            profit_bins[">20%"] += 1

    # ── 9. 多空对比 → stockCompare ───────────
    quality_set = quality_ids  # set of zh_id
    compare_map = {}
    for p in positions_raw:
        code = p.get("stock_code", "")
        if not code:
            continue
        if code not in compare_map:
            compare_map[code] = {"code": code, "name": p.get("stock_name", ""),
                                  "totalHolders": 0, "totalPosition": 0.0,
                                  "qualityHolders": 0,
                                  "allBuying": 0, "allSelling": 0,
                                  "qualityBuying": 0, "qualitySelling": 0}
        cm = compare_map[code]
        cm["totalHolders"] += 1
        cm["totalPosition"] += safe_float(p.get("position_ratio"))
        if p.get("zh_id") in quality_set:
            cm["qualityHolders"] += 1
    for t in trades_raw:
        if t.get("trade_date", "") != crawl_date:
            continue
        code = t.get("stock_code", "")
        if not code or code not in compare_map:
            continue
        cm = compare_map[code]
        if t.get("direction") == "买入":
            cm["allBuying"] += 1
            if t.get("zh_id") in quality_set:
                cm["qualityBuying"] += 1
        else:
            cm["allSelling"] += 1
            if t.get("zh_id") in quality_set:
                cm["qualitySelling"] += 1

    concentration = sorted(
        [{"code": cm["code"], "name": cm["name"],
          "totalHolders": cm["totalHolders"], "totalPosition": round(cm["totalPosition"], 1),
          "qualityHolders": cm["qualityHolders"],
          "allBuying": cm["allBuying"], "allSelling": cm["allSelling"],
          "qualityBuying": cm["qualityBuying"], "qualitySelling": cm["qualitySelling"]}
         for cm in compare_map.values()],
        key=lambda c: c["totalHolders"], reverse=True
    )[:30]

    divergence_list = []
    for cm in compare_map.values():
        if cm["totalHolders"] < 3:
            continue
        all_net = cm["allBuying"] - cm["allSelling"]
        quality_net = cm["qualityBuying"] - cm["qualitySelling"]
        divergence_list.append({
            "code": cm["code"], "name": cm["name"],
            "totalHolders": cm["totalHolders"], "totalPosition": round(cm["totalPosition"], 1),
            "qualityHolders": cm["qualityHolders"],
            "allNet": all_net, "qualityNet": quality_net,
            "gap": abs(all_net - quality_net),
        })
    divergence = sorted(divergence_list, key=lambda d: d["gap"], reverse=True)[:20]

    stock_compare = {"concentration": concentration, "divergence": divergence,
                     "qualityCount": len(quality_ids)}

    # ── 10. 持仓变动 → positionChanges + alerts ──
    # 需要最近两个日期的数据
    changes_data = None
    alerts = {"highByStock": [], "mid": [], "totalClear": 0}
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
                changes.append({
                    "zh_id": pid,
                    "player_name": (player_names.get(pid) or pid),
                    "stock_code": code,
                    "stock_name": (ref.get("stock_name") or "") if ref else "",
                    "type": change_type,
                    "emoji": emoji,
                    "delta": round(delta, 1),
                    "yesterdayRatio": round(yesterday_ratio, 1),
                    "todayRatio": round(today_ratio, 1),
                })
        changes.sort(key=lambda c: abs(c["delta"]), reverse=True)
        changes_data = {
            "hasHistory": True,
            "yesterday": yesterday,
            "today": today,
            "changes": changes,
            "added": [c for c in changes if c["type"] == "新进"],
            "cleared": [c for c in changes if c["type"] == "清仓"],
        }

        # alerts
        stock_alert_map = {}
        mid_alerts = []
        for c in changes:
            if c["type"] == "清仓":
                code = c["stock_code"]
                if code not in stock_alert_map:
                    stock_alert_map[code] = {"stock_name": c["stock_name"],
                                              "stock_code": code, "players": []}
                stock_alert_map[code]["players"].append(
                    {"name": c["player_name"], "zh_id": c["zh_id"]}
                )
            elif c["delta"] < -30:
                mid_alerts.append({
                    "player_name": c["player_name"], "zh_id": c["zh_id"],
                    "stock_name": c["stock_name"], "stock_code": c["stock_code"],
                })
        high_by_stock = sorted(stock_alert_map.values(),
                               key=lambda a: len(a["players"]), reverse=True)
        total_clear = sum(len(a["players"]) for a in high_by_stock)
        alerts = {"highByStock": high_by_stock, "mid": mid_alerts, "totalClear": total_clear}

    # ── 11. 今日有调仓的选手 ID ──────────────
    traded_player_ids = list(set(
        t["zh_id"] for t in trades_raw
        if t.get("trade_date", "") == crawl_date and t.get("zh_id")
    ))

    # ── 12. 构建 summary.json ────────────────
    summary = {
        "date": crawl_date,
        "crawl_time": "",  # 下面填入
        "players": [
            {"i": p["id"], "n": p["name"],
             "f": p["followers"],
             "T": p["total_return"], "d": p["daily_return"],
             "w": p["weekly_return"], "m": p["monthly_return"],
             "y": p["yearly_return"], "v": p["net_value"],
             "dd": p["max_drawdown"], "wr": p["win_rate"],
             "dy": p["days"], "lb": p["labels"], "rk": p["ranks"],
             "tp": p["total_position"], "q": p["quality"],
             "ss": p["stocks"]}
            for p in players_flat
        ],
        "qualityPlayerCount": len(quality_ids),
        "copyTradeSignals": copy_trade_signals,
        "stockStats": stock_stats,
        "tradeConsensus": trade_consensus,
        "sectorStats": sector_stats,
        "positionDist": position_dist,
        "profitDist": profit_bins,
        "stockCompare": stock_compare,
        "tradeAlerts": trade_alerts,
        "suspectedClears": suspected_clears,
        "tradedPlayerIds": traded_player_ids,
        "fullRankCount": sum(1 for p in players_flat if len(p.get("ranks") or []) >= 5),
        # 选手名 → zh_id 映射（name→id + id→id）
        "playerNameMap": {},  # 将在下面填充
    }
    name_map = {p["name"]: p["id"] for p in players_flat if p["name"]}
    for p in players_flat:
        name_map[p["id"]] = p["id"]  # id → id 映射
    summary["playerNameMap"] = name_map

    # ── 13. 构建选手详情文件 ──────────────────
    # positions/trades by player
    pos_by_pid = defaultdict(list)
    for p in positions_raw:
        pos_by_pid[p["zh_id"]].append({
            "stock_name": p.get("stock_name", ""),
            "stock_code": p.get("stock_code", ""),
            "cost_price": safe_float(p.get("cost_price")),
            "current_price": safe_float(p.get("current_price")),
            "profit_ratio": safe_float(p.get("profit_ratio")),
            "position_ratio": safe_float(p.get("position_ratio")),
        })

    trades_by_pid = defaultdict(list)
    for t in trades_raw:
        trades_by_pid[t["zh_id"]].append({
            "trade_date": t.get("trade_date", ""),
            "direction": t.get("direction", ""),
            "stock_name": t.get("stock_name", ""),
            "stock_code": t.get("stock_code", ""),
            "trades_count": safe_int(t.get("trades_count"), 1),
            "position_ratio": t.get("position_ratio", ""),
        })

    # 推测持仓
    def compute_inferred_positions(zh_id, confirmed_codes):
        stock_state = defaultdict(lambda: {"stock_name": "", "stock_code": "",
                                            "buys": [], "sells": []})
        for t in all_trades:
            if t.get("zh_id") != zh_id:
                continue
            code = t.get("stock_code", "")
            if not code or code in confirmed_codes:
                continue
            ss = stock_state[code]
            ss["stock_name"] = t.get("stock_name", "")
            ss["stock_code"] = code
            level = t.get("position_ratio") or "?"
            if t.get("direction") == "买入":
                ss["buys"].append({"level": level, "date": t.get("trade_date", "")})
            else:
                ss["sells"].append({"level": level, "date": t.get("trade_date", "")})

        result = []
        for code, ss in stock_state.items():
            if not ss["buys"]:
                continue
            buys_sorted = sorted(ss["buys"], key=lambda b: b["date"], reverse=True)
            sells_sorted = sorted(ss["sells"], key=lambda s: s["date"], reverse=True)
            latest_buy = buys_sorted[0]
            has_sells = len(sells_sorted) > 0

            if not has_sells:
                status = "持续买入"
                confidence = "mid" if len(ss["buys"]) >= 2 else "low"
            elif latest_buy["date"] > sells_sorted[0]["date"]:
                status = "近期加仓"
                confidence = "mid"
            elif sells_sorted[0]["date"] > latest_buy["date"] and latest_buy["level"] == sells_sorted[0]["level"]:
                continue  # skip — likely cleared
            else:
                status = "可能减持"
                confidence = "low"

            result.append({
                "stock_name": ss["stock_name"], "stock_code": code,
                "level_estimate": latest_buy["level"],
                "status": status, "confidence": confidence,
                "buy_count": len(ss["buys"]), "sell_count": len(ss["sells"]),
            })
        return sorted(result, key=lambda r: 0 if r["confidence"] == "mid" else 1)

    # ── 14. 构建历史时间序列 ──────────────────
    # Per-player: [{date, daily_return, total_return, net_value}, ...]
    # From players table: each crawl_date snapshot is different
    # Actually, we need to get players data per crawl_date
    # But the players table doesn't have crawl_date — it's UPSERTed
    # We need to reconstruct from positions/trades which have crawl_date
    # For history, we use the player's current snapshot for each date
    # Since players are UPSERTed, we can't get historical player data from SQLite
    #
    # Alternative: The history is constructed from what we have in all_positions/all_trades
    # For each player, for each date they have positions/trades, we have data
    # But daily_return etc. won't be historical
    #
    # SIMPLER: Since the frontend's getPlayerHistory uses allPlayersByDate
    # which reconstructs from the daily players.json files, we need to do the same.
    # Each date's players.json has the player's return values on that date.
    #
    # BUT: the players table always has the LATEST data (due to UPSERT).
    # Historical player snapshots don't exist in SQLite.
    #
    # To solve this: we can export the player history from the already-exported
    # \{date}/players.json files, or we can accept that history needs to be
    # built from existing daily exports.
    #
    # For NOW: we'll build history from the existing daily exports in the data dir.
    # If they don't exist, we skip.
    player_history = {}
    for date_str in all_dates:
        players_file = out_dir / date_str / "players.json"
        if not players_file.exists():
            continue
        try:
            date_players = json.loads(players_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in date_players:
            pid = p.get("zh_id") or p.get("id", "")
            if not pid:
                continue
            if pid not in player_history:
                player_history[pid] = []
            player_history[pid].append({
                "d": date_str,
                "dr": safe_float(p.get("daily_return")),
                "tr": safe_float(p.get("total_return")),
                "nv": safe_float(p.get("net_value")),
            })

    # ── 15. 写文件 ───────────────────────────

    # 读取 crawl_time
    crawl_start_file = ROOT / "data" / "crawl_start.txt"
    if crawl_start_file.exists():
        crawl_time = crawl_start_file.read_text().strip()
    else:
        crawl_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    summary["crawl_time"] = crawl_time

    # 15. 新格式输出
    latest_dir = out_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)

    # summary.json
    with open(latest_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, separators=(",", ":"))

    # players/{zh_id}.json
    players_out_dir = latest_dir / "players"
    players_out_dir.mkdir(parents=True, exist_ok=True)
    for p in players_flat:
        pid = p["id"]
        detail = {
            "id": pid,
            "name": p["name"],
            "followers": p["followers"],
            "total_return": p["total_return"],
            "daily_return": p["daily_return"],
            "weekly_return": p["weekly_return"],
            "monthly_return": p["monthly_return"],
            "yearly_return": p["yearly_return"],
            "net_value": p["net_value"],
            "max_drawdown": p["max_drawdown"],
            "win_rate": p["win_rate"],
            "days": p["days"],
            "concept": p["concept"],
            "intro": p["intro"],
            "labels": p["labels"],
            "ranks": p["ranks"],
            "total_position": p["total_position"],
            "quality": p["quality"],
            "positions": pos_by_pid.get(pid, []),
            "trades": trades_by_pid.get(pid, []),
            "inferred": compute_inferred_positions(pid, set(
                pp.get("stock_code", "") for pp in pos_by_pid.get(pid, [])
            )),
        }
        with open(players_out_dir / f"{pid}.json", "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, separators=(",", ":"))

    # history/{zh_id}.json
    history_dir = out_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    for pid, entries in player_history.items():
        entries.sort(key=lambda e: e["d"])
        with open(history_dir / f"{pid}.json", "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, separators=(",", ":"))

    # changes.json
    with open(latest_dir / "changes.json", "w", encoding="utf-8") as f:
        json.dump({"changes": changes_data, "alerts": alerts},
                  f, ensure_ascii=False, separators=(",", ":"))

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
    n_history = len(list(history_dir.glob("*.json")))
    summary_size = (latest_dir / "summary.json").stat().st_size / 1024
    changes_size = (latest_dir / "changes.json").stat().st_size / 1024 if changes_data else 0

    print(f"✅ 导出完成 ({crawl_date})")
    print(f"   summary.json → {summary_size:.0f}KB")
    print(f"   players/ → {n_players} 个选手详情文件")
    print(f"   history/ → {n_history} 个历史序列文件")
    if changes_data:
        print(f"   changes.json → {changes_size:.0f}KB")
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
