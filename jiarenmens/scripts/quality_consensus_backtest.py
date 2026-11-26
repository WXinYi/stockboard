#!/usr/bin/env python3
"""高手(优质选手)重仓共识票·次日跟随回测（2026-11-07 立项，预注册规格）

数据源: git 逐日存档的 players_index.json（每日最后一班, 时点真值零重放——
quality 标记是当时代码当时算的, 持仓码表 idx16 是当日采集快照）。
背景: stocks.json 的 qh 字段经检查为恒定退化值(全行相同), 不可用, 故自行聚合。

规格（写死不再改）:
- 高手 = 档案行 idx15 为 True 的选手; 共识票 = 当日 ≥N 个高手持有(qh≥2 主口径,
  qh≥3 副口径), 对照 = 恰 1 个高手持有(qh==1)
- 动作: D+1 开盘买 → A=D+1 收盘卖 / B=D+2 开盘卖; 开盘≥涨停价 → 一字不可成交单列
- 每个交易日每个票是一个事件(持仓连续多日会重复计, 另报"去重票"口径)
- 分半验证(按时间); 负结果照发; 无参数扫描

用法: python scripts/quality_consensus_backtest.py
"""
import sys
import json
import subprocess
import time
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]          # jiarenmens/
REPO = ROOT.parent                                   # 仓库根(git pathspec 用)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from divergence_anchor_backtest import kline, limit_ratio   # 共用机制

IDX = "stockboard-app/public/data/latest/players_index.json"


def git_days():
    log = subprocess.run(
        ["git", "log", "--format=%h %cd", "--date=short", "--", IDX],
        capture_output=True, text=True, cwd=str(REPO)).stdout.splitlines()
    last_of_day = {}
    for line in log:
        sha, d = line.split()
        if d not in last_of_day:
            last_of_day[d] = sha
    return last_of_day


def show(sha):
    raw = subprocess.run(["git", "show", f"{sha}:{IDX}"],
                         capture_output=True, text=True, cwd=str(REPO)).stdout
    return json.loads(raw)


def main():
    t0 = time.time()
    days = git_days()
    print(f"players_index 存档 {len(days)} 天")
    # 交易序列(用存档日期序列)
    all_days = sorted(days)
    ti = {d: i for i, d in enumerate(all_days)}
    events = []   # {D, code, name?, qh}
    parsed = 0
    for d in all_days:
        if d < "2026-09-01" or d > "2026-11-06":
            continue
        try:
            rows = show(days[d])
        except Exception as e:
            print(f"  {d} 解析失败: {e}")
            continue
        parsed += 1
        holders = defaultdict(int)
        nm = {}
        for r in rows:
            if not r[15]:
                continue
            for c in r[16] or []:
                holders[c] += 1
                nm.setdefault(c, None)
        for c, qh in holders.items():
            events.append({"D": d, "code": c, "qh": qh})
    print(f"解析 {parsed} 天, 事件(高手持仓票·日) {len(events)} 个 | {time.time()-t0:.1f}s")

    # 票名: 用当日采集的 players_index 没有票名 → 从 crawl_data.db players/trades 补
    import sqlite3
    conn = sqlite3.connect(f"file:{ROOT/'data'/'crawl_data.db'}?mode=ro", uri=True)
    nm_map = {}
    for c, n in conn.execute(
            "SELECT stock_code, MAX(stock_name) FROM trades WHERE stock_name != '' GROUP BY stock_code"):
        nm_map[c] = n
    conn.close()

    # 收益
    for e in events:
        i = ti[e["D"]]
        d1 = all_days[i+1] if i+1 < len(all_days) else None
        d2 = all_days[i+2] if i+2 < len(all_days) else None
        bars = kline(e["code"])
        e["A"] = e["B"] = None
        e["unfill"] = e["nok"] = False
        if not bars or not d1 or e["D"] not in bars or d1 not in bars:
            e["nok"] = True
            continue
        d0c = bars[e["D"]][1]
        o1, c1 = bars[d1]
        if o1 >= round(d0c * (1 + limit_ratio(e["code"], nm_map.get(e["code"], ""))), 2):
            e["unfill"] = True
            continue
        e["A"] = (c1 - o1) / o1 * 100
        if d2 and d2 in bars:
            e["B"] = (bars[d2][0] - o1) / o1 * 100

    def stats(es, tag):
        a = [e["A"] for e in es if e["A"] is not None]
        b = [e["B"] for e in es if e["B"] is not None]
        if not a:
            print(f"{tag}: 无样本")
            return
        a2 = sorted(a)
        win = sum(1 for x in a if x > 0) / len(a) * 100
        btxt = f" | B 均值{sum(b)/len(b):+.2f}% 胜率{sum(1 for x in b if x>0)/len(b)*100:.0f}% (n={len(b)})" if b else ""
        print(f"{tag}: A 均值{sum(a)/len(a):+.2f}% 中位{a2[len(a2)//2]:+.2f}% 胜率{win:.0f}% "
              f"n={len(a)}{btxt}")
        half = len(a) // 2
        if half >= 5:
            print(f"    A前半 {sum(a[:half])/half:+.2f}% (n={half}) | "
                  f"A后半 {sum(a[half:])/(len(a)-half):+.2f}% (n={len(a)-half})")

    print("\n== 结果 ==")
    g2 = [e for e in events if e["qh"] >= 2 and not e["unfill"] and not e["nok"]]
    g3 = [e for e in events if e["qh"] >= 3 and not e["unfill"] and not e["nok"]]
    g1 = [e for e in events if e["qh"] == 1 and not e["unfill"] and not e["nok"]]
    uf = sum(1 for e in events if e["qh"] >= 2 and e["unfill"])
    nk = sum(1 for e in events if e["qh"] >= 2 and e["nok"])
    print(f"事件: qh≥2 共 {sum(1 for e in events if e['qh']>=2)} (一字 {uf}, 缺K {nk}) "
          f"| qh≥3 {sum(1 for e in events if e['qh']>=3)} | qh==1 {sum(1 for e in events if e['qh']==1)}")
    print("— 主口径 qh≥2:")
    stats(g2, "  ")
    print("— 副口径 qh≥3:")
    stats(g3, "  ")
    print("— 对照 qh==1:")
    stats(g1, "  ")
    # 去重票口径: 每票只取第一次进入 qh≥2 的事件
    seen, uniq = set(), []
    for e in sorted(g2, key=lambda x: x["D"]):
        if e["code"] not in seen:
            seen.add(e["code"])
            uniq.append(e)
    print("— 去重票(每票首日):")
    stats(uniq, "  ")
    # 高频共识票 Top
    cnt = defaultdict(int)
    for e in g2:
        cnt[e["code"]] += 1
    top = sorted(cnt.items(), key=lambda x: -x[1])[:10]
    print("— qh≥2 出现最多的票(天次): " +
          ", ".join(f"{nm_map.get(c, c)}×{k}" for c, k in top))


if __name__ == "__main__":
    main()
