#!/usr/bin/env python3
"""龙头战法选手收益筛选 —— 买入后收益(FIFO 兑现)为主, 涨停率/连板率为风格参考

用户核心关注: 「买入后的收益」。持仓快照法(持仓法)会漏掉卖出后的收益,
故直接用 trades 表的 买入价→卖出价 FIFO 匹配, 计算每笔买入的兑现收益。

指标口径:
  - 兑现收益  = 卖出价/买入价 - 1 (FIFO 匹配, 每笔买入闭环)
  - 浮盈收益  = 现价/买入价 - 1   (仍持有、无卖出的, 用最新持仓快照现价 mark-to-market)
  - 平均收益  = 等权(每笔同权) | 仓位加权均(按仓位文本 9成以上=9.5, 8成=8, ... 1成=1, 1成以下=0.5)
  - 胜率      = 盈利笔数 / 闭环笔数 (浮盈按现价>买入价计盈利)
  - 持有天数  = 买入到卖出的交易日数(该股K线日期序列)
  - 涨停率/连板率/最高板 = 与 screen_dragon 同口径(涨停池交叉), 作为「是不是龙头打法」的风格参考

数据:
  - trades 表: 买入/卖出 价格 + 笔数 + 仓位文本 (price 有值; position_value 金额为空)
  - positions 表: 最新快照 成本价/现价 (浮盈)
  - limit_pool (auction.db): 涨停池 07-15~08-13

用法(在 jiarenmens/ 目录下):
    python3 scripts/screen_return.py [--min-closed 5] [--top 40]
"""
import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CRAWL_DB = DATA_DIR / "crawl_data.db"
AUCTION_DB = DATA_DIR / "auction.db"

WINDOW = ("2026-07-15", "2026-08-14")


def is_stock(code: str, name: str) -> bool:
    if not code or len(code) != 6 or not code.isdigit():
        return False
    if code[0] in ("1", "5"):
        return False
    if "转债" in name or "ETF" in name or "LOF" in name:
        return False
    return True


def weight_of(ratio_text: str) -> float:
    """仓位文本 → 权重 (9成以上=9.5, 8成=8 ... 1成=1, 1成以下=0.5)"""
    s = (ratio_text or "").strip()
    if not s:
        return 1.0
    if "以上" in s or "满仓" in s:
        return 9.5
    m = re.search(r"(\d+)成", s)
    if m:
        return float(m.group(1))
    if "以下" in s:
        return 0.5
    return 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-closed", type=int, default=5, help="闭环买入(有价)笔数下限")
    ap.add_argument("--min-rate", type=float, default=0.0, help="涨停率下限(龙头风格过滤, 如 0.4)")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    # ── 涨停池 (风格参考) ──
    pool = {}
    with sqlite3.connect(AUCTION_DB) as conn:
        for (d, code, pid) in conn.execute(
            "SELECT date, code, pid_type FROM limit_pool").fetchall():
            pool[(d, code)] = pid
    pool_dates = {d for (d, _) in pool}

    # ── 选手/名称上下文 ──
    ctx = {}
    with sqlite3.connect(CRAWL_DB) as conn:
        c = conn.cursor()
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT zh_id, name, followers, total_return, monthly_return, days FROM players"):
            ctx[r["zh_id"]] = {"name": r["name"], "followers": r["followers"],
                               "total": r["total_return"], "month": r["monthly_return"], "days": r["days"]}

    # ── 最新持仓快照 (浮盈用) ──
    with sqlite3.connect(CRAWL_DB) as conn:
        latest = conn.execute("SELECT MAX(crawl_date) FROM positions").fetchone()[0]
        cur_price = {}
        for (zid, code, cp) in conn.execute(
            "SELECT zh_id, stock_code, current_price FROM positions WHERE crawl_date=?", (latest,)):
            if cp and cp > 0:
                cur_price[(zid, code)] = cp
    print(f"最新持仓快照: {latest}")

    # ── 逐选手 FIFO 兑现收益 ──
    print(f"\n=== FIFO 兑现收益计算 (窗口 {WINDOW[0]}~{WINDOW[1]}, 用有价记录) ===")
    # 去重: 同 (date, code, direction) 多日快照取 MAX(price)
    trades = defaultdict(lambda: defaultdict(list))  # zh_id -> code -> [(date, dir, price, units, w)]
    with sqlite3.connect(CRAWL_DB) as conn:
        c = conn.cursor()
        c.row_factory = sqlite3.Row
        for r in c.execute("""SELECT zh_id, stock_name, stock_code, trade_date, direction,
                                     MAX(price) price, MAX(trades_count) tc, position_ratio
                              FROM trades
                              WHERE trade_date BETWEEN ? AND ? AND direction IN ('买入','卖出')
                              GROUP BY zh_id, stock_name, stock_code, trade_date, direction""",
                           WINDOW):
            if not is_stock(r["stock_code"], r["stock_name"]):
                continue
            rec = (r["trade_date"], r["direction"], r["price"], max(r["tc"] or 1, 1),
                   weight_of(r["position_ratio"]))
            trades[r["zh_id"]][r["stock_code"]].append(rec)

    results = []
    for wid, stocks in trades.items():
        closed = []   # (收益, 权重, 持有日)
        open_ = []    # (收益, 权重) 浮盈
        skip = 0
        for code, recs in stocks.items():
            recs.sort(key=lambda x: x[0])
            queue = []  # (date, price, units, w)
            for (d, dr, price, units, w) in recs:
                if dr == "买入":
                    queue.append([d, price, units, w])
                else:  # 卖出
                    remain = units
                    while remain > 0 and queue:
                        b = queue[0]
                        closed_units = min(remain, b[2])
                        if b[1] > 0 and price > 0:
                            for _ in range(closed_units):  # 一笔卖出闭合多笔买入 → 按笔展开
                                closed.append((price / b[1] - 1, b[3]))
                        else:
                            skip += closed_units
                        b[2] -= closed_units
                        remain -= closed_units
                        if b[2] <= 0:
                            queue.pop(0)
                    if remain > 0:
                        skip += remain  # 卖出多于买入: 成本在窗口外/缺失, 无法定价
            # 剩余队列 = 仍持仓 → 浮盈
            for (d, bprice, units, w) in queue:
                if bprice > 0:
                    cp = cur_price.get((wid, code))
                    if cp:
                        open_.append((cp / bprice - 1, w))
                    else:
                        skip += 1
        if len(closed) < args.min_closed:
            continue
        # 涨停率/连板率 (风格)
        wbuys = [(d, code) for code, recs in stocks.items() for (d, dr, p, u, w) in recs if dr == "买入" and d in pool_dates]
        cover = set(wbuys)
        zt = [(d, c) for (d, c) in cover if (d, c) in pool]
        rate = len(zt) / len(cover) if cover else 0
        if rate < args.min_rate:
            continue
        lb_rate = (sum(1 for (d, c) in zt if pool[(d, c)] >= 2) / len(zt)) if zt else 0
        max_board = max((pool[(d, c)] for (d, c) in zt), default=0)
        avg_eq = sum(r for r, _ in closed) / len(closed)
        wsum = sum(w for _, w in closed)
        avg_w = sum(r * w for r, w in closed) / wsum if wsum else 0
        win = sum(1 for r, _ in closed if r > 0) / len(closed)
        results.append({
            "zh_id": wid, "n_closed": len(closed), "n_open": len(open_),
            "avg": avg_eq, "avg_w": avg_w, "win": win,
            "rate": rate, "lb_rate": lb_rate, "max_board": max_board,
            "skip": skip,
        })

    results.sort(key=lambda x: (-x["avg_w"], -x["win"], -x["n_closed"]))
    print(f"闭环≥{args.min_closed}笔的选手: {len(results)} 人")

    # ── 输出 ──
    hdr = (f"{'#':>3} {'选手':<9} {'闭环':>4} {'未平':>4} {'仓位加权':>8} {'等权':>7} {'胜率':>6}"
           f" {'涨停率':>6} {'连板率':>6} {'最高':>5} {'月收益':>8} {'总收益':>9}")
    print(hdr)
    print("-" * len(hdr))
    for i, r in enumerate(results[:args.top], 1):
        c = ctx.get(r["zh_id"], {})
        name = (c.get("name") or r["zh_id"])[:9]
        mo = c.get("month") or 0
        tt = c.get("total") or 0
        print(f"{i:>3} {name:<9} {r['n_closed']:>4} {r['n_open']:>4} {r['avg_w']*100:>+8.1f}% {r['avg']*100:>+7.1f}%"
              f" {r['win']:>6.0%} {r['rate']:>6.0%} {r['lb_rate']:>6.0%} {r['max_board']:>4}板"
              f" {mo:>+8.1f}% {tt:>+9.1f}%")

    out = DATA_DIR / "return_screen.json"
    full = []
    for r in results:
        c = ctx.get(r["zh_id"], {})
        row = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
        for k in ("avg", "avg_w"):
            row[k] = round(row[k] * 100, 2)  # 小数 → 百分比
        row["win"] = round(row["win"] * 100, 1)
        row.update({"name": c.get("name", r["zh_id"]), "month": c.get("month"),
                    "total": c.get("total"), "followers": c.get("followers")})
        full.append(row)
    out.write_text(json.dumps(full, ensure_ascii=False, indent=1))
    print(f"\n💾 全量结果已存: {out}")
    print("\n说明: 仓位加权=按仓位文本加权(重仓权重高); 等权=每笔同权; 胜率=盈利闭环/闭环笔数;")
    print("      未平=仍在持仓用现价计浮盈; 涨停率/连板率为风格参考; 月/总收益为排行榜战绩")


if __name__ == "__main__":
    main()
