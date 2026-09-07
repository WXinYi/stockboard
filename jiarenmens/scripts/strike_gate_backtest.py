#!/usr/bin/env python3
"""出击闸门/阈值回测(v1 · 样本盘点版)

目的: 评估「可买(出击)75 / 待确认(备选)55 评分线」与「矩阵龙头豁免」是否该调,
需要历史 strike_pool 存档(每日 9:26 当时口径)做次日收益对照。

当前口径(v1, 先回答"有多少样本"):
  对每条历史存档日 D 的每只 pick:
    - 状态 = 存档原词(可做*/可做(矩阵谨慎)/观察…)
    - 次日竞价涨幅 = bid_pool(D_next) 的 change_pct
    - 次日是否涨停 = 该股 ∈ limit_pool(D_next)
  按 状态分组输出: 样本数 / 次日竞价均值 / 次日涨停率。

结论解读:
  - 若「可做」组样本 < 30: 说明存档期太短, 不具备调阈值统计效力(本脚本如实报样本不足);
  - 样本足够后, 再加次日收盘收益(需接日K)与分组对比, 那时才谈改 75/55 与龙头豁免。

用法(在 jiarenmens/ 目录):
  venv/bin/python scripts/strike_gate_backtest.py            # 全历史
  venv/bin/python scripts/strike_gate_backtest.py --days 10  # 最近 N 个存档日
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "auction.db"


def main():
    ap = argparse.ArgumentParser(description="出击闸门回测 · 样本盘点版")
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 个存档日(0=全部)")
    args = ap.parse_args()

    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT date, picks FROM strike_pool ORDER BY date").fetchall()
        if args.days:
            rows = rows[-args.days:]
        if not rows:
            print("strike_pool 无存档 → 先跑 auction_scan 建档"); return
        # 每个存档日的下一交易日
        trade_days = [r[0] for r in c.execute("SELECT DISTINCT date FROM limit_pool ORDER BY date")]
        nxt = {}
        for i, d in enumerate(trade_days):
            later = [x for x in trade_days if x > d]
            if later:
                nxt[d] = later[0]
        stats = {}
        for r in rows:
            d = r["date"]
            dn = nxt.get(d)
            try:
                picks = json.loads(r["picks"])
            except Exception:
                continue
            for p in picks:
                st = (p.get("status") or "").strip()
                grp = ("可做(出击)" if st.startswith("可做") and "矩阵谨慎" not in st
                       else "可做(矩阵谨慎)" if "矩阵谨慎" in st else "观察")
                s = stats.setdefault(grp, {"n": 0, "bid": [], "zt": 0})
                s["n"] += 1
                if dn:
                    b = c.execute("SELECT MAX(change_pct) FROM bid_pool WHERE date=? AND code=?",
                                  (dn, p.get("code"))).fetchone()[0]
                    if b is not None:
                        s["bid"].append(b)
                    in_limit = c.execute("SELECT 1 FROM limit_pool WHERE date=? AND code=? LIMIT 1",
                                         (dn, p.get("code"))).fetchone()
                    if in_limit:
                        s["zt"] += 1
        print(f"存档日 {rows[0]['date']} → {rows[-1]['date']} 共 {len(rows)} 天")
        print(f"{'分组':<12}{'样本':>6}{'次日竞价均值%':>14}{'次日涨停率':>12}")
        for k, s in sorted(stats.items()):
            bid = f"{sum(s['bid'])/len(s['bid']):+.2f}" if s["bid"] else "—"
            zt = f"{s['zt']/s['n']*100:.0f}%" if s["n"] else "—"
            print(f"{k:<12}{s['n']:>6}{bid:>14}{zt:>12}")
        go = stats.get("可做(出击)", {}).get("n", 0)
        care = stats.get("可做(矩阵谨慎)", {}).get("n", 0)
        if go < 30:
            print(f"\n⚠️ 「可做(出击)」样本 {go} < 30, 暂不具备调阈值(75/55)的统计效力;")
            print("   建议: 让 strike_pool 存档继续积累, 或用 --days 回看已有窗口; 龙头豁免同理需先有样本。")
        else:
            print(f"\n样本足够(可做 {go} / 谨慎 {care}), 可进入下一步: 接次日日K收益做分组对比。")


if __name__ == "__main__":
    main()
