#!/usr/bin/env python3
"""
回测引擎: 因子区分度分析(证据驱动的融合校准基础)

读取全部 candidates JOIN candidate_results(按 date+code, pct_bid 主口径) →
对每个因子输出 分组命中率/均值/相对基线, 供评估:
  1) 现有 v3 因子(S1-S6/评分/tier) 是否真有区分度
  2) 文章融合因子(ma60_above/ret20/macd_ok/kdj_ok/委比代理) 是否值得计入评分
  3) SCORE_THRESHOLD / S7 权重 / 对倒门限 的校准依据

用法(在 jiarenmens/ 目录下执行):
  python scripts/backtest_factors.py            # 全部样本
  python scripts/backtest_factors.py --min 8    # 只看样本量≥8 的分组(避免单日噪声误读)

⚠️ 当前样本仅 27(08-12+08-13 两日), 结论只是"方法论演示", 攒够 2-4 周才有统计意义。
"""
import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.spiders.auction_spider import AuctionStore  # noqa: E402


def load():
    store = AuctionStore()
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT c.*, r.pct_bid, r.close_px, r.pct_day, r.pct_open_day, r.pct_e31
        FROM candidates c JOIN candidate_results r USING(date, code)
        ORDER BY c.date, c.tier, c.rank_in_day
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 分组定义: (因子名, 分割函数[row->分组标签])
GROUPS = []


def group(name, fn):
    GROUPS.append((name, fn))


# --- 现有 v3 因子 ---
group("tier", lambda r: r.get("tier"))
group("score分档", lambda r: "≥10" if (r.get("score") or 0) >= 10
      else "8-9" if (r.get("score") or 0) >= 8 else "<8")
for i in range(1, 7):
    group(f"s{i}", lambda r, _i=i: ">0" if (r.get(f"s{_i}") or 0) > 0 else "=0")


def _band(v, edges, labels):
    """按分档标签切分; v 为 None → '无数据'"""
    if v is None:
        return "无数据"
    for lo, hi, lab in zip(edges[:-1], edges[1:], labels):
        if lo <= v < hi:
            return lab
    return labels[-1]


group("bid_pct分档", lambda r: _band(r.get("bid_pct"), (0, 1, 3, 7, 999), ("<1%", "1-3%", "3-6%", "≥7%")))
group("vol_ratio分档", lambda r: _band(r.get("vol_ratio"), (0, 2, 8, 15, 999),
                                     ("<2", "2-8", "8-15", ">15对倒")))
# --- 文章融合因子 ---
group("s7技术分", lambda r: ">0" if (r.get("s7") or 0) > 0
      else "=0" if (r.get("s7") or 0) == 0 else "<0")
group("ma60_above", lambda r: "站上" if r.get("ma60_above") == 1
      else "跌破" if r.get("ma60_above") == 0 else "无数据")
group("ret20分档", lambda r: _band(r.get("ret20"), (-999, 0.10, 0.30, 999),
                                 ("<10%", "10-30%", ">30%")))
group("macd_ok", lambda r: "向上" if r.get("macd_ok") == 1
      else "向下" if r.get("macd_ok") == 0 else "无数据")
group("kdj_ok", lambda r: "向上" if r.get("kdj_ok") == 1
      else "向下" if r.get("kdj_ok") == 0 else "无数据")
group("委比代理", lambda r: _band(r.get("bid_buy_ratio"), (0, 0.50, 0.70, 1.01),
                                ("<50%", "50-70%", "≥70%")))
group("竞价量", lambda r: "≥500手" if (r.get("bid_vol_total") or 0) >= 500
      else "<500手" if (r.get("bid_vol_total") or 0) is not None else "无数据")
# --- 三力(2026-08-14 融合 v3.1) ---
group("s8撮合", lambda r: "3档" if (r.get("s8") or 0) >= 3
      else "2档" if (r.get("s8") or 0) == 2
      else "1档" if (r.get("s8") or 0) == 1 else "0档")
group("s9委买", lambda r: "3档" if (r.get("s9") or 0) >= 3
      else "2档" if (r.get("s9") or 0) == 2
      else "1档" if (r.get("s9") or 0) == 1 else "0档")
group("unfilled_buy分档", lambda r: _band(r.get("unfilled_buy"), (0, 0.1e7, 0.5e7, 2e7, 1e18),
                                         ("<1kw", "1-5kw", "5-20kw", ">20kw")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=5, help="分组最小样本量(默认5)")
    args = ap.parse_args()
    data = load()
    if not data:
        print("无样本(candidates × candidate_results 为空), 先跑 scan + --label 攒数据")
        return 0
    win = sum(1 for r in data if r["pct_bid"] > 0)
    avg = sum(r["pct_bid"] for r in data) / len(data)
    # 当天涨跌幅口径(选股"选对没"直觉口径): 收盘>昨收 算赢
    have_day = [r for r in data if r.get("pct_day") is not None]
    win_day = sum(1 for r in have_day if r["pct_day"] > 0)
    avg_day = sum(r["pct_day"] for r in have_day) / len(have_day) if have_day else 0
    # 开盘买入口径(收盘vs开盘) 与 E层09:31确认口径(收盘vs 09:31价)
    have_od = [r for r in data if r.get("pct_open_day") is not None]
    have_e31 = [r for r in data if r.get("pct_e31") is not None]

    def _stat(sub):
        w = sum(1 for r in sub if r["v"] > 0)
        a = sum(r["v"] for r in sub) / len(sub)
        return w, a

    print(f"样本 {len(data)} 只")
    print(f"  竞价口径(收盘vs竞价): 胜率 {win/len(data):.0%}, 平均 {avg*100:+.2f}%   ← 09:25竞价买入至收盘")
    if have_day:
        w, a = _stat([{"v": r["pct_day"]} for r in have_day])
        print(f"  昨收口径(收盘vs昨收): 胜率 {w/len(have_day):.0%} ({w}/{len(have_day)}), 平均 {a*100:+.2f}%   ← 前一天埋伏")
    if have_od:
        w, a = _stat([{"v": r["pct_open_day"]} for r in have_od])
        print(f"  开盘口径(收盘vs开盘): 胜率 {w/len(have_od):.0%} ({w}/{len(have_od)}), 平均 {a*100:+.2f}%   ← 开盘价买入")
    if have_e31:
        w, a = _stat([{"v": r["pct_e31"]} for r in have_e31])
        print(f"  E层口径(收盘vs 09:31): 胜率 {w/len(have_e31):.0%} ({w}/{len(have_e31)}), 平均 {a*100:+.2f}%   ← 09:31确认走强入场")
    print(f"样本不足({args.min})分组标注 ⚠️, 不解读\n")

    for name, fn in GROUPS:
        buckets = defaultdict(list)
        for r in data:
            buckets[fn(r)].append(r["pct_bid"])
        print(f"── {name} ──")
        for tag, vals in sorted(buckets.items()):
            n = len(vals)
            w = sum(1 for v in vals if v > 0)
            a = sum(vals) / n
            flag = "" if n >= args.min else " ⚠️样本不足"
            diff = f"{(a-avg)*100:+.2f}pp" if n >= args.min else ""
            print(f"  {tag:<10} n={n:<4} 胜率{w/n:.0%}  均值{a*100:+.2f}%  相对基线{diff}{flag}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
