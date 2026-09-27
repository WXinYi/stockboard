#!/usr/bin/env python3
"""清理 auction.db 中指定日期的幽灵行(假日回放数据)。

背景(2026-09-27): 09-25(中秋) auction.yml / 涨停池回补 无交易日守卫, KPL His 对假日
静默回放上一交易日数据 → limit_pool/bid_pool/board_bid/mood_daily/strike_pool 写入
09-25 幽灵行并随热层上传。防复发 = 两处交易日守卫 + backfill_emotion.trading_days()
日历过滤(同批修复); 本工具用于清理存量幽灵行, 也可复用于未来同类事故。

用法(在 jiarenmens/ 目录):
  python scripts/purge_auction_date.py --date 2026-09-25             # 干跑, 只打印
  python scripts/purge_auction_date.py --date 2026-09-25 --apply     # 实际删除
  python scripts/purge_auction_date.py --date 2026-09-25 --apply --db /tmp/test.db
"""
import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "auction.db"

# 全部以 date 为主键/含 date 列的表(新增表时在此登记)
DATE_TABLES = [
    "mood_daily", "board_bid", "bid_pool", "limit_pool", "strike_pool",
    "broken_pool", "index_daily", "market_breadth", "llm_review",
]


def main():
    ap = argparse.ArgumentParser(description="清理 auction.db 指定日期的幽灵行")
    ap.add_argument("--date", required=True, help="要清理的日期 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true", help="实际删除(默认 dry-run 只打印)")
    ap.add_argument("--db", default=str(DB), help="库路径(默认 Release 恢复后的 data/auction.db)")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    total = 0
    for t in DATE_TABLES:
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {t} WHERE date=?", (a.date,)).fetchone()[0]
        except sqlite3.OperationalError as e:
            print(f"  - {t}: 跳过({e})")
            continue
        if n:
            print(f"  {t}: {n} 行{' → 删除' if a.apply else ' (dry-run, 未删)'}")
            if a.apply:
                con.execute(f"DELETE FROM {t} WHERE date=?", (a.date,))
            total += n
        else:
            print(f"  {t}: 0 行")
    if a.apply:
        con.commit()
        print(f"✅ 已清理 {a.date} 共 {total} 行")
    else:
        print(f"(dry-run) 命中 {total} 行; 确认无误后加 --apply 执行删除")


if __name__ == "__main__":
    main()
