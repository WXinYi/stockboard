#!/usr/bin/env python3
"""炸板池归档: 选股宝 limit_up_broken → auction.db broken_pool(弱转强"昨日分歧"精确数据源)

用法(在 jiarenmens/ 目录):
  venv/bin/python scripts/backfill_broken.py                    # 补最近交易日(幂等, 收盘班调用)
  venv/bin/python scripts/backfill_broken.py --from 2026-07-15  # 区间回填(按 limit_pool 交易日)
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.spiders.xuangubao import fetch_broken_pool  # noqa: E402

DB = Path(__file__).resolve().parents[1] / "data" / "auction.db"


def main():
    ap = argparse.ArgumentParser(description="炸板池归档(选股宝源)")
    ap.add_argument("--from", dest="dfrom", help="起始日 YYYY-MM-DD(含), 配合 --to 区间回填")
    ap.add_argument("--to", dest="dto", help="结束日 YYYY-MM-DD(含), 默认=最近交易日")
    args = ap.parse_args()
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS broken_pool (date TEXT, code TEXT, name TEXT, "
                 "break_times INTEGER, change_pct REAL, turnover REAL, height INTEGER, "
                 "PRIMARY KEY(date, code))")
    to_d = args.dto or datetime.now().strftime("%Y-%m-%d")
    if args.dfrom:
        days = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM limit_pool WHERE date>=? AND date<=? ORDER BY date", (args.dfrom, to_d))]
    else:
        days = [conn.execute("SELECT MAX(date) FROM limit_pool").fetchone()[0]]
    total = 0
    for i, d in enumerate(days):
        rows = fetch_broken_pool(d)
        conn.execute("DELETE FROM broken_pool WHERE date=?", (d,))
        conn.executemany("INSERT OR REPLACE INTO broken_pool VALUES (?,?,?,?,?,?,?)",
                         [(d, r["code"], r["name"], r["break_times"], r["change_pct"],
                           r["turnover"], r["height"]) for r in rows])
        conn.commit()
        total += len(rows)
        print(f"{d}: {len(rows)} 只炸板")
        if i < len(days) - 1:
            time.sleep(0.4)
    conn.close()
    print(f"✅ broken_pool 完成 {len(days)} 天 {total} 条")


if __name__ == "__main__":
    main()
