#!/usr/bin/env python3
"""龙头战法选手验证 v2 —— 拉长涨停池历史 + 买入后第二日收益

v1 局限: 涨停池只覆盖 08-07~08-13 一周(可对照样本小, 4/4 只有 4 笔)。
v2 改进:
  1. 涨停池历史拉长到选手调仓窗口全覆盖(07-15~08-13), 落库 auction.db 供复用
  2. 新增「买入后第二日收益」: 涨停日买入后, 次日开盘/收盘相对买入日的涨跌
     - 次日开盘收益 = open_{D+1}/close_D - 1  (打板次日的高开兑现)
     - 次日收盘收益 = close_{D+1}/close_D - 1  (打板次日的持有结果)
     - 实际兑现收益 = 选手自身卖价/买价 - 1     (用 DB 里该股后续卖出价)

数据源:
  - 涨停池: KPL His DailyLimitPerformance (apphis.longhuvip.com, 免Token)
  - 选手调仓: jiarenmens/data/crawl_data.db trades 表 (累积快照 07-22~08-14)
  - 日K: 腾讯 qfq 日K (http://web.ifzq.gtimg.cn/appstock/app/fqkline/get)

用法(在 jiarenmens/ 目录下):
    python3 scripts/verify_dragon.py [--start 2026-07-15] [--end 2026-08-13]
    python3 scripts/verify_dragon.py --watched   # 验证 main.py WATCHED_PLAYERS 全部关注选手
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.spiders.auction_spider import KPLSpider  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CRAWL_DB = DATA_DIR / "crawl_data.db"
AUCTION_DB = DATA_DIR / "auction.db"

# 龙头战法跟踪选手: id -> (名称, 操作风格标签)
DRAGON = {
    "900315547": ("西门星辰啊", "埋伏型龙头"),
    "900428477": ("多多易战", "纯龙头接力"),
    "900351276": ("新生代柚子04", "重仓龙头+小仓试错"),
    "900018239": ("新缘众妙之门", "短线接力"),
}

PIDS = list(range(1, 6))  # DailyLimitPerformance PidType 1一板~5五板及以上


# =============================================================================
# 1. 涨停池历史: 拉取 + 落库
# =============================================================================
def trading_days(start: date, end: date):
    """逐日历日尝试拉涨停池, 仅保留有数据(交易日)的日期"""
    days, d = [], start
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def fetch_limit_pool_history(spider: KPLSpider, start: str, end: str) -> dict:
    """拉取 [start, end] 所有交易日涨停池(全板位), 落库 auction.db
    返回 {(date, code): {"name", "pid", "reason"}}"""
    pool = {}
    with sqlite3.connect(AUCTION_DB) as conn:
        for d in trading_days(date.fromisoformat(start), date.fromisoformat(end)):
            got = False
            for pid in PIDS:
                try:
                    data = spider.zt_pool(d, pid_type=pid, st=500)
                    rows = [r for g in data.get("info", []) if isinstance(g, list) for r in g]
                    for r in rows:
                        if len(r) < 14:
                            continue
                        code = str(r[0])
                        pool[(d, code)] = {"name": str(r[1]), "pid": pid, "reason": str(r[5])}
                        got = True
                except Exception as e:
                    print(f"  ⚠️ {d} PidType={pid} 失败: {e}")
                    time.sleep(0.5)
            if got:
                print(f"  ✓ {d}: {sum(1 for (dd, _) in pool if dd == d)} 只涨停(覆盖 PidType 1-5)")
            time.sleep(0.15)  # 避免打太狠
    # 落库(幂等 INSERT OR REPLACE), 供复用
    _persist_limit_pool(pool)
    return pool


def _persist_limit_pool(pool: dict):
    """将涨停池历史写入 auction.db limit_pool 表"""
    with sqlite3.connect(AUCTION_DB) as conn:
        c = conn.cursor()
        for (d, code), info in pool.items():
            c.execute("""INSERT OR REPLACE INTO limit_pool
                (date, code, name, pid_type, reason) VALUES (?,?,?,?,?)""",
                (d, code, info["name"], info["pid"], info["reason"]))
        conn.commit()
    n = len(pool)
    print(f"  💾 涨停池落库完成: {n} 条 (auction.db limit_pool)")


# =============================================================================
# 2. 选手调仓: 从 crawl_data.db 取买入记录
# =============================================================================
def is_stock(code: str, name: str) -> bool:
    """是否 A 股正股(排除 ETF/转债/其他): 6位数字, 首字母不在 {1,5}"""
    if not code or len(code) != 6 or not code.isdigit():
        return False
    if code[0] in ("1", "5"):  # 1=转债/国债, 5=SH ETF
        return False
    if "转债" in name or "ETF" in name or "LOF" in name:
        return False
    return True


def load_buys() -> dict:
    """每位选手的买入记录, 按 (trade_date, stock_code) 去重(多日快照取最新价)
    返回 {zh_id: {date: [(code, name, price), ...]}} 以及 {zh_id: {code: [卖出价...]}}"""
    buys = defaultdict(lambda: defaultdict(list))
    sells = defaultdict(list)
    with sqlite3.connect(CRAWL_DB) as conn:
        c = conn.cursor()
        c.row_factory = sqlite3.Row
        for wid in DRAGON:
            rows = c.execute("""SELECT trade_date, stock_name, stock_code, direction,
                                       MAX(price) AS price
                                FROM trades WHERE zh_id=?
                                GROUP BY trade_date, stock_code, direction
                                ORDER BY trade_date""", (wid,)).fetchall()
            for r in rows:
                if not r["stock_code"]:
                    continue
                code = r["stock_code"]
                name = r["stock_name"]
                if not is_stock(code, name):
                    continue
                if r["direction"] == "买入":
                    buys[wid][r["trade_date"]].append((code, name, r["price"]))
                else:
                    sells[wid].append((r["trade_date"], code, r["price"]))
    return buys, sells


# =============================================================================
# 3. 腾讯日K: 次日收益
# =============================================================================
def _market(code: str) -> str:
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def fetch_kline(codes) -> dict:
    """腾讯 qfq 日K → {code: {date: {"open":, "close":}}}"""
    out = {}
    for code in codes:
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
            if day_map:
                out[code] = day_map
        except Exception as e:
            print(f"  ⚠️ K线失败 {code}: {e}")
        time.sleep(0.1)
    return out


# =============================================================================
# 4. 验证 + 次日收益
# =============================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-15")
    ap.add_argument("--end", default="2026-08-13")
    ap.add_argument("--watched", action="store_true",
                    help="验证 main.py WATCHED_PLAYERS 全部关注选手(默认仅 DRAGON 4 人)")
    ap.add_argument("--skip-fetch", action="store_true", help="跳过拉取涨停池, 直接用库内数据")
    args = ap.parse_args()

    if args.watched:
        global DRAGON
        from main import WATCHED_PLAYERS  # 关注名单单一数据源(main.py), 勿在别处另建名单
        DRAGON = {zh: (name, DRAGON.get(zh, (None, "关注选手"))[1])
                  for zh, name in WATCHED_PLAYERS}

    print("=== 步骤1: 涨停池历史 ===")
    if args.skip_fetch:
        pool = {}
        with sqlite3.connect(AUCTION_DB) as conn:
            for (d, code, name, pid, reason) in conn.execute(
                "SELECT date, code, name, pid_type, reason FROM limit_pool"
            ).fetchall():
                pool[(d, code)] = {"name": name, "pid": pid, "reason": reason}
        print(f"  直接读库: {len(pool)} 条")
    else:
        spider = KPLSpider()
        pool = fetch_limit_pool_history(spider, args.start, args.end)

    pool_dates = sorted({d for (d, _) in pool})
    print(f"  涨停池覆盖交易日: {pool_dates[0] if pool_dates else None} ~ {pool_dates[-1] if pool_dates else None} ({len(pool_dates)} 天)")

    print("\n=== 步骤2: 选手买入记录 ===")
    buys, sells = load_buys()
    for wid, (wname, style) in DRAGON.items():
        n = sum(len(v) for v in buys[wid].values())
        print(f"  {wname}: {n} 笔买入")

    # 次日收益所需 K 线: 涨停日买入的股票(取有涨停买入的选手)
    print("\n=== 步骤3: 拉取日K(次日收益) ===")
    all_codes = set()
    for wid in DRAGON:
        for d, items in buys[wid].items():
            for (code, name, price) in items:
                if (d, code) in pool:
                    all_codes.add(code)
    kline = fetch_kline(sorted(all_codes))
    print(f"  K线股票数: {len(kline)}/{len(all_codes)}")

    print("\n=== 步骤4: 验证结果 ===")
    for wid, (wname, style) in DRAGON.items():
        # 可对照买入 = 买入日在涨停池覆盖范围内
        coverable = []
        uncovered = []
        for d, items in buys[wid].items():
            for (code, name, price) in items:
                if d in set(pool_dates):
                    coverable.append((d, code, name, price))
                else:
                    uncovered.append((d, code, name, price))
        zt = [(d, code, name, price, pool[(d, code)]["pid"]) for (d, code, name, price) in coverable if (d, code) in pool]
        zt_dates = {d for (d, *_ ) in zt}

        print(f"\n──── {wname} · {style} ────")
        print(f"  买入: 共{len(coverable)+len(uncovered)}笔, 可对照{len(coverable)}笔, 涨停池外{len(uncovered)}笔"
              f"({[f'{d}{n}' for d, code, n, p in uncovered] if uncovered else '无'})")

        if zt:
            zt_rate = len(zt) / len(coverable) * 100 if coverable else 0
            lb = [p for (_, _, _, _, p) in zt]
            lb_rate = sum(1 for p in lb if p >= 2) / len(lb) * 100
            print(f"  涨停日买入: {len(zt)}/{len(coverable)} = {zt_rate:.1f}% | "
                  f"连板率(≥2板) {lb_rate:.0f}% | 最高板 {max(lb)}板 | 涨停日: {sorted(zt_dates)}")

            # 次日收益 + 实际兑现(选手自身卖价, 披露持有交易日数)
            next_returns = []  # (日期, 代码, 名称, 板, 次日开盘%, 次日收盘%, 实际兑现%, 持有交易日)
            for d, code, name, price, pid in sorted(zt):
                kmap = kline.get(code, {})
                dates = sorted(kmap.keys())
                if d not in kmap or len(dates) < 2:
                    continue
                i = dates.index(d)
                if i + 1 >= len(dates):
                    continue
                nxt = dates[i + 1]
                close_d = kmap[d]["close"]
                if close_d <= 0:
                    continue
                open_r = (kmap[nxt]["open"] / close_d - 1) * 100
                close_r = (kmap[nxt]["close"] / close_d - 1) * 100
                # 实际兑现: DB 里该股买入后的最近卖出价 + 持有交易日数(用该股K线日期序列数)
                realized, hold_days = None, None
                for sd, sc, sp in sorted(sells.get(wid, [])):
                    if sc == code and sd > d and sp > 0 and price > 0:
                        realized = (sp / price - 1) * 100
                        if sd in dates and i >= 0:
                            hold_days = dates.index(sd) - i
                        else:
                            hold_days = None
                        break
                next_returns.append((d, code, name, pid, open_r, close_r, realized, hold_days))
                rl = f" 实际{realized:+.1f}%(持有{hold_days}日)" if realized is not None else ""
                print(f"    {d} {name}({code}) {pid}板: 次日开盘{open_r:+.1f}% 收盘{close_r:+.1f}%{rl}")

            if next_returns:
                opens = [r[4] for r in next_returns]
                closes = [r[5] for r in next_returns]
                realized = [(r[6], r[7]) for r in next_returns if r[6] is not None]
                n = len(next_returns)
                print(f"  ▶ 次日收益: 开盘 平均{sum(opens)/n:+.1f}% 中位{sorted(opens)[n//2]:+.1f}% 高开{sum(1 for x in opens if x>0)}/{n}笔"
                      f" | 收盘 平均{sum(closes)/n:+.1f}% 中位{sorted(closes)[n//2]:+.1f}% 收红{sum(1 for x in closes if x>0)}/{n}笔")
                if realized:
                    rv = [r[0] for r in realized]
                    hd = [r[1] for r in realized if r[1] is not None]
                    hd_s = f", 平均持有{sum(hd)/len(hd):.1f}交易日" if hd else ""
                    print(f"  ▶ 实际兑现(选手自身卖出价): 平均{sum(rv)/len(rv):+.1f}% ({len(rv)}笔{hd_s})")
        else:
            print(f"  涨停日买入: 0 笔")

    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()
