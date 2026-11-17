#!/usr/bin/env python3
"""六情绪「按日截断」对拍夹具生成器(供前端 vitest 回归使用)

用法(在 jiarenmens/ 目录):
  venv/bin/python scripts/six_parity_fixture.py                 # 默认最近 5 个交易日
  venv/bin/python scripts/six_parity_fixture.py --days 8
  venv/bin/python scripts/six_parity_fixture.py --date 2026-09-04,2026-09-03

口径(⚠️ 两处 DB 都要指向截断库, 否则 load_pool 仍读全库——已踩坑):
  1) 复制 auction.db → /tmp 临时库, DELETE 四张日终表(mood_daily/limit_pool/market_breadth/index_daily)中
     date > D 的行(实时分位语义 = 不含未来日);
  2) six_emotions.DB 与 emotion_cycle.DB 同时指向该临时库;
  3) compute_all() 取当日 out 值(market/spec/sector/整体三项/dominant)作为参考。

前端回归: stockboard-app/src/utils/__tests__/sixParity.test.js 读该夹具, 与 JS 实时引擎逐日断言全等。
改公式/阈值流程: 改哪边都要 1) 跑本脚本重生成夹具 2) 跑前端 vitest, 两边同批提交。

夹具结构(2026-11-17 复核 P0-2 补充):
  rows  — 截止到 max(case.date) 的逐日**原始分量**(紧凑键, 同 six_history.json 口径),
          由 Python _load_all() 产出。前端按 case.date 切片后喂给 computeSixLive(),
          实现"同一输入 → 同一输出"的真对拍。
  cases — 各日按截断口径算出的参考值(market/spec/sector/m_*/dominant)。
  ⚠️ 不要用 public/data/latest/six_history.json 做对拍基准: 该切片生产者已下线,
     文件是冻结旧产物, 其 zt/dt 与当前 DB(market_breadth) 口径不同(172/258 天不一致),
     会伪造出"双实现漂移"的假象。
"""
import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_PATH = ROOT.parent / "stockboard-app" / "src" / "utils" / "__tests__" / "fixtures" / "six_ref_asof.json"
TRUNC_TABLES = ("mood_daily", "limit_pool", "market_breadth", "index_daily")
KEYS = ("market", "spec", "sector", "m_market", "m_spec", "m_sector", "dominant")


def asof_scores(db_path: str, date: str) -> dict:
    """截断库上的当日六情绪(不含未来日), 返回参考值 dict"""
    import src.analysis.six_emotions as se
    import src.analysis.emotion_cycle as ec
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    shutil.copy(db_path, tmp.name)
    try:
        c = sqlite3.connect(tmp.name)
        for t in TRUNC_TABLES:
            c.execute(f"DELETE FROM {t} WHERE date > ?", (date,))
        c.commit()
        c.close()
        se.DB = tmp.name
        ec.DB = tmp.name
        out, _ = se.compute_all()
        if date not in out:
            raise KeyError(f"{date} 不在截断后的数据范围(检查日期/表)")
        o = out[date]
        return {k: o.get(k) for k in KEYS}
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def dump_rows(db_path: str, max_date: str) -> list:
    """导出 <= max_date 的逐日原始分量(紧凑键), 作为前端对拍的同一输入源。

    这些分量是"当日值", 与按日截断无关(height_prev/promo/relay/tc_prev 只依赖前一日,
    run_days/switches_5d 只依赖更早历史), 因此一份全量序列可按 case.date 切片复用。
    """
    import src.analysis.emotion_cycle as ec
    import src.analysis.six_emotions as se
    old_db, old_ec_db = se.DB, ec.DB
    se.DB = ec.DB = db_path          # load_pool 读 emotion_cycle.DB, 两个都要指过去
    try:
        dates, metrics = se._load_all()
    finally:
        se.DB, ec.DB = old_db, old_ec_db
    rows = []
    for d in dates:
        if d > max_date:
            continue
        m = metrics[d]
        rows.append({
            "d": d, "s": m.get("strong"), "dt": m.get("dt"), "broke": m.get("broke"),
            "zt": m.get("zt"), "h": m.get("height"), "promo": m.get("promo"),
            "relay": m.get("relay"), "zb": m.get("zhaban"), "tc": m.get("top_cnt"),
            "tc_prev": m.get("top_cnt_prev"), "ta": m.get("top_amt"), "tb": m.get("top_board"),
            "run": m.get("run_days"), "sw": m.get("switches_5d"),
            "idx": m.get("idx_trend"), "bid": m.get("bid_amt"),
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="生成六情绪按日截断对拍夹具")
    ap.add_argument("--days", type=int, default=5, help="取最近 N 个交易日(默认5)")
    ap.add_argument("--date", type=str, default="", help="逗号分隔指定日期, 优先于 --days")
    ap.add_argument("--db", type=str, default=str(ROOT / "data" / "auction.db"))
    ap.add_argument("--out", type=str, default=str(FIXTURE_PATH))
    args = ap.parse_args()

    with sqlite3.connect(args.db) as c:
        rows = c.execute("SELECT DISTINCT date FROM limit_pool ORDER BY date DESC LIMIT ?", (args.days,)).fetchall()
    dates = [d for (d,) in rows]
    if args.date:
        dates = [x.strip() for x in args.date.split(",") if x.strip()]
    if not dates:
        raise SystemExit("没有可用日期")

    cases = []
    for d in sorted(dates):
        ref = asof_scores(args.db, d)
        cases.append({"date": d, **ref})
        print(f"  {d}  {ref['dominant']}  ({ref['market']}/{ref['spec']}/{ref['sector']})")

    fixture = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db_as_of": dates[0] if len(dates) == 1 else max(dates),
        "note": "按日截断口径(不含未来日); 再生成: jiarenmens venv/bin/python scripts/six_parity_fixture.py",
        "rows": dump_rows(args.db, max(c["date"] for c in cases)),
        "cases": cases,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    print(f"✅ 夹具已写 {out} ({len(cases)} 天)")


if __name__ == "__main__":
    main()
