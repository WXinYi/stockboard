#!/usr/bin/env python3
"""昨日连板 × 今日9:25竞价实际换手 Top N (一条命令出结果)

用法(在 jiarenmens/ 目录):
  venv/bin/python scripts/lianban_bid_hs.py                  # 默认: 上一交易日 × 最新交易日, Top5
  venv/bin/python scripts/lianban_bid_hs.py --top 10
  venv/bin/python scripts/lianban_bid_hs.py --prev 2026-09-02 --today 2026-09-03

口径(2026-09-03 双源验证, 详见 memory/stockboard-data-env-gotchas):
  连板名单 = KPL 昨日涨停池 PidType 2~5 组(昨日收盘连板身位)
  竞价换手 = KPL bid_pool.turnover_ratio(今晨 09:26 扫描落库, =竞价成交额/流通市值)
             该字段部分行为 0(接口缺陷) → 用腾讯分时 0930 首行(=09:25 集合竞价成交打印,
             金额与 KPL 竞价成交额分毫不差)÷流通市值 补算
  依赖: 先 git pull 拿最新 auction.db(CI 每日提交); 未 pull 也能跑, 全量走腾讯补算
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("NO_PROXY", "longhuvip.com,.longhuvip.com,gtimg.cn,.gtimg.cn")
os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.spiders.auction_spider import KPLSpider  # noqa: E402

DB = ROOT / "data" / "auction.db"


def latest_trading_day(spider):
    b = (spider.rise_fall_rt().get("info") or [[None] * 7])[0]
    d = str(b[6]) if len(b) > 6 and b[6] else datetime.now().strftime("%Y-%m-%d")
    return d


def prev_trading_day(spider, today):
    """向前找最近有涨停池的交易日(周末跳过, 节假日靠池空探测)"""
    d = date.fromisoformat(today)
    for _ in range(12):
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        cand = d.isoformat()
        info = spider.zt_pool(cand, pid_type=1, st=5).get("info") or []
        if any(isinstance(g, list) and g for g in info):
            return cand
        time.sleep(0.1)
    raise SystemExit(f"❌ {today} 前找不到交易日涨停池")


def fetch_lianban(spider, prev):
    seen = {}
    for pid in (2, 3, 4, 5):
        for g in spider.zt_pool(prev, pid_type=pid, st=500).get("info", []):
            if not isinstance(g, list):
                continue
            for r in g:
                if isinstance(r, list) and len(r) >= 14:
                    code = str(r[0])
                    if code not in seen or pid > seen[code]["pid"]:
                        seen[code] = {"code": code, "name": str(r[1]), "pid": pid,
                                      "mv_prev": r[13] if len(r) > 13 else None}
        time.sleep(0.15)
    return seen


def tencent_auction(code):
    """腾讯分时首行 '0930 价 累计量(手) 累计额' = 09:25 集合竞价成交打印 → (价, 额)"""
    pfx = "sh" if code.startswith("6") else ("bj" if code[0] in "48" else "sz")
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={pfx}{code}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        rows = json.load(resp)["data"][pfx + code]["data"]["data"]
    first = rows[0].split()
    return float(first[1]), float(first[3]) if first[0] == "0930" else (None, None)


def main():
    ap = argparse.ArgumentParser(description="昨日连板×今日竞价实际换手 TopN")
    ap.add_argument("--prev"); ap.add_argument("--today"); ap.add_argument("--top", type=int, default=5)
    args = ap.parse_args()
    spider = KPLSpider()
    today = args.today or latest_trading_day(spider)
    prev = args.prev or prev_trading_day(spider, today)

    lianban = fetch_lianban(spider, prev)
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    bid = {}
    for r in conn.execute("SELECT * FROM bid_pool WHERE date=?", (today,)):
        cur = bid.get(r["code"])
        if not cur or (r["turnover_ratio"] or 0) > (cur["turnover_ratio"] or 0):
            bid[r["code"]] = r

    rows = []
    for c, s in lianban.items():
        b = bid.get(c)
        if b and (b["turnover_ratio"] or 0) > 0:
            rows.append({**s, "bid_pct": b["bid_pct"], "hs": b["turnover_ratio"],
                         "src": "KPL", "amt": b["main_net"], "mv": b["circ_mv"]})
            continue
        px, amt = tencent_auction(c)                      # 补算: 竞价额/流通市值
        mv = (b["circ_mv"] if b else None) or s["mv_prev"]
        if not amt or not mv:
            rows.append({**s, "bid_pct": b["bid_pct"] if b else None, "hs": None,
                         "src": "⚠️无竞价数据", "amt": amt, "mv": mv})
            continue
        rows.append({**s, "bid_pct": b["bid_pct"] if b else None,
                     "hs": amt / mv * 100, "src": "腾讯补算", "amt": amt, "mv": mv})
    rows.sort(key=lambda x: -(x["hs"] if x["hs"] is not None else -1))

    print(f"数据日 {prev}(昨) → {today}(今) · 昨日连板 {len(rows)} 只 · 换手=竞价成交额/流通市值")
    print(f"{'排名':<4}{'代码':<8}{'名称':<10}{'昨日':<6}{'竞价涨幅%':<11}{'竞价额(亿)':<12}{'实际换手%':<10}来源")
    for i, r in enumerate(rows, 1):
        pct = f"{r['bid_pct']:+.2f}" if r["bid_pct"] is not None else "—"
        amt = f"{r['amt']/1e8:.2f}" if r["amt"] else "—"
        hs = f"{r['hs']:.2f}" if r["hs"] is not None else "—"
        print(f"{i:<5}{r['code']:<9}{r['name']:<11}{str(r['pid'])+'板':<7}{pct:<12}{amt:<13}{hs:<11}{r['src']}")
    top = [f"{r['name']} {r['hs']:.2f}%" for r in rows[:args.top] if r["hs"] is not None]
    print(f"\nTOP{args.top}: {' | '.join(top)}")


if __name__ == "__main__":
    main()
