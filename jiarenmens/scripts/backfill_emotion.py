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
from datetime import date, datetime, timedelta, timezone
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


def backfill_breadth(spider: KPLSpider, strict: bool = False,
                     retries: int = 30, retry_wait: int = 180) -> int:
    """近 250 交易日市场宽度(一次请求): [涨停,跌停,自然涨停,曾跌停,破板率,炸板数,日期]

    2026-09-09 加固: His 当天数据收盘后若干分钟才定稿(15:05 实测 errcode=1020),
    无数据时长重试(默认 30×180s=90min 等定稿); strict 时最终仍 0 行 → 非零退出。
    单请求含 250 天, 次日成功即自动覆盖漏掉的那天(自愈)。"""
    rows = []
    for attempt in range(retries + 1):
        try:
            data = spider._get({"a": "RiseFallAnalysis", "st": 250, "apiv": "w43",
                                "c": "HisHomeDingPan", "PhoneOSNew": 1, "Index": 0}, KPL_HOST_HIS)
            rows = [r for r in (data.get("info") or []) if isinstance(r, list) and len(r) >= 7]
        except Exception as e:
            print(f"  ⚠️ breadth His 请求失败: {e}")
            rows = []
        # 当日实时补行(用户口径 09-09: 今天用实时, 历史用 His): His 只服务已完成交易日,
        # 收盘后 15:05 常返回 1020; 实时 rise_fall_rt 已含当日宽度行, 直接并入。
        try:
            today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            if today not in {str(r[6]) for r in rows}:
                rt = spider.rise_fall_rt()
                rt_rows = [r for r in (rt.get("info") or []) if isinstance(r, list) and len(r) >= 7]
                if rt_rows:
                    rows = rows + rt_rows
                    print(f"  ℹ️ breadth 并入实时当日行 {rt_rows[0][6]}")
        except Exception as e:
            print(f"  ⚠️ breadth 实时请求失败: {e}")
        if rows:
            break
        if attempt < retries:
            print(f"  ⏳ breadth 无数据(His/实时均空), {retry_wait}s 后重试 {attempt + 1}/{retries}")
            time.sleep(retry_wait)
    if not rows:
        msg = "❌ market_breadth 0 行(His 未定稿或接口异常)"
        if strict:
            raise SystemExit(msg + ", strict 中止")
        print("  " + msg + ", 跳过本次")
        return 0
    n = 0
    with sqlite3.connect(DB) as conn:
        init_tables(conn)
        for r in rows:
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


def _insert_pool(spider: KPLSpider, conn: sqlite3.Connection, d: str, pid: int, use_rt: bool) -> int:
    """抓取并落库某日某板位桶。use_rt=True 走当日实时(HomeDingPan), 否则走 His(历史)。
    返回写入行数; 异常返回 -1(用于区分"接口未定稿/非交易日"与"成功但 0 条")。"""
    try:
        data = spider.zt_pool_rt(pid_type=pid, st=500) if use_rt else spider.zt_pool(d, pid_type=pid, st=500)
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
        return len(rows)
    except Exception as e:
        print(f"  ⚠️ {d} P{pid} {'实时' if use_rt else 'His'} 失败: {e}")
        time.sleep(0.5)
        return -1


def backfill_pool(spider: KPLSpider, start: str, end: str,
                 strict: bool = False, retries: int = 30, retry_wait: int = 180) -> int:
    """涨停池全字段回补: r[0]代码 r[1]名称 r[4]涨停时间 r[5]原因 r[6]封单 r[7]最大封单
       r[8]主力净额 r[11]成交额 r[12]板块 r[13]实际流通; PidType=板高

    2026-09-09 加固(09-07/08 静默漏补事故):
      - 仅对最后一日(end)做"无数据重试"——His 当天数据常在收盘后若干分钟才定稿;
      - strict: 若 end 为工作日且 0 条、而窗口内其它日有数据 → 非零退出(区分节假日全 0)。
    """
    total = 0
    got_by_day: dict = {}
    with sqlite3.connect(DB) as conn:
        for d in trading_days(start, end):
            is_end = (d == end)
            got = 0
            his_err = False
            attempts = retries if is_end else 0
            for attempt in range(attempts + 1):
                got = 0
                # 口径(2026-09-09 用户拍板): 当日优先实时接口(收盘后 His 未定稿, 实时已含全天);
                # 历史一律 His。当日实时为空时再用 His 探测(区分节假日 errcode=1020 与真异常)。
                order = [True, False] if is_end else [False]
                for use_rt in order:
                    for pid in PIDS:
                        n = _insert_pool(spider, conn, d, pid, use_rt)
                        if n < 0:
                            his_err = True
                        else:
                            got += n
                        time.sleep(0.15)
                    if got > 0:
                        break
                conn.commit()
                if got > 0 or (is_end and his_err):
                    break
                if attempt < attempts:
                    print(f"  ⏳ {d} 无数据(实时/His 均空), {retry_wait}s 后重试 {attempt + 1}/{attempts}")
                    time.sleep(retry_wait)
            got_by_day[d] = got
            print(f"  ✓ {d}: {got} 条涨停(板位1-5)")
            total += got
    print(f"  💾 limit_pool 全字段落库 {total} 条")
    # strict: 末尾日为工作日、0 条、且窗口内其它日有数据、且 His 未报"未定稿/非交易日" → 判为漏补
    if (strict and date.fromisoformat(end).weekday() < 5 and got_by_day.get(end, 0) == 0
            and not his_err and any(v > 0 for v in got_by_day.values())):
        raise SystemExit(f"❌ {end} 涨停池 0 条(窗口内其它日有数据, 判定为漏补), strict 中止")
    return total


def main():
    ap = argparse.ArgumentParser(description="情绪/涨停池历史回补")
    ap.add_argument("--breadth", action="store_true", help="回补 250 天市场宽度")
    ap.add_argument("--pool", nargs=2, metavar=("START", "END"), help="回补涨停池(全字段)")
    ap.add_argument("--strict", action="store_true", help="末尾交易日 0 条且窗口内其它日有数据 → 非零退出")
    ap.add_argument("--retries", type=int, default=30, help="末尾日/宽度无数据重试次数(默认30)")
    ap.add_argument("--retry-wait", type=int, default=180, help="重试间隔秒(默认180=3min)")
    args = ap.parse_args()
    if not args.breadth and not args.pool:
        ap.error("至少指定 --breadth 或 --pool")
    spider = KPLSpider()
    if args.breadth:
        backfill_breadth(spider, strict=args.strict, retries=args.retries, retry_wait=args.retry_wait)
    if args.pool:
        backfill_pool(spider, args.pool[0], args.pool[1], strict=args.strict,
                      retries=args.retries, retry_wait=args.retry_wait)


if __name__ == "__main__":
    main()
