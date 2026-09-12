#!/usr/bin/env python3
"""出击候选"预计成功概率"校准器。

流程:
  replay  逐日 as-of 回放最近 N 个交易日(stage_pool 无前视: 每日独立截断库),
          收集候选特征(梯队/封单/早封/主净/换手/竞价涨幅/阶段/矩阵) + 次日竞价涨幅标签
  fetch   腾讯日K 补齐今日 OHLC(计算真实收益: 今日开盘买 → 明日竞价卖, 剔除一字买不到)
  fit     逻辑回归(纯Python梯度下降) 训练集拟合 → 留出样本(最近 holdout 天)评估
          输出: 校准报告(分位桶胜率/均值收益, vs 引擎可做基线) + 权重 JSON(供引擎接入)

用法(在 jiarenmens/ 下):
  venv/bin/python scripts/calibrate_probability.py            # 全流程(带缓存, 可重复跑)
  venv/bin/python scripts/calibrate_probability.py --days 120 --holdout 20
缓存: /tmp/calib_samples.json(特征) /tmp/calib_ohlc.json(行情) 命中即跳过对应阶段。
"""
import argparse
import json
import math
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "auction.db"
sys.path.insert(0, str(ROOT))
import src.analysis.emotion_cycle as ec  # noqa: E402
import src.analysis.stage_candidates as sc  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
from auction_scan import BOARD_LIMIT, normalize_bkjjbl  # noqa: E402
from src.spiders.auction_spider import KPLSpider  # noqa: E402
SPIDER = KPLSpider()
BOARD_SCAN_LIMIT = 30

CACHE_S = Path("/tmp/calib_samples.json")
CACHE_O = Path("/tmp/calib_ohlc.json")
TRUNC = ("mood_daily", "board_bid", "bid_pool", "limit_pool", "market_breadth",
         "broken_pool", "strike_pool", "index_daily")


# ────────────────────────── ① replay: as-of 回放收集候选特征 ──────────────────────────

def replay(days_n=120, refresh_his=False):
    """竞价时点候选宇宙回放(无前视):

    每个交易日 d 的 09:25 可见信息只有:
      a. 昨日涨停池(梯队/封单/连板标记) —— 截断库只保留 date <= prev,
         当日 EOD 池从库里整个删掉, 结构性杜绝"今日收盘涨停"泄漏;
      b. 当日竞价数据: 08-07+ 用 bid_pool(实盘同口径), 更早用 His 板块竞价成分
         (GetBKJJBL, 实测回溯到 2025-10, 字段=竞价量比/涨幅/净额/换手/流通市值);
      c. 周期阶段/矩阵用 compute_cycle(prev_day) —— 与实盘 9:26 口径一致(昨日池+今日竞价)。
    候选宇宙 = 昨日池 ∪ 当日竞价池。标签在 fit 阶段用 OHLC 计算(今日开盘买→明日竞价卖)。
    """
    main = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    main.row_factory = sqlite3.Row
    days = [r[0] for r in main.execute("SELECT DISTINCT date FROM limit_pool ORDER BY date")]
    targets = days[-days_n:]
    his_cache_p = Path("/tmp/calib_his_universe.json")
    his = json.loads(his_cache_p.read_text()) if (his_cache_p.exists() and not refresh_his) else {}
    samples = []
    leak_stat = {"n": 0, "close_lt_open": 0}
    for d in targets:
        prev = next((x for x in reversed(days) if x < d), None)
        if not prev:
            continue
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        shutil.copy(DB, tmp.name)
        t = sqlite3.connect(tmp.name)
        t.row_factory = sqlite3.Row
        # 周期口径: 截断到 prev(当日池整个删除 → 泄漏在结构上不可能)
        for tb in TRUNC:
            try:
                t.execute(f"DELETE FROM {tb} WHERE date > ?", (prev,))
            except sqlite3.OperationalError:
                pass
        t.commit()
        try:
            ec.DB = tmp.name
            cr = ec.compute_cycle(prev, persist=False)   # 昨日周期(实盘 9:26 口径)
            # 昨日池(带真实连板高度)
            pool_rows = ec.load_pool()
            prev_rows = [r for r in pool_rows if r["date"] == prev]
            prev_map = {r["code"]: dict(r) for r in prev_rows}
        except Exception as e:
            print(f"  ⚠️ {d} 周期回放失败: {type(e).__name__} {str(e)[:40]}")
            t.close()
            Path(tmp.name).unlink(missing_ok=True)
            continue
        t.close()
        Path(tmp.name).unlink(missing_ok=True)

        # 当日竞价池: 08-07+ 用 bid_pool; 更早用 His 板块成分(带磁盘缓存)
        bids = {}
        n_bid = main.execute("SELECT COUNT(*) FROM bid_pool WHERE date=?", (d,)).fetchone()[0]
        if n_bid:
            for r in main.execute("SELECT code,change_pct,main_net,turnover_ratio FROM bid_pool WHERE date=?", (d,)):
                bids[r["code"]] = {"bid": r["change_pct"], "net": r["main_net"], "turn": r["turnover_ratio"]}
        else:
            if d not in his:
                uni = {}
                try:
                    bb = SPIDER.board_bid(d)
                    boards = (bb.get("List1") or []) + (bb.get("List2") or []) + (bb.get("List3") or [])
                    for row in boards[:BOARD_SCAN_LIMIT]:
                        pid, nm = row[0], row[1]
                        try:
                            comp = SPIDER.board_stocks(pid, d, st=50)
                            lst = comp.get("List") or []
                        except Exception:
                            lst = []
                        for r in lst:
                            if not isinstance(r, list) or len(r) < 10:
                                continue
                            it = normalize_bkjjbl(r)
                            uni[it["code"]] = {"bid": it.get("change_pct"),
                                               "net": it.get("main_net"),
                                               "turn": it.get("turnover_ratio"),
                                               "vratio": it.get("volume_ratio"),
                                               "name": it.get("name")}
                except Exception as e:
                    print(f"  ⚠️ {d} His 竞价成分失败: {e}")
                his[d] = uni
                his_cache_p.write_text(json.dumps(his, ensure_ascii=False))
                time.sleep(0.2)
            bids = {c: {"bid": v.get("bid"), "net": v.get("net"), "turn": v.get("turn")}
                    for c, v in his[d].items()}
        universe = set(prev_map) | set(bids)
        leak_stat["n"] += len(universe)
        mx = cr.get("matrix") or {}
        for code in universe:
            pv = prev_map.get(code)
            b = bids.get(code, {})
            samples.append({
                "date": d, "nd": next((x for x in days if x > d), None),
                "stage": cr["stage"], "m_high": mx.get("high"), "m_mid": mx.get("mid"),
                "code": code, "name": (b.get("name") if isinstance(b, dict) and b.get("name") else
                                       (pv["name"] if pv else code)),
                "in_prev_pool": 1 if pv else 0,
                "height": (pv["height"] if pv else 0) or 0,
                "seal": pv["seal_amount"] if pv else None,
                "max_seal": pv["max_seal"] if pv else None,
                "zt_time": pv["zt_time"] if pv else None,
                "bid": b.get("bid"), "main_net": b.get("net"),
                "turn": b.get("turn"), "circ_mv": (pv.get("circ_mv") if pv else None),
                "amount": (pv.get("amount") if pv else None),
            })
    his_stat = f"(His 回放 {len(his)} 天)" if his else "(全部 bid_pool 口径)"
    print(f"[replay] ✅ {len(set(s['date'] for s in samples))} 天 {len(samples)} 条 {his_stat}")
    return samples


# ────────────────────────── ② OHLC + 真实收益标签 ──────────────────────────

def sym(code):
    return ("sh" if code[0] in "65" else "bj" if code[0] in "48" else "sz") + code


def fetch_ohlc(codes, d0="2026-03-01", d1="2026-09-12"):
    out = {}
    for i, c in enumerate(sorted(codes)):
        url = (f"https://ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param={sym(c)},day,{d0},{d1},160,qfq")
        try:
            raw = urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
            data = json.loads(raw)["data"][sym(c)]
            rows = data.get("qfqday") or data.get("day")
            out[c] = {r[0]: [float(r[1]), float(r[2])] for r in rows}  # date -> [open, close]
        except Exception:
            out[c] = {}
        if i % 100 == 0:
            print(f"  行情 {i}/{len(codes)}")
        time.sleep(0.12)
    return out


def label(samples, ohlc):
    """真实收益: 今日开盘买(一字买不到则剔除) → 明日竞价卖(=今日收盘×(1+next_bid%))"""
    n_drop = 0
    for s in samples:
        k = ohlc.get(s["code"], {})
        day = k.get(s["date"])
        # 次日竞价涨幅 ≡ 次日开盘/今日收盘(集合竞价定义, 200条对照中位偏差0.00pct);
        # bid_pool 只覆盖 08-07 起, 用 OHLC 推导可把窗口扩到全部池史
        if day and s.get("next_bid") is None:
            dates_k = sorted(k)
            i = dates_k.index(s["date"]) if s["date"] in dates_k else -1
            if i >= 0 and i + 1 < len(dates_k):
                s["next_bid"] = (k[dates_k[i + 1]][0] / day[1] - 1) * 100
        if not day or s.get("next_bid") is None:
            s["ret"] = None
            n_drop += 1
            continue
        open_d, close_d = day
        prev = None
        dates = sorted(k)
        i = dates.index(s["date"])
        prev = k[dates[i - 1]][1] if i > 0 else None
        s["open"], s["close"] = open_d, close_d
        s["prev_close"] = prev
        lim = 30 if re.match(r"^(4|8|92)", s["code"]) else \
            20 if re.match(r"^(688|689|300|301)", s["code"]) else 10
        s["unbuyable"] = prev is not None and open_d >= prev * (1 + lim / 100) - 0.002
        s["ret"] = (close_d * (1 + s.get("next_bid") / 100) / open_d - 1) * 100
    return n_drop


# ────────────────────────── ③ 逻辑回归(纯Python) ──────────────────────────

def featurize(s):
    def zt_early(t):
        if not t:
            return 0.0
        v = t
        while v > 2400:
            v //= 100
        return 1.0 if v <= 935 else 0.0
    seal_ratio = (s["seal"] / s["max_seal"]) if (s.get("seal") and s.get("max_seal")) else None
    turn = s.get("turn")
    f = {
        "in_prev_pool": float(s.get("in_prev_pool") or 0),
        "height": float(s.get("height") or 0),
        "bid": s["bid"] if s.get("bid") is not None else 0.0,
        "bid_missing": 1.0 if s.get("bid") is None else 0.0,
        "log_seal": math.log1p(s["seal"] or 0) / 10,
        "seal_ratio": seal_ratio if seal_ratio is not None else 0.5,
        "main_net": (s.get("main_net") or 0) / 1e8,
        "turn": min(turn or 0, 60) / 10,
        "early": zt_early(s.get("zt_time")),
        "mx_强": 1.0 if s.get("m_high") == "强" else 0.0,
        "mm_强": 1.0 if s.get("m_mid") == "强" else 0.0,
    }
    return f


def fit_logreg(X, y, iters=4000, lr=0.5, l2=0.02):
    n, m = len(X), len(X[0])
    mu = [sum(r[j] for r in X) / n for j in range(m)]
    sd = [math.sqrt(sum((r[j] - mu[j]) ** 2 for r in X) / n) or 1.0 for j in range(m)]
    Xs = [[(r[j] - mu[j]) / sd[j] for j in range(m)] for r in X]
    w = [0.0] * m
    b = 0.0
    for _ in range(iters):
        gw = [0.0] * m
        gb = 0.0
        for r, yi in zip(Xs, y):
            z = b + sum(w[j] * r[j] for j in range(m))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = p - yi
            gb += e
            for j in range(m):
                gw[j] += e * r[j]
        b -= lr * gb / n
        for j in range(m):
            w[j] -= lr * gw[j] / n
    return {"w": w, "b": b, "mu": mu, "sd": sd}


def predict(model, feats):
    z = model["b"] + sum(model["w"][j] * ((feats[j] - model["mu"][j]) / model["sd"][j])
                         for j in range(len(model["w"])))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


# ────────────────────────── ④ 报告 ──────────────────────────

def report(samples, model, names, holdout_days):
    rows = [s for s in samples if s.get("ret") is not None and not s.get("unbuyable")]
    dates = sorted({s["date"] for s in rows})
    hold = set(dates[-holdout_days:])
    for s in rows:
        s["p"] = predict(model, list(featurize(s).values()))
    tr = [s for s in rows if s["date"] not in hold]
    ho = [s for s in rows if s["date"] in hold]
    print(f"\n== 样本: 可用 {len(rows)} (训练 {len(tr)} / 留出 {len(ho)}, 留出日 {holdout_days}) ==")
    print(f"   基础胜率(ret>0): 全体 {sum(1 for s in rows if s['ret'] > 0)/len(rows)*100:.0f}%"
          f" | 均值 {sum(s['ret'] for s in rows)/len(rows):+.2f}%")

    def bucket_table(rs, title):
        rs = sorted(rs, key=lambda s: -s["p"])
        q = max(1, len(rs) // 5)
        print(f"\n== {title} (按预测概率五分位) ==")
        print(f"{'分位':<10}{'样本':>5}{'胜率':>7}{'均值收益':>10}{'预测P':>8}")
        for i in range(5):
            part = rs[i * q:(i + 1) * q] if i < 4 else rs[4 * q:]
            if not part:
                continue
            wr = sum(1 for s in part if s["ret"] > 0) / len(part) * 100
            mr = sum(s["ret"] for s in part) / len(part)
            print(f"P{i+1}({'高' if i == 0 else '低' if i == 4 else '中'})"
                  f"{len(part):>7}{wr:>6.0f}%{mr:>9.2f}%{sum(s['p'] for s in part)/len(part):>8.2f}")

    bucket_table(rows, "全样本")
    bucket_table(ho, f"留出样本(最后{holdout_days}个交易日)")

    # 每日 Top3 对照(留出): 概率Top3 vs 引擎可做 vs 全体
    print(f"\n== 留出期每日 Top3 对照 ==")
    by_day = {}
    for s in ho:
        by_day.setdefault(s["date"], []).append(s)
    agg = {"prob": [], "act": [], "all": []}
    for d in sorted(by_day):
        day = by_day[d]
        top3 = sorted(day, key=lambda s: -s["p"])[:3]
        r_prob = sum(s["ret"] for s in top3) / len(top3)
        act = [s for s in day if s.get("act")] if "act" in day[0] else []
        r_act = (sum(s["ret"] for s in act) / len(act)) if act else None
        r_all = sum(s["ret"] for s in day) / len(day)
        agg["prob"].append(r_prob)
        if r_act is not None:
            agg["act"].append(r_act)
        agg["all"].append(r_all)
        print(f"  {d} 概率Top3 {r_prob:+.2f}% | 引擎可做({len(act)}) "
              f"{r_act:+.2f}%" if r_act is not None else f"  {d} 概率Top3 {r_prob:+.2f}% | 引擎可做 0只")
        print("     " + "  ".join(f"{s['name']}P{s['p']:.0f}:{s['ret']:+.1f}%" for s in top3))
    def st(xs):
        return f"{len(xs)}日 均值 {sum(xs)/len(xs):+.2f}%" if xs else "无"
    print(f"\n== 留出期汇总 == 概率Top3: {st(agg['prob'])} | 引擎可做: {st(agg['act'])} | 全体候选: {st(agg['all'])}")

    print("\n== 因子权重(标准化, 正=提升胜率) ==")
    for name, w in sorted(zip(names, model["w"]), key=lambda x: -abs(x[1])):
        print(f"  {name:<10} {w:+.3f}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--holdout", type=int, default=8)
    ap.add_argument("--refresh", action="store_true", help="忽略缓存重跑 replay/行情")
    a = ap.parse_args()

    if CACHE_S.exists() and not a.refresh:
        samples = json.loads(CACHE_S.read_text())
        print(f"[replay] 缓存命中 {len(samples)} 条 ({CACHE_S})")
    else:
        print(f"[replay] as-of 回放最近 {a.days} 个交易日…")
        samples = replay(a.days)
        CACHE_S.write_text(json.dumps(samples, ensure_ascii=False))
        print(f"[replay] ✅ {len(samples)} 条 → {CACHE_S}")

    codes = {s["code"] for s in samples}
    if CACHE_O.exists() and not a.refresh:
        ohlc = json.loads(CACHE_O.read_text())
        print(f"[ohlc] 缓存命中 {len(ohlc)} 只")
    else:
        print(f"[ohlc] 拉取 {len(codes)} 只日K…")
        ohlc = fetch_ohlc(codes)
        CACHE_O.write_text(json.dumps(ohlc))
        print(f"[ohlc] ✅ → {CACHE_O}")

    n_drop = label(samples, ohlc)
    rows = [s for s in samples if s.get("ret") is not None and not s.get("unbuyable")]
    print(f"[label] 剔除无行情/缺次日竞价 {n_drop} 条, 一字买不到 "
          f"{sum(1 for s in samples if s.get('ret') is not None and s.get('unbuyable'))} 条, "
          f"可用 {len(rows)} 条")
    # ── 泄漏自检(硬约束) ──
    if rows:
        n_down = sum(1 for s in rows if s["close"] < s["open"])
        base = sum(1 for s in rows if s["ret"] > 0) / len(rows) * 100
        print(f"[leak-check] 候选中今日收跌(未封住/炸板)占比: {n_down/len(rows)*100:.0f}%"
              f"  {'✅ 正常(候选宇宙含失败票, 无幸存者过滤)' if n_down/len(rows) >= 0.15 else '🚨 <15% 疑似幸存者泄漏!'}")
        print(f"[leak-check] 基础胜率: {base:.0f}%"
              f"  {'✅ 合理' if base <= 80 else '🚨 >80% 疑似前视泄漏!'}")

    dates = sorted({s["date"] for s in rows})
    hold = set(dates[-a.holdout:])
    train = [s for s in rows if s["date"] not in hold]
    names = list(featurize(train[0]).keys())
    X = [list(featurize(s).values()) for s in train]
    y = [1.0 if s["ret"] > 0 else 0.0 for s in train]
    model = fit_logreg(X, y)
    rep_rows = report(samples, model, names, a.holdout)
    out = {"fitted_at": datetime.now().isoformat(timespec="seconds"),
           "train_days": a.days - a.holdout, "holdout_days": a.holdout,
           "n_train": len(train), "names": names,
           "model": {"w": model["w"], "b": model["b"], "mu": model["mu"], "sd": model["sd"]}}
    Path("/tmp/calib_model.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"\n[done] 模型权重 → /tmp/calib_model.json ( {a.days - a.holdout} 训练日 / "
          f"{a.holdout} 留出日 ) —— 切不切排序键, 看留出期报告定")


if __name__ == "__main__":
    main()
