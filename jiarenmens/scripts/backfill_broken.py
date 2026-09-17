#!/usr/bin/env python3
"""炸板池归档: 选股宝 limit_up_broken → auction.db broken_pool(弱转强"昨日分歧"精确数据源)

用法(在 jiarenmens/ 目录):
  python scripts/backfill_broken.py                    # 收盘班: 最近 7 个交易日滚动归档(幂等自愈)
  python scripts/backfill_broken.py --days 30          # 加长滚动窗口(补历史盲区)
  python scripts/backfill_broken.py --from 2026-09-07  # 区间回填(按 limit_pool 交易日)

2026-09-17 加固(09-14~09-16 连缺三天事故, 同 09-09 backfill_emotion --pool 的静默漏补同族):
  原实现只取 limit_pool 最大值这"一天" + 把 0 行当成功写入。
  - 选股宝当日炸板池收盘后(实测 15:19)未定稿时返回空 data → 脚本 DELETE 后写 0 行, 退出码 0;
  - 次日 MAX(date) 已翻篇到新的一天, 永不回头 → 缺口永久留存;
  - crawl.yml 的守卫 grep "⚠️|失败|Traceback" 也看不见这条路径(只打印 "0 只炸板" 和 ✅)。
  现改为 7 天滚动窗口(幂等 INSERT OR REPLACE, 前一日次日自动补上) + 末尾日无数据重试
  + 空返回不删旧数据(重跑不能清空已归档好的一天) + 落后超一个交易日时打 ⚠️ 让守卫可见。
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
WINDOW_DAYS = 7


def _trading_days(conn, dfrom: str | None, dto: str, window: int) -> list[str]:
    """窗口交易日 = limit_pool 里已有的日期(与周期引擎同一份交易日历, 天然排除周末/节假日)"""
    if dfrom:
        rows = conn.execute("SELECT DISTINCT date FROM limit_pool WHERE date>=? AND date<=? "
                            "ORDER BY date", (dfrom, dto)).fetchall()
    else:
        rows = conn.execute("SELECT DISTINCT date FROM limit_pool WHERE date<=? "
                            "ORDER BY date DESC LIMIT ?", (dto, window)).fetchall()
        rows = list(reversed(rows))
    return [r[0] for r in rows]


def _archive_day(conn, d: str) -> tuple[int, Exception | None]:
    """抓取并归档单日 → (写入行数, 异常); 空返回不落库也不删旧数据"""
    try:
        rows = fetch_broken_pool(d)
    except Exception as e:
        return 0, e
    if not rows:
        return 0, None
    conn.execute("DELETE FROM broken_pool WHERE date=?", (d,))
    conn.executemany("INSERT OR REPLACE INTO broken_pool VALUES (?,?,?,?,?,?,?)",
                     [(d, r["code"], r["name"], r["break_times"], r["change_pct"],
                       r["turnover"], r["height"]) for r in rows])
    conn.commit()
    return len(rows), None


def main():
    ap = argparse.ArgumentParser(description="炸板池归档(选股宝源)")
    ap.add_argument("--from", dest="dfrom", help="起始日 YYYY-MM-DD(含), 配合 --to 区间回填")
    ap.add_argument("--to", dest="dto", help="结束日 YYYY-MM-DD(含), 默认=今天")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS,
                    help=f"滚动窗口交易日数(默认 {WINDOW_DAYS}; 给了 --from 则改按区间)")
    ap.add_argument("--retries", type=int, default=3, help="末尾交易日无数据重试次数(默认3)")
    ap.add_argument("--retry-wait", type=int, default=60, help="重试间隔秒(默认60)")
    ap.add_argument("--strict", action="store_true",
                    help="末尾交易日 0 条且窗口内其它日有数据 → 非零退出")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS broken_pool (date TEXT, code TEXT, name TEXT, "
                 "break_times INTEGER, change_pct REAL, turnover REAL, height INTEGER, "
                 "PRIMARY KEY(date, code))")
    to_d = args.dto or datetime.now().strftime("%Y-%m-%d")
    days = _trading_days(conn, args.dfrom, to_d, args.days)
    if not days:
        print("⚠️ 窗口内无交易日(limit_pool 无该区间数据), 跳过炸板池归档")
        conn.close()
        return

    got_by_day: dict[str, int] = {}
    failed: list[str] = []
    for i, d in enumerate(days):
        is_last = i == len(days) - 1
        got, err = _archive_day(conn, d)
        # 末尾日(今天)收盘后常未定稿: 重试等它落定; 历史日不重试(次日滚动窗口会再抓一次)
        if is_last and got == 0:
            for attempt in range(args.retries):
                print(f"  ⏳ {d} 炸板池无数据(源未定稿), {args.retry_wait}s 后重试 "
                      f"{attempt + 1}/{args.retries}")
                time.sleep(args.retry_wait)
                got, err = _archive_day(conn, d)
                if got:
                    break
        got_by_day[d] = got
        if err:
            failed.append(d)
            print(f"⚠️ {d} 拉取失败, 跳过: {err}")
        elif got:
            print(f"  ✓ {d}: {got} 只炸板")
        else:
            print(f"  · {d}: 0 只炸板(源未定稿, 保留已有归档)")
        if not is_last:
            time.sleep(0.4)

    total = sum(got_by_day.values())
    print(f"✅ broken_pool 本轮写入 {total} 条 / {len(days)} 个交易日({days[0]} ~ {days[-1]})")
    if failed:
        print(f"⚠️ {len(failed)} 天拉取失败未补: {', '.join(failed)} (重跑同命令可补齐)")

    # 自愈校验: 滚动窗口下最新归档至少应覆盖"窗口倒数第二个交易日"(末尾日次日补上)。
    # 落后更多说明自愈失效(09-14~09-16 连缺三天即此形态, 修前无窗口故无人察觉)。
    latest = conn.execute("SELECT MAX(date) FROM broken_pool").fetchone()[0]
    conn.close()
    if len(days) >= 2 and (latest is None or latest < days[-2]):
        print(f"⚠️ broken_pool 最新日期 {latest} 落后于 {days[-2]}(窗口内前一个交易日), "
              f"自愈未生效 — 弱转强「昨日炸板」会少数据")

    if failed and len(failed) == len(days):
        sys.exit(1)
    if args.strict and got_by_day.get(days[-1], 0) == 0 and any(v > 0 for v in got_by_day.values()):
        sys.exit(f"❌ {days[-1]} 炸板池 0 条(窗口内其它日有数据, 判定为漏补), strict 中止")


if __name__ == "__main__":
    main()
