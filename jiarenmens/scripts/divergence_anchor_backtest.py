#!/usr/bin/env python3
"""双引擎背离·空间锚豁免回测（2026-11-07 立项，预注册规格——先定死再算，无参数扫描）

规格（写完不再改，任何改动必须另起版本重跑全部）：
- 背离日 D = 六情绪主导 ∈ {板块情绪极强, 投机情绪极强}
            AND（周期阶段 ∈ {退潮,冰点} OR 矩阵高位格 = ban）
- 六情绪重放 = 逐日前缀重跑生产管线（分位/阈值只用 ≤D 数据；生产每日跑即此语义，
  全窗口一遍算属未来函数，禁用）
- 周期/矩阵重放 = 生产 compute_cycle，但池窗口按"≤D 的最近 40 个池日"重建
  （生产每日跑时 DB 最大日期=D，等价）；重放结果对 38 天 strike_pool.stage 存档
  逐日对拍验证（存档=当时真值）
- 豁免动作：D+1 开盘买 D 日空间锚（当日最高连板；并列取首封时间最早，KPL 首封
  时间当日盘中产生，无后视）
- 出场两口径都报：A=D+1 收盘卖 / B=D+2 开盘卖，不挑好的
- 不可成交：D+1 开盘价 ≥ 涨停价（按板块 5/10/20/30% 规则）→ 单列，不进主样本
- 对照组：全部决策日空间锚同规则 + 空仓基准=0
- 分半验证（按时间前后两半）；缺失日如实计数；负结果照发

用法: python scripts/divergence_anchor_backtest.py [--start 2026-03-01]
"""
import sys
import sqlite3
import json
import time
import urllib.request
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis import six_emotions as se          # noqa: E402
from src.analysis import emotion_cycle as ec         # noqa: E402

AUC = ROOT / "data" / "auction.db"
STRONG_SIX = ("板块情绪极强", "投机情绪极强")
# leaderBattle.MATRIX_GATE 中 tier.high == 'ban' 的九宫格格子（JS 源码逐行抄录）
MATRIX_HIGH_BAN = {("平衡", "弱"), ("弱", "平衡"), ("弱", "弱")}
STAGE_BAN = {"退潮", "冰点"}
WARMUP = 120          # 决策窗口起点前至少 120 个交易日（分位数热身，预注册）
POOL_WINDOW = 40      # 生产 compute_cycle 的池窗口（每日 40 个池日）


def load_full_pool():
    conn = sqlite3.connect(f"file:{AUC}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        """SELECT date, code, name, pid_type, zt_time, seal_amount, max_seal,
                  main_net, amount, plates FROM limit_pool ORDER BY date""")]
    conn.close()
    # 连板高度反推（与 ec.load_pool 逐行同式，窗口=全历史）
    streaks, out = {}, []
    prev_date, prev_codes = None, {}
    for r in rows:
        d = r["date"]
        if d != prev_date:
            prev_codes = {c: s for (dd, c), s in streaks.items() if dd == prev_date}
            prev_date = d
        pid = r["pid_type"] or 0
        if r["code"] in prev_codes:
            h = prev_codes[r["code"]] + 1
        else:
            h = pid if pid < 5 else 5
        r["height"] = h
        streaks[(d, r["code"])] = h
        out.append(r)
    return out


def pool_dates_upto(pool, d):
    ds = sorted({r["date"] for r in pool if r["date"] <= d})
    return ds[-POOL_WINDOW:]


def anchor_of(rows_d):
    best = None
    for r in rows_d:
        key = (-r["height"], r["zt_time"] or 9_999_999_999)
        if best is None or key < best[0]:
            best = (key, r)
    return best[1]


def limit_ratio(code, name):
    nm = (name or "").upper()
    if "ST" in nm:
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("43", "83", "87", "88", "92")):
        return 0.30
    return 0.10


_KC = {}


def kline(code):
    """东财日K(不复权): {date: (open, close)}；失败重试一次，仍失败返回 {}"""
    if code in _KC:
        return _KC[code]
    bars = {}
    for mkt in (("1" if code.startswith(("6", "9", "5")) else "0"),
                ("0" if code.startswith(("6", "9", "5")) else "1")):
        url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={mkt}.{code}"
               f"&klt=101&fqt=0&beg=20250801&end=20261231"
               f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56")
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            kl = (data.get("data") or {}).get("klines") or []
            for line in kl:
                p = line.split(",")
                bars[p[0]] = (float(p[1]), float(p[2]))   # (open, close)
            if bars:
                break
        except Exception:
            time.sleep(0.5)
    _KC[code] = bars
    return bars


def main():
    start = "2026-03-01"
    if "--start" in sys.argv:
        start = sys.argv[sys.argv.index("--start") + 1]

    print("== 0) 载入(一次) ==")
    t0 = time.time()
    dates_all, metrics_all = se._load_all()          # 生产 400 日窗口口径, 一次性缓存
    full_pool = load_full_pool()
    pool_by_date = defaultdict(list)
    for r in full_pool:
        pool_by_date[r["date"]].append(r)
    dates_all = sorted(dates_all)
    print(f"   交易日 {dates_all[0]}→{dates_all[-1]} 共{len(dates_all)}天 | "
          f"池行 {len(full_pool)} | {time.time()-t0:.1f}s")

    # 决策日 = 热身后的交易日
    idx_of = {d: i for i, d in enumerate(dates_all)}
    decision_days = [d for d in dates_all
                     if d >= start and idx_of[d] >= WARMUP and pool_by_date.get(d)]
    print(f"   决策窗口 {decision_days[0]}→{decision_days[-1]} 共{len(decision_days)}天 "
          f"(热身≥{WARMUP}交易日)")

    # == 1) 逐日重放: 六情绪(前缀分位) + 周期阶段用【当时存档】 ==
    # 诚实条款: 初版曾用 compute_cycle 重放周期, 与 strike_pool.stage 存档对拍仅 11/38 一致
    # (同代码同数据的 11-06 也分裂: 重放=发酵 vs 存档=启动) → 重放路径不可信,
    # 决策层一律取存档值(=当时系统真实输出, 零后视); 重放对拍差异作为独立发现报告。
    # 代价: 决策窗口收缩到存档覆盖期(2026-09-04 起), 之前日期记"无法判定"不硬算。
    print("== 1) 逐日重放: 六情绪前缀 + 周期取存档 ==")
    conn = sqlite3.connect(f"file:{AUC}?mode=ro")
    archived = {r[0]: r[1] for r in conn.execute(
        "SELECT date, stage FROM strike_pool WHERE stage IS NOT NULL ORDER BY date")}
    conn.close()
    recs = []
    orig_load = se._load_all
    skipped_pre = 0
    for D in decision_days:
        if D not in archived:
            skipped_pre += 1
            continue
        dd = [x for x in dates_all if x <= D]
        mm = {x: metrics_all[x] for x in dd}
        se._load_all = lambda: (dd, mm)               # 前缀重放(分位只见 ≤D)
        out, _ = se.compute_all()
        se._load_all = orig_load
        dom = out[D]["dominant"]
        stage = archived[D]
        # 矩阵: 存档只有阶段; 阶段∈{退潮,冰点} 时矩阵无从取 → 按阶段禁买口径判定,
        # 其余阶段取不到当日矩阵九宫格 → 如实记 matrix=None, 仅在阶段禁买时记背离。
        # (周期引擎当日矩阵随 pool/breadth 重放已被证伪, 不用)
        ban = stage in STAGE_BAN
        div = dom in STRONG_SIX and ban
        a = anchor_of(pool_by_date[D])
        recs.append({"date": D, "dominant": dom, "stage": stage,
                     "matrix": None, "ban": ban,
                     "div": div, "code": a["code"], "name": a["name"],
                     "height": a["height"]})
    print(f"   决策日(有存档) {len(recs)} 天, 无存档跳过 {skipped_pre} 天")

    # == 3) 收益: D+1 开盘买 → A=当日收 / B=次日开 ==
    print("== 3) 收益计算(东财日K, 不复权) ==")
    for r in recs:
        r.setdefault("unfillable", False)
        r.setdefault("no_k", False)
        i = idx_of[r["date"]]
        r["d1"] = dates_all[i + 1] if i + 1 < len(dates_all) else None
        r["d2"] = dates_all[i + 2] if i + 2 < len(dates_all) else None
        bars = kline(r["code"])
        r["no_k"] = not bars
        o1c1 = o2 = None
        if bars and r["d1"] and r["d1"] in bars and r["date"] in bars:
            d0_close = bars[r["date"]][1]
            o1, c1 = bars[r["d1"]]
            lim = round(d0_close * (1 + limit_ratio(r["code"], r["name"])), 2)
            r["unfillable"] = o1 >= lim            # 开盘即涨停, 买不进
            o1c1 = (c1 - o1) / o1 * 100
            if r["d2"] and r["d2"] in bars:
                o2 = (bars[r["d2"]][0] - o1) / o1 * 100
        r["retA"] = o1c1
        r["retB"] = o2

    def stats(rows, tag):
        a = [r["retA"] for r in rows if r["retA"] is not None]
        b = [r["retB"] for r in rows if r["retB"] is not None]
        if not a:
            print(f"   {tag}: 无样本")
            return None
        a2 = sorted(a)
        med = a2[len(a2)//2]
        win = sum(1 for x in a if x > 0) / len(a) * 100
        btxt = f" | B(次日开卖) 均值{sum(b)/len(b):+.2f}% n={len(b)}" if b else ""
        print(f"   {tag}: A(当日收卖) 均值{sum(a)/len(a):+.2f}% 中位{med:+.2f}% "
              f"胜率{win:.0f}% n={len(a)}{btxt}")
        return sum(a) / len(a)

    divs = [r for r in recs if r["div"]]
    divs_fill = [r for r in divs if not r["unfillable"] and not r["no_k"]]
    unfill = [r for r in divs if r["unfillable"]]
    nok = [r for r in divs if r["no_k"]]
    base = [r for r in recs if not r["div"] and not r["unfillable"] and not r["no_k"]]

    print(f"\n== 4) 结果 ==")
    print(f"背离日 {len(divs)} 天 (其中一字不可成交 {len(unfill)}, 缺K线 {len(nok)})")
    for r in unfill:
        print(f"   [不可成交] {r['date']} {r['name']}({r['code']}) {r['height']}板 "
              f"{r['dominant']} {r['stage']}|{r['matrix']}")
    for r in nok:
        print(f"   [缺K线] {r['date']} {r['code']} {r['name']}")
    print("— 背离日·可成交组(核心结论):")
    stats(divs_fill, "背离日")
    half = len(divs_fill) // 2
    if half:
        stats(divs_fill[:half], "  前半")
        stats(divs_fill[half:], "  后半")
    print("— 对照组(非背离日空间锚, 同规则):")
    stats(base, "非背离日")
    if divs_fill and base:
        da = sum(r["retA"] for r in divs_fill) / len(divs_fill)
        ba = sum(r["retA"] for r in base) / len(base)
        print(f"— 背离日 vs 非背离日(空间锚同规则) A口径差: {da-ba:+.2f}pp (空仓基准=0)")

    # == 5) 明细 ==
    print("\n== 5) 背离日明细 ==")
    print(f"{'日期':<11}{'主导':<8}{'阶段':<5}{'矩阵':<7}{'锚':<10}{'板':>3} "
          f"{'A%':>7} {'B%':>7}  备注")
    for r in divs:
        fa = f"{r['retA']:+.2f}" if r["retA"] is not None else "—"
        fb = f"{r['retB']:+.2f}" if r["retB"] is not None else "—"
        note = "一字不可成交" if r["unfillable"] else ("缺K" if r["no_k"] else "")
        mtx = r["matrix"] or "—(存档无矩阵)"
        print(f"{r['date']:<11}{r['dominant']:<8}{str(r['stage']):<5}{mtx:<11}"
              f"{r['name']}({r['code']}) {r['height']:>2}板 {fa:>7} {fb:>7}  {note}")
    # 上下文: 全体决策日里空间锚开盘即涨停的比例(可买性是这套豁免的硬约束)
    dec = [r for r in recs]
    uf = sum(1 for r in dec if r["unfillable"])
    nk = sum(1 for r in dec if r["no_k"])
    print(f"\n[上下文] 全部 {len(dec)} 个决策日中, 空间锚次日开盘即涨停(买不进) {uf} 天, "
          f"缺K线 {nk} 天 —— 一字密度本身就是禁买期特征")


if __name__ == "__main__":
    main()
