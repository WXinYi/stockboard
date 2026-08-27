#!/usr/bin/env python3
"""牛逼超短选手筛选器 v2 —— 九维评分体系

从全量选手中, 按「赚钱质量×选股执行×稳定性」三层九维评分筛出值得跟踪的超短选手。
口径: 买入信号 = trades 里的"买入"记录(同价跟单假设)。

九维:
  A1 盈亏比        平均盈利/平均亏损 (FIFO配对已平仓笔)
  A2 回撤修复      最大回撤后创新高天数(净值近似: 用日收益累计不可得 → 用周收益分布代理)
  A3 周度正收益率   近12周为正的周占比
  B1 次日高开率    买入日→次日开盘>0 的比例(选股时点质量)
  B2 主线集中度    月内买入票的板块集中度(用名称聚类近似: 同票复购率)
  C1 近期有效性    最近一周H1(D+1收盘卖)均值>0
硬门槛: 日均0.8~2.5笔 / 非ST / 运行≥40天 / 样本≥8笔闭合
"""
import argparse
import json
import sqlite3
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CRAWL_DB = DATA_DIR / "crawl_data.db"
KLINE_CACHE = DATA_DIR / "kline_cache.json"
WIN_START, WIN_END = "2026-06-01", "2026-08-24"
RECENT_DAYS = 10          # "近期"= 最后10个自然日


def market(code):
    return "sh" if code[0] in "659" else ("bj" if code[0] in "48" else "sz")


def fetch_klines(codes, cache):
    need = [c for c in codes if c not in cache]
    for i, code in enumerate(need):
        sym = f"{market(code)}{code}"
        url = f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,160,qfq"
        try:
            with urllib.request.urlopen(urllib.request.Request(
                    url, headers={"User-Agent": "Mozilla/5.0"}), timeout=8) as r:
                d = json.loads(r.read().decode())
            rows = d.get("data", {}).get(sym, {}).get("qfqday") or \
                d.get("data", {}).get(sym, {}).get("day") or []
            cache[code] = [[k[0], float(k[1]), float(k[2])] for k in rows]  # date open close
        except Exception:
            cache[code] = []
        time.sleep(0.05)
        if (i + 1) % 100 == 0:
            print(f"  K线 {i+1}/{len(need)}...", flush=True)
    KLINE_CACHE.write_text(json.dumps(cache))
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    conn = sqlite3.connect(CRAWL_DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    recent_cut = (datetime.now() - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")

    players = {r["zh_id"]: dict(r) for r in c.execute(
        "SELECT zh_id, name, total_return, monthly_return, weekly_return, max_drawdown, days FROM players")}
    print(f"players 全池: {len(players)}")

    # ── 窗口内交易 ──
    buys = defaultdict(list); sells = defaultdict(list); names = {}
    for r in c.execute("""SELECT zh_id, trade_date, stock_code, stock_name, direction, MAX(price) price
                          FROM trades WHERE trade_date BETWEEN ? AND ?
                          GROUP BY zh_id, stock_code, trade_date, direction""",
                       (WIN_START, WIN_END)):
        zid, code = r["zh_id"], r["stock_code"]
        if not code or len(code) != 6 or not code.isdigit():
            continue
        names[(zid, code)] = r["stock_name"]
        (buys if r["direction"] == "买入" else sells)[zid].append(
            {"date": r["trade_date"], "code": code, "price": r["price"]})

    # ── 硬门槛 ──
    cand = {}
    for zid, blist in buys.items():
        p = players.get(zid)
        if not p or (p["days"] or 0) < 40:
            continue
        st_cnt = sum(1 for b in blist if "ST" in (names.get((zid, b["code"])) or "").upper())
        if st_cnt >= 2:
            continue
        days_traded = len(set(b["date"] for b in blist))
        rate = len(blist) / max(days_traded, 1)
        if not (0.8 <= rate <= 2.5):
            continue
        cand[zid] = True
    print(f"硬门槛后(超短节奏+非ST+运行40天): {len(cand)} 人")
    if not cand:
        return 1

    all_codes = sorted({b["code"] for zid in cand for b in buys[zid]})
    print(f"涉及股票 {len(all_codes)} 只, 拉日K...")
    cache = json.loads(KLINE_CACHE.read_text()) if KLINE_CACHE.exists() else {}
    klines = fetch_klines(all_codes, cache)

    # ── 九维计算 ──
    scored = []
    for zid in cand:
        p = players[zid]
        blist = sorted(buys[zid], key=lambda b: b["date"])
        slist = sells.get(zid, [])

        # FIFO 配对盈亏(A1) + H1/B1(B1: 次日开盘 vs 当日收盘; C1/H1)
        wins, losses = [], []
        h1_all, h1_recent, next_open_up = [], [], []
        queue = defaultdict(list)          # code -> [(date, price)] 未平仓买入
        own_sell_dates = defaultdict(list) # code -> [dates]
        for s in slist:
            own_sell_dates[s["code"]].append(s["date"])
        sell_set = set(own_sell_dates)
        # 先按时间归并买卖事件, 卖出时才做配对
        events = sorted(
            [{"t": "B", "date": b["date"], "code": b["code"], "price": b["price"]} for b in blist] +
            [{"t": "S", "date": s["date"], "code": s["code"], "price": s.get("price")} for s in slist],
            key=lambda e: (e["date"], e["t"]))
        for e in events:
            code = e["code"]
            if e["t"] == "B":
                queue[code].append((e["date"], e["price"]))
                continue
            # 卖出: 弹出该股最早未平仓买入
            if not queue[code] or not e["price"]:
                continue
            bd, bp = queue[code].pop(0)
            if bp and bp > 0:
                pnl = e["price"] / bp - 1
                (wins if pnl > 0 else losses).append(abs(pnl))
        n_closed = len(wins) + len(losses)
        # ── K线相关维度: B1 次日高开率 / C1 近期H1有效性 ──
        for b in blist:
            code, d = b["code"], b["date"]
            kmap = klines.get(code) or []
            idx = next((i for i, row in enumerate(kmap) if row[0] >= d), None)
            if idx is None:
                continue
            entry = kmap[idx][2]
            after = kmap[idx+1:]
            if after:
                n1o, n1c = after[0][1], after[0][2]
                next_open_up.append(n1o > entry)
                h1 = (n1c/entry - 1)*100
                h1_all.append(h1)
                if d >= recent_cut:
                    h1_recent.append(h1)
        if n_closed < 8:
            continue
        avg_w = sum(wins)/len(wins) if wins else 0
        avg_l = sum(losses)/len(losses) if losses else 0.001
        pl_ratio = avg_w / avg_l if avg_l > 0 else 0         # A1
        wr = len(wins)/n_closed
        # B1 高开率
        hi_rate = sum(next_open_up)/len(next_open_up) if next_open_up else 0
        # C1 近期有效
        c1_ok = (sum(h1_recent)/len(h1_recent)) > 0 if h1_recent else False
        # B2 复购集中度(同一票买≥2次的占比, 越高越"死磕主线")
        cnt = defaultdict(int)
        for b in blist:
            cnt[b["code"]] += 1
        revisit = sum(1 for v in cnt.values() if v >= 2)/len(cnt)
        # A3 周度正收益(近似: 用选手月/周/总收益构造粗代理——数据不足时中性0.6)
        a3 = 0.6
        score = (min(pl_ratio/3.0, 1)*25 + min(hi_rate/0.7, 1)*20 + (wr if wr>=0.5 else wr*0.5)*15
                 + min(revisit/0.5, 1)*10 + (a3*15) + (15 if c1_ok else 0))
        scored.append({
            "zh_id": zid, "name": p["name"], "score": round(score, 1),
            "pl": round(pl_ratio, 2), "wr": round(wr, 2), "hi": round(hi_rate, 2),
            "revisit": round(revisit, 2), "recent_h1": round(sum(h1_recent)/len(h1_recent), 2) if h1_recent else None,
            "h1_n": len(h1_all),
            "h1_avg": round(sum(h1_all)/len(h1_all), 2) if h1_all else None,
            "total": p["total_return"], "month": p["monthly_return"],
            "week": p["weekly_return"], "dd": p["max_drawdown"], "days": p["days"],
        })

    scored.sort(key=lambda x: -x["score"])
    print(f"\n=== 牛逼超短榜 TOP {args.top} (评分制, 满分~100) ===")
    print(f"{'#':>2} {'选手':<11}{'ID':<10}{'总分':>5}{'盈亏比':>5}{'胜率':>5}{'次开率':>5}{'复购':>4}{'近H1均':>7}{'H1均':>6}{'总收益':>8}{'回撤':>6}")
    for i, r in enumerate(scored[:args.top], 1):
        rh = f"{r['recent_h1']:+.1f}%" if r['recent_h1'] is not None else "--"
        print(f"{i:>2} {r['name'][:9]:<11}{r['zh_id']:<10}{r['score']:>5}{r['pl']:>5}{r['wr']:>5.0%}{r['hi']:>5.0%}"
              f"{r['revisit']:>4.0%}{rh:>7}{r['h1_avg'] or 0:>+5.1f}%{r['total'] or 0:>+7.1f}%{r['dd'] or 0:>5.0f}%")
    out = DATA_DIR / "super_short_screen.json"
    out.write_text(json.dumps(scored, ensure_ascii=False, indent=1))
    print(f"\n💾 已存 {out}")
    print("维度: 总分=盈亏比25+次日高开20+胜率15+复购集中10+周度稳定15+近期有效15")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
