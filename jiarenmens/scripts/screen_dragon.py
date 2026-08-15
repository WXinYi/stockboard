#!/usr/bin/env python3
"""龙头战法选手筛选 v2 —— 用拉长的涨停池(22交易日) + 次日收益重筛全选手池

目的: 帮用户从 12527 名有买入记录的选手中, 筛出值得追踪的龙头战法选手。

两阶段:
  A. 离线: 全池涨停率/连板率/最高板 (涨停池 07-15~08-13 落库 auction.db)
  B. 联网: 候选选手涨停日买入的次日收益 (腾讯 qfq 日K, 带缓存) + 实际兑现 + 持仓收益上下文

指标口径:
  - 涨停率      = 涨停日买入笔数 / 可对照买入笔数 (可对照=买入日在涨停池覆盖的交易日内)
  - 连板率      = 涨停买入中 pid_type>=2 的比例 | 最高板 = 买过的最大连板数
  - 次日开盘均  = 涨停买入次日开盘相对买入日收盘 (高开兑现)
  - 次日收盘均  = 涨停买入次日收盘相对买入日收盘 | 收红率 = 次日收盘>0 的比例 (=打板胜率)
  - 兑现均      = 选手自身买入价→最近卖出价的实际收益 (含持有交易日数)
  - 月/总收益   = 选手排行榜当前战绩 (上下文, 非本窗口)

用法(在 jiarenmens/ 目录下):
    python3 scripts/screen_dragon.py [--min-buys 5] [--min-zt-rate 0.4] [--min-zt 3]
    python3 scripts/screen_dragon.py --skip-kline   # 跳过联网, 只看涨停率/连板率/最高板
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CRAWL_DB = DATA_DIR / "crawl_data.db"
AUCTION_DB = DATA_DIR / "auction.db"
KLINE_CACHE = DATA_DIR / "kline_cache.json"

# 当前已追踪的龙头选手 (输出时高亮)
TRACKED = {"900315547", "900428477", "900351276", "900018239"}

WINDOW = ("2026-07-15", "2026-08-13")


def is_stock(code: str, name: str) -> bool:
    """是否 A 股正股(排除 ETF/转债/其他)"""
    if not code or len(code) != 6 or not code.isdigit():
        return False
    if code[0] in ("1", "5"):
        return False
    if "转债" in name or "ETF" in name or "LOF" in name:
        return False
    return True


def _market(code: str) -> str:
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def load_kline_cache() -> dict:
    if KLINE_CACHE.exists():
        return json.loads(KLINE_CACHE.read_text())
    return {}


def save_kline_cache(cache: dict):
    KLINE_CACHE.write_text(json.dumps(cache, ensure_ascii=False))


def fetch_kline(codes, cache: dict) -> dict:
    """腾讯 qfq 日K → {code: {date: {"open":, "close":}}} (带本地缓存)"""
    missing = [c for c in codes if c not in cache or not cache[c]]
    for code in missing:
        sym = f"{_market(code)}{code}"
        url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,90,qfq"
        try:
            with urllib.request.urlopen(url, timeout=8) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            data = d.get("data", {}).get(sym, {})
            kline = data.get("qfqday") or data.get("day") or []
            day_map = {}
            for k in kline:
                if len(k) >= 5:
                    day_map[k[0]] = {"open": float(k[1]), "close": float(k[2])}
            cache[code] = day_map
        except Exception as e:
            print(f"  ⚠️ K线失败 {code}: {e}")
            cache[code] = {}
        time.sleep(0.08)
    save_kline_cache(cache)
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-buys", type=int, default=5, help="可对照买入下限")
    ap.add_argument("--min-zt-rate", type=float, default=0.4, help="涨停率下限")
    ap.add_argument("--min-zt", type=int, default=3, help="涨停日买入笔数下限")
    ap.add_argument("--top", type=int, default=50, help="表格展示前 N 名")
    ap.add_argument("--skip-kline", action="store_true", help="跳过联网 K 线")
    args = ap.parse_args()

    # ── 涨停池 ──
    pool = {}
    with sqlite3.connect(AUCTION_DB) as conn:
        for (d, code, pid) in conn.execute(
            "SELECT date, code, pid_type FROM limit_pool").fetchall():
            pool[(d, code)] = pid
    pool_dates = {d for (d, _) in pool}
    print(f"涨停池: {len(pool)} 条, {len(pool_dates)} 个交易日 ({min(pool_dates)}~{max(pool_dates)})")

    # ── 阶段A: 全池涨停统计 ──
    print(f"\n=== 阶段A: 全池涨停率/连板率/最高板 (窗口 {WINDOW[0]}~{WINDOW[1]}) ===")
    buys = defaultdict(set)   # zh_id -> {(date, code)}
    sells = defaultdict(list)  # zh_id -> [(date, code, price)]
    with sqlite3.connect(CRAWL_DB) as conn:
        c = conn.cursor()
        c.row_factory = sqlite3.Row
        # 买入限定窗口(涨停池覆盖 07-15~08-13); 卖出不设上界(08-14 当日卖出也要能闭合持仓)
        for r in c.execute("""SELECT zh_id, stock_name, stock_code, trade_date, direction, MAX(price) price
                              FROM trades
                              WHERE (direction='买入' AND trade_date BETWEEN ? AND ?)
                                 OR (direction='卖出' AND trade_date >= ?)
                              GROUP BY zh_id, stock_name, stock_code, trade_date, direction""",
                           (WINDOW[0], WINDOW[1], WINDOW[0])):
            if not is_stock(r["stock_code"], r["stock_name"]):
                continue
            code, name = r["stock_code"], r["stock_name"]
            if r["direction"] == "买入":
                buys[r["zh_id"]].add((r["trade_date"], code, name, r["price"]))
            else:
                sells[r["zh_id"]].append((r["trade_date"], code, r["price"]))

    # 名字映射 + 上下文
    ctx = {}
    with sqlite3.connect(CRAWL_DB) as conn:
        c = conn.cursor()
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT zh_id, name, followers, total_return, monthly_return, days FROM players"):
            ctx[r["zh_id"]] = {"name": r["name"], "followers": r["followers"],
                               "total": r["total_return"], "month": r["monthly_return"], "days": r["days"]}

    candidates = []
    for wid, wbuys in buys.items():
        cover = [(d, code) for (d, code, name, p) in wbuys if d in pool_dates]
        if len(cover) < args.min_buys:
            continue
        zt = [(d, code) for (d, code) in cover if (d, code) in pool]
        if len(zt) < args.min_zt:
            continue
        rate = len(zt) / len(cover)
        if rate < args.min_zt_rate:
            continue
        pids = [pool[(d, code)] for (d, code) in zt]
        candidates.append({
            "zh_id": wid,
            "n": len(cover),
            "zt": len(zt),
            "rate": rate,
            "lb_rate": sum(1 for p in pids if p >= 2) / len(pids),
            "max_board": max(pids),
            "zt_buys": [(d, code, next(name for (dd, cc, name, p) in wbuys if (dd, cc) == (d, code))) for (d, code) in zt],
        })
    candidates.sort(key=lambda x: (-x["rate"], -x["zt"]))
    print(f"候选选手: {len(candidates)} 人 (可对照买入≥{args.min_buys}, 涨停买入≥{args.min_zt}, 涨停率≥{args.min_zt_rate:.0%})")

    if not candidates:
        print("无候选, 调低阈值重试")
        return

    # ── 阶段B: 次日收益 + 实际兑现 ──
    if args.skip_kline:
        for cd in candidates:
            cd.update({"open_avg": None, "close_avg": None, "win_rate": None,
                       "realized_avg": None, "hold_days": None})
    else:
        print(f"\n=== 阶段B: 拉取次日收益 K 线 ===")
        all_codes = sorted({code for cd in candidates for (_, code, _) in cd["zt_buys"]})
        print(f"候选涨停买入涉及 {len(all_codes)} 只股票, 拉取日K...")
        cache = load_kline_cache()
        kline = fetch_kline(all_codes, cache)
        for cd in candidates:
            opens, closes, win = [], [], []
            realized, holds = [], []
            for (d, code, name) in cd["zt_buys"]:
                kmap = kline.get(code, {})
                dates = sorted(kmap.keys())
                if d not in kmap or dates.index(d) + 1 >= len(dates):
                    continue
                i = dates.index(d)
                nxt = dates[i + 1]
                close_d = kmap[d]["close"]
                if close_d <= 0:
                    continue
                opens.append((kmap[nxt]["open"] / close_d - 1) * 100)
                closes.append((kmap[nxt]["close"] / close_d - 1) * 100)
                win.append(closes[-1] > 0)
                # 实际兑现: 最近卖出
                price = next(p for (dd, cc, name2, p) in buys[cd["zh_id"]] if (dd, cc) == (d, code))
                for sd, sc, sp in sorted(sells.get(cd["zh_id"], [])):
                    if sc == code and sd > d and sp > 0 and price > 0:
                        realized.append((sp / price - 1) * 100)
                        if sd in dates and i >= 0:
                            holds.append(dates.index(sd) - i)
                        break
            cd.update({
                "open_avg": sum(opens) / len(opens) if opens else None,
                "close_avg": sum(closes) / len(closes) if closes else None,
                "win_rate": sum(win) / len(win) if win else None,
                "n_ret": len(opens),
                "realized_avg": sum(realized) / len(realized) if realized else None,
                "hold_avg": sum(holds) / len(holds) if holds else None,
            })

    # ── 输出 ──
    print(f"\n=== 筛选结果 (按涨停率↓, 前 {min(args.top, len(candidates))} 名) ===")
    hdr = f"{'#':>3} {'选手':<8} {'买入':>3} {'涨停':>3} {'涨停率':>6} {'连板率':>6} {'最高板':>5} {'次日开':>7} {'次日收':>7} {'收红率':>6} {'兑现':>7} {'月收益':>7} {'总收益':>8}"
    print(hdr)
    print("-" * len(hdr))
    results = []
    for i, cd in enumerate(candidates[:args.top], 1):
        c = ctx.get(cd["zh_id"], {})
        name = c.get("name", cd["zh_id"])[:8]
        mark = " ◀在追踪" if cd["zh_id"] in TRACKED else ""
        oa = f"{cd['open_avg']:+.1f}%" if cd["open_avg"] is not None else "  -- "
        ca = f"{cd['close_avg']:+.1f}%" if cd["close_avg"] is not None else "  -- "
        wr = f"{cd['win_rate']:.0%}" if cd["win_rate"] is not None else " -- "
        rz = f"{cd['realized_avg']:+.1f}%" if cd["realized_avg"] is not None else "  -- "
        mo = f"{c.get('month') or 0:+.1f}%" if c.get("month") is not None else "  -- "
        tt = f"{c.get('total') or 0:+.1f}%" if c.get("total") is not None else "  -- "
        print(f"{i:>3} {name:<8} {cd['n']:>3} {cd['zt']:>3} {cd['rate']:>6.0%} {cd['lb_rate']:>6.0%} {cd['max_board']:>4}板"
              f" {oa:>7} {ca:>7} {wr:>6} {rz:>7} {mo:>7} {tt:>8}{mark}")
        results.append({**cd, "name": name})

    # 存完整结果 (合并选手战绩上下文)
    out = DATA_DIR / "dragon_screen.json"
    full = []
    for r in candidates:
        c = ctx.get(r["zh_id"], {})
        row = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
        row.update({"name": c.get("name", r["zh_id"]), "followers": c.get("followers"),
                    "month": c.get("month"), "total": c.get("total"), "days": c.get("days")})
        full.append(row)
    out.write_text(json.dumps(full, ensure_ascii=False, indent=1))
    print(f"\n💾 全量结果(含未展示)已存: {out}")

    # 当前追踪选手的位置
    print("\n=== 当前在追踪的 4 位龙头选手 ===")
    for wid, nm in [("900315547", "西门星辰啊"), ("900428477", "多多易战"),
                    ("900351276", "新生代柚子04"), ("900018239", "新缘众妙之门")]:
        idx = next((i for i, cd in enumerate(candidates, 1) if cd["zh_id"] == wid), None)
        print(f"  {nm}: " + (f"榜上第 {idx} 名" if idx else "未进入榜单(样本/阈值不足)"))
    print("\n说明: 收红率=次日收盘>0比例(打板胜率); 兑现=选手自身买入→最近卖出实际收益; 月/总收益为当前战绩, 非窗口口径")


if __name__ == "__main__":
    main()
