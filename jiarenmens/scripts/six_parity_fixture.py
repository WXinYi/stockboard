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
