#!/usr/bin/env python3
"""LLM 竞价选股回测对账: llm_review 表 picks 逐只"竞价买入→当日收盘"收益, 与引擎 strike_pool Top5 同口径对照。

用法(在 jiarenmens/ 下):
  venv/bin/python scripts/llm_picks_review.py                # 最近 10 个存档日
  venv/bin/python scripts/llm_picks_review.py --ver v2       # 只看指定提示词版本
  venv/bin/python scripts/llm_picks_review.py --days 20
数据源: data/auction.db(llm_review + strike_pool + bid_pool); 行情走腾讯日K(前复权)。
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "auction.db"


def sym(code):
    return ("sh" if code[0] in "65" else "bj" if code[0] in "48" else "sz") + code


def ohlc(code, d):
    url = f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym(code)},day,2026-09-01,{d},12,qfq"
    for _ in range(2):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
            data = json.loads(raw)["data"][sym(code)]
            rows = data.get("qfqday") or data.get("day")
            idx = next((i for i, r in enumerate(rows) if r[0] == d), None)
            if not idx:
                return None
            return dict(prev=float(rows[idx - 1][2]), o=float(rows[idx][1]), c=float(rows[idx][2]))
        except Exception:
            time.sleep(1)
    return None


def limit_pct(code):
    return 30 if re.match(r"^(4|8|92)", code) else 20 if re.match(r"^(688|689|300|301)", code) else 10


def score(rows, d, cur):
    """rows: [{code,name}]; 返回(逐只明细, 等权均值, 可买笔数)"""
    detail, rets = [], []
    for r in rows:
        q = ohlc(r["code"], d)
        time.sleep(0.15)
        if not q:
            detail.append((r["name"], None, "无行情"))
            continue
        lim = limit_pct(r["code"])
        buyable = q["o"] < q["prev"] * (1 + lim / 100) - 0.002
        ret = (q["c"] / q["o"] - 1) * 100
        detail.append((r["name"], ret, "一字·买不到" if not buyable else f"{ret:+.1f}%"))
        if buyable:
            rets.append(ret)
    avg = sum(rets) / len(rets) if rets else None
    return detail, avg, len(rets)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ver", default="", help="提示词版本(如 v2), 空=全部")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--compare-engine", action="store_true", help="同日引擎 strike_pool Top5 对照")
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sql = "SELECT date,prompt_ver,regime,picks,degraded FROM llm_review WHERE degraded=0"
    args = []
    if a.ver:
        sql += " AND prompt_ver=?"
        args.append(a.ver)
    sql += " ORDER BY date DESC LIMIT ?"
    args.append(a.days)
    rows = conn.execute(sql, args).fetchall()
    if not rows:
        print("(llm_review 无存档)")
        return
    all_rets, all_engine = [], []
    for r in reversed(rows):
        d, ver, regime = r["date"], r["prompt_ver"], r["regime"]
        picks = json.loads(r["picks"] or "[]")
        detail, avg, n = score([{"code": k["code"], "name": k.get("name", "")} for k in picks], d, conn)
        line = f"{d} [{ver}] {regime} | {len(picks)}只 可买{n} → " + \
               ("空仓" if not picks else f"{avg:+.2f}%" if avg is not None else "无行情")
        print(line + "   " + "  ".join(f"{nm}:{v if isinstance(v, str) else format(v, '+.1f') + '%'}"
                                       for nm, v, _ in detail))
        if avg is not None:
            all_rets.append(avg)
        if a.compare_engine:
            sp = conn.execute("SELECT picks FROM strike_pool WHERE date=?", (d,)).fetchone()
            if sp:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from auction_scan import pick_strike_top
                eng_top, _wm = pick_strike_top(json.loads(sp["picks"]))
                eng_detail, eng_avg, eng_n = score([{"code": p["code"], "name": p["name"]} for p in eng_top], d, conn)
                print(f"    └ 引擎Top5对照: {eng_avg:+.2f}%({eng_n}只可买)" if eng_avg is not None
                      else "    └ 引擎Top5对照: 无行情")
                if eng_avg is not None:
                    all_engine.append(eng_avg)
    def stat(xs):
        return f"{len(xs)}日 均值 {sum(xs)/len(xs):+.2f}%" if xs else "无"
    print(f"\n== LLM({a.ver or '全部版本'}) {stat(all_rets)}")
    if a.compare_engine:
        print(f"== 引擎Top5   {stat(all_engine)}")


if __name__ == "__main__":
    main()
