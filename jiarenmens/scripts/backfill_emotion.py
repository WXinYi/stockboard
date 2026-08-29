#!/usr/bin/env python3
"""
情绪/涨停池历史回补(周期引擎的数据地基):
  1. RiseFallAnalysis His(st=250) → market_breadth: 近 250 交易日 涨停/跌停/炸板数/破板率
  2. DailyLimitPerformance His 逐日 → limit_pool 全字段回补(涨停时间/封单/最大封单/主力净额/成交额
     —— 旧 verify_dragon 回补只存 5 字段, 时间线全丢, 本脚本补齐并顺带修复断档)

用法(在 jiarenmens/ 目录, 需直连大陆: NO_PROXY=longhuvip.com):
  python scripts/backfill_emotion.py --breadth            # 250 天市场宽度(一次请求)
  python scripts/backfill_emotion.py --pool 2026-08-22 2026-08-28
  python scripts/backfill_emotion.py --breadth --pool 2026-08-22 2026-08-28
"""
import argparse
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.spiders.auction_spider import KPLSpider  # noqa: E402
from src.config import KPL_HOST_HIS  # noqa: E402

DB = ROOT / "data" / "auction.db"
PIDS = [1, 2, 3, 4, 5]


def init_tables(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS market_breadth(
        date TEXT PRIMARY KEY, zt INTEGER, dt INTEGER, natural_zt INTEGER,
        once_dt INTEGER, broke_rate REAL, zhaban INTEGER);
    """)


def backfill_breadth(spider: KPLSpider) -> int:
    """近 250 交易日市场宽度(一次请求): [涨停,跌停,自然涨停,曾跌停,破板率,炸板数,日期]"""
    data = spider._get({"a": "RiseFallAnalysis", "st": 250, "apiv": "w43",
                        "c": "HisHomeDingPan", "PhoneOSNew": 1, "Index": 0}, KPL_HOST_HIS)
    rows = data.get("info") or []
    n = 0
    with sqlite3.connect(DB) as conn:
        init_tables(conn)
        for r in rows:
            if not isinstance(r, list) or len(r) < 7:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO market_breadth"
                "(date, zt, dt, natural_zt, once_dt, broke_rate, zhaban) VALUES(?,?,?,?,?,?,?)",
                (str(r[6]), r[0], r[1], r[2], r[3], r[4], r[5]))
            n += 1
    print(f"  💾 market_breadth 落库 {n} 天 (auction.db)")
    return n


def trading_days(start: str, end: str):
    days = []
    d = date.fromisoformat(start)
    while d <= date.fromisoformat(end):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def backfill_pool(spider: KPLSpider, start: str, end: str) -> int:
    """涨停池全字段回补: r[0]代码 r[1]名称 r[4]涨停时间 r[5]原因 r[6]封单 r[7]最大封单
       r[8]主力净额 r[11]成交额 r[12]板块 r[13]实际流通; PidType=板高"""
    total = 0
    with sqlite3.connect(DB) as conn:
        for d in trading_days(start, end):
            got = 0
            for pid in PIDS:
                try:
                    data = spider.zt_pool(d, pid_type=pid, st=500)
                    rows = [r for g in data.get("info", []) if isinstance(g, list)
                            for r in g if isinstance(r, list) and len(r) >= 14]
                    for r in rows:
                        conn.execute(
                            """INSERT OR REPLACE INTO limit_pool
                            (date, code, name, pid_type, zt_time, reason, seal_amount,
                             max_seal, main_net, amount, plates, circ_mv, tag)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (d, str(r[0]), str(r[1]), pid,
                             r[4] if isinstance(r[4], (int, float)) else None,
                             str(r[5]), r[6], r[7], r[8], r[11],
                             str(r[12]), r[13], f"{pid}连板"))
                        got += 1
                    time.sleep(0.15)
                except Exception as e:
                    print(f"  ⚠️ {d} P{pid} 失败: {e}")
                    time.sleep(0.5)
            print(f"  ✓ {d}: {got} 条涨停(板位1-5)")
            total += got
    print(f"  💾 limit_pool 全字段落库 {total} 条")
    return total


def main():
    ap = argparse.ArgumentParser(description="情绪/涨停池历史回补")
    ap.add_argument("--breadth", action="store_true", help="回补 250 天市场宽度")
    ap.add_argument("--pool", nargs=2, metavar=("START", "END"), help="回补涨停池(全字段)")
    args = ap.parse_args()
    if not args.breadth and not args.pool:
        ap.error("至少指定 --breadth 或 --pool")
    spider = KPLSpider()
    if args.breadth:
        backfill_breadth(spider)
    if args.pool:
        backfill_pool(spider, args.pool[0], args.pool[1])


if __name__ == "__main__":
    main()
