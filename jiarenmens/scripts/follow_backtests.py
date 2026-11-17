#!/usr/bin/env python3
"""跟随类回测 ×2（2026-11-07 立项，预注册规格——先定死再算，无参数扫描）

共用规则（两实验一致）:
- 时点纪律: 只用 crawl_date==trade_date 的行(当日盘中可见, 收盘时点可得);
  爬虫次日才出现的行不进信号(真实跟随者当时看不见), 如实计数丢弃量。
- 窗口: 决策日 D ∈ [2026-09-10, 2026-11-05] (D+1/D+2 须在数据内)
- 动作: D+1 开盘买信号票 → A=D+1 收盘卖 / B=D+2 开盘卖, 两口径都报
- 不可成交: D+1 开盘 ≥ 涨停价(5/10/20/30% 按代码板块) → 单列不进主样本
- 对照组同规则; 分半验证(按时间); 负结果照发; 不挑口径

实验 4 狙击手跟随:
- 狙击手(时点化, 回看 20 交易日, 只用 ≤D 数据):
  a) 有买入的活跃天 ≤8 (低频)  b) 买入笔数≥2 且 "9成以上"占比≥80% (满仓)
  c) 已完成往返(同票先买后卖)中 跨度≥5交易日 的占比≥50% (耐心)
- 信号: 狙击手 D 日满仓新买入
- 对照: 一日一全仓换票选手(见实验2定义) 同日满仓新买入

实验 2 换票流派共识票:
- 换票选手(时点化, 回看 10 交易日): 活跃≥5天; 买入覆盖 distinct 股票数 ≥0.8×活跃天;
  买入满仓占比≥0.7; 窗口内触及的票 买卖跨度均 ≤3 交易日
- 信号: D 日 ≥2 个换票选手买入同一票; 主口径 N≥2, 副口径 N≥3(都报)
- 对照: 同日恰 1 个换票选手买入的票(独狼)

用法: python scripts/follow_backtests.py
"""
import sys
import sqlite3
import time
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from divergence_anchor_backtest import kline, limit_ratio   # 共用机制, 防第二份口径

DB = ROOT / "data" / "crawl_data.db"
FULL = "9成以上"
D0, D1_END = "2026-09-10", "2026-11-05"


def tindex(days):
    return {d: i for i, d in enumerate(days)}


def span_days(ti, a, b):
    return ti[b] - ti[a]


def main():
    t0 = time.time()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        """SELECT zh_id, trade_date, crawl_date, direction, stock_code, stock_name,
                  position_ratio FROM trades
           WHERE trade_date >= '2026-09-08' AND direction IN ('买入','卖出')""")]
    conn.close()
    # 交易日用 crawl 窗口内的 trade_date(全量历史含 2022 老档, 不在跟随窗口)
    days = sorted({r["trade_date"] for r in rows})
    ti = tindex(days)
    # 时点可见 = 当日已爬出
    visible = [r for r in rows if r["crawl_date"] == r["trade_date"]]
    late = len(rows) - len(visible)
    # 按日索引: buys[D][zh] = [(code,name,ratio)], sells[D][zh] = [code]
    buys = defaultdict(lambda: defaultdict(list))
    sells = defaultdict(lambda: defaultdict(list))
    for r in visible:
        if r["direction"] == "买入":
            buys[r["trade_date"]][r["zh_id"]].append(
                (r["stock_code"], r["stock_name"], r["position_ratio"] or ""))
        else:
            sells[r["trade_date"]][r["zh_id"]].append(r["stock_code"])
    print(f"载入 {len(rows)} 行(当日可见 {len(visible)}, 次日后爬丢弃 {late}) "
          f"交易日 {days[0]}→{days[-1]} | {time.time()-t0:.1f}s")

    decision = [d for d in days if D0 <= d <= D1_END]
    print(f"决策日 {len(decision)} 天: {decision[0]}→{decision[-1]}")

    # 每人时序索引(可见行): 买入/卖出按日排序, 窗口用 bisect 切
    from bisect import bisect_right
    zh_buys = defaultdict(list)   # zh -> [(d, code, ratio)]
    zh_sells = defaultdict(list)  # zh -> [(d, code)]
    for r in visible:
        if r["direction"] == "买入":
            zh_buys[r["zh_id"]].append((r["trade_date"], r["stock_code"], r["position_ratio"] or ""))
        else:
            zh_sells[r["zh_id"]].append((r["trade_date"], r["stock_code"]))
    for lst in zh_buys.values():
        lst.sort()
    for lst in zh_sells.values():
        lst.sort()

    def player_buys_until(zh, D, w):
        lst = zh_buys.get(zh) or []
        hi = bisect_right([x[0] for x in lst], D) if lst else 0
        lo_d = days[max(0, ti[D] - w + 1)]
        return [x for x in lst[:hi] if x[0] >= lo_d]

    def roundtrip_spans(zh, D, w):
        pb = player_buys_until(zh, D, w)
        if not pb:
            return []
        lo_d = days[max(0, ti[D] - w + 1)]
        bs = defaultdict(list)
        for d, c, _r in pb:
            bs[c].append(d)
        spans = []
        for c, bd in bs.items():
            sd = [d for d, cc in zh_sells.get(zh, ()) if cc == c and lo_d <= d <= D]
            if sd:
                spans.append(span_days(ti, min(bd), max(sd)))
        return spans

    def is_sniper(zh, D, w=20):
        pb = player_buys_until(zh, D, w)
        if len(pb) < 2:
            return False
        active = len({d for d, _c, _r in pb})
        if active > 8:
            return False
        if sum(1 for _d, _c, r in pb if r == FULL) / len(pb) < 0.8:
            return False
        spans = roundtrip_spans(zh, D, w)
        if not spans or sum(1 for s in spans if s >= 5) / len(spans) < 0.5:
            return False
        return True

    def is_rotator(zh, D, w=10):
        pb = player_buys_until(zh, D, w)
        if not pb:
            return False
        active = len({d for d, _c, _r in pb})
        if active < 5:
            return False
        if len({c for _d, c, _r in pb}) < 0.8 * active:
            return False
        if sum(1 for _d, _c, r in pb if r == FULL) / len(pb) < 0.7:
            return False
        spans = roundtrip_spans(zh, D, w)
        if spans and max(spans) > 3:
            return False
        return True

    print("== 实验准备: 逐日角色判定(仅当日有满仓买入者) ==")
    sniper_by_day, rotator_by_day = {}, {}
    for i, D in enumerate(decision):
        cands = {zh for zh, lst in buys[D].items()
                 if any(r == FULL for _c, _n, r in lst)}
        sn = {zh for zh in cands if is_sniper(zh, D)}
        ro = {zh for zh in cands if is_rotator(zh, D)}
        sniper_by_day[D], rotator_by_day[D] = sn, ro
        if (i + 1) % 10 == 0:
            print(f"   … {i+1}/{len(decision)} (累计狙击手日均 {sum(len(v) for v in sniper_by_day.values())/(i+1):.1f}, "
                  f"换票手日均 {sum(len(v) for v in rotator_by_day.values())/(i+1):.1f})")

    # 信号收集
    sig_sniper, sig_cons2, sig_cons3, sig_solo, sig_rot_full = [], [], [], [], []
    late_sig = 0
    for D in decision:
        # 当日新买入(满仓) — 时点可见
        day_buy = {}   # zh -> [(code,name)]
        for zh, lst in buys[D].items():
            lst2 = [(c, n) for c, n, r in lst if r == FULL]
            if lst2:
                day_buy[zh] = lst2
        # 次日后爬才出现的满仓买入(诚实计数)
        for dd in days[ti[D]+1: ti[D]+3]:
            for zh, lst in buys.get(dd, {}).items():
                late_sig += sum(1 for c, n, r in lst
                                if r == FULL and not any(c == c2 for c2, _n in day_buy.get(zh, ())))
        sn = sniper_by_day[D]; ro = rotator_by_day[D]
        cnt = Counter(c for zh, lst in day_buy.items() if zh in ro for c, _n in lst)
        for zh, lst in day_buy.items():
            for c, n in lst:
                if zh in sn:
                    sig_sniper.append({"D": D, "code": c, "name": n, "zh": zh})
                if zh in ro:
                    rec = {"D": D, "code": c, "name": n, "zh": zh, "n": cnt[c]}
                    if cnt[c] >= 3:
                        sig_cons3.append(rec)
                    if cnt[c] >= 2:
                        sig_cons2.append(rec)
                    else:
                        sig_solo.append(rec)
    print(f"信号量: 狙击手满仓买 {len(sig_sniper)} | 共识N≥2 {len(sig_cons2)} "
          f"(N≥3 {len(sig_cons3)}) | 独狼 {len(sig_solo)} | 次日后爬不可见丢弃 {late_sig}")

    def evaluate(sigs, tag):
        fillA, fillB, unfill, nok = [], [], [], []
        for s in sigs:
            i = ti[s["D"]]
            d1 = days[i+1] if i+1 < len(days) else None
            d2 = days[i+2] if i+2 < len(days) else None
            bars = kline(s["code"])
            if not bars or not d1 or s["D"] not in bars or d1 not in bars:
                nok.append(s); continue
            d0c = bars[s["D"]][1]
            o1, c1 = bars[d1]
            if o1 >= round(d0c * (1 + limit_ratio(s["code"], s["name"])), 2):
                unfill.append(s); continue
            fillA.append((c1 - o1) / o1 * 100)
            if d2 and d2 in bars:
                fillB.append((bars[d2][0] - o1) / o1 * 100)
        def st(v, t2):
            if not v:
                print(f"   {t2}: 无样本"); return
            v2 = sorted(v)
            print(f"   {t2}: 均值{sum(v)/len(v):+.2f}% 中位{v2[len(v2)//2]:+.2f}% "
                  f"胜率{sum(1 for x in v if x>0)/len(v)*100:.0f}% n={len(v)}")
        print(f"— {tag}")
        st(fillA, "A(当日收卖)")
        st(fillB, "B(次日开卖)")
        if unfill:
            print(f"   [一字不可成交] {len(unfill)} 笔: " +
                  "、".join(f"{u['D'][5:]}{u['name']}" for u in unfill[:8]) +
                  ("…" if len(unfill) > 8 else ""))
        if nok:
            print(f"   [缺K线] {len(nok)} 笔")
        half = len(fillA) // 2
        if half >= 3:
            st(fillA[:half], "  A前半")
            st(fillA[half:], "  A后半")
        return fillA

    print("\n== 实验结果 ==")
    evaluate(sig_sniper, "实验4: 狙击手满仓买入·次日跟随")
    evaluate(sig_rot_full, "对照4: 换票手满仓买入·次日跟随" if sig_rot_full else "对照4(无)")
    evaluate(sig_cons2, "实验2: 换票共识 N≥2")
    evaluate(sig_cons3, "实验2副口径: N≥3")
    evaluate(sig_solo, "对照2: 独狼(仅1个换票手买)")


if __name__ == "__main__":
    main()
