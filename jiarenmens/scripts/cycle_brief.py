#!/usr/bin/env python3
"""
当前市场超短格局报告: 周期阶段 + 主线 + 龙头谱系 + 梯队矩阵 (周期引擎 CLI)

用法(在 jiarenmens/ 目录):
  python scripts/cycle_brief.py               # 最新交易日
  python scripts/cycle_brief.py --date 2026-08-28

market_breadth 断档自愈: 计算前检查宽度表是否覆盖目标日, 缺则从 KPL His
一次拉 250 天补齐(曾因无人日常更新, 9/4 用 8/28 旧宽度误判"分歧")。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "auction.db"
sys.path.insert(0, str(ROOT))
from src.analysis.emotion_cycle import compute_cycle  # noqa: E402
from src.config import KPL_HOST_HIS  # noqa: E402
from src.spiders.auction_spider import KPLSpider  # noqa: E402


def _latest_pool_date():
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        return conn.execute("SELECT MAX(date) FROM limit_pool").fetchone()[0]


def ensure_breadth(target: str) -> int:
    """宽度表缺 target 当日行时, 拉一次 KPL RiseFallAnalysis His(250天) 补齐"""
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT MAX(date) FROM market_breadth").fetchone()
    if row and row[0] and row[0] >= target:
        return 0
    data = KPLSpider()._get({"a": "RiseFallAnalysis", "st": 250, "apiv": "w43",
                             "c": "HisHomeDingPan", "PhoneOSNew": 1, "Index": 0},
                            KPL_HOST_HIS)
    n = 0
    with sqlite3.connect(DB) as conn:
        for r in data.get("info") or []:
            if isinstance(r, list) and len(r) >= 7:
                conn.execute(
                    "INSERT OR REPLACE INTO market_breadth"
                    "(date, zt, dt, natural_zt, once_dt, broke_rate, zhaban) VALUES(?,?,?,?,?,?,?)",
                    (str(r[6]), r[0], r[1], r[2], r[3], r[4], r[5]))
                n += 1
    print(f"  🩹 market_breadth 断档自愈: 补 {n} 天 (覆盖至 {target})")
    return n


def main():
    ap = argparse.ArgumentParser(description="超短格局报告")
    ap.add_argument("--date", help="YYYY-MM-DD(默认最新涨停池日期)")
    args = ap.parse_args()
    if not args.date:
        args.date = _latest_pool_date()
    try:
        ensure_breadth(args.date)
    except Exception as e:
        print(f"  ⚠️ 宽度自愈失败, 继续用库内数据: {e}")
    r = compute_cycle(args.date)
    m = r["metrics"]
    print("=" * 64)
    print(f"超短格局 · {r['date']} · 周期阶段: {r['stage']} (置信度 {r['confidence']}/9)")
    print("=" * 64)
    for x in r["reasons"]:
        print(f"  · {x}")
    print(f"\n📌 操作提示: {r['playbook']}")
    print(f"\n── 梯队结构 ──")
    print(f"  低位(1-2板) {m['ladder']['low']} 只 | 中位(3-5板) {m['ladder']['mid']} 只 | "
          f"高位(≥6板) {m['ladder']['high']} 只 | 最高 {m['height']}B (昨 {m['height_prev']}B)")
    print(f"  晋级率: 低位 {fmt(m['promo']['low'])} | 中位 {fmt(m['promo']['mid'])} | "
          f"高位 {fmt(m['promo']['high'])}")
    print(f"  矩阵定位: 高位{r['matrix']['high']} × 中位{r['matrix']['mid']}")
    print(f"\n── 主线板块(涨停数/高度) ──")
    for a in r["mainlines"]:
        print(f"  {a['board']:8s} {a['count']:2d} 只 最高{a['max_pid']}B "
              f"成交 {a['amount']/1e8:.0f}亿 | {'、'.join(a['names'][:5])}")
    print(f"\n── 龙头谱系 ──")
    for l in r["leaders"]:
        print(f"  [{l['role']}] {l['name']}({l['code']}) {l['pid']}板 — {l['note']}")
    print(f"\n💾 已落库 analysis.db (cycle_daily / leaders_daily)")
    return 0


def fmt(x):
    return f"{x:.0%}" if x is not None else "无数据"


if __name__ == "__main__":
    sys.exit(main())
