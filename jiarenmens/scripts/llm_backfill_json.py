#!/usr/bin/env python3
"""把 auction.db.llm_review 存档回填进 auction.json 的 llm 字段(页面展示用)。

用途:
  1. 功能上线(9dec81da7a)前的历史日期: 存档里有 v1/v2 输出, 回填后选股页卡片可展示/点入个股复核;
  2. 竞价班 llm 调用失败但存档已有当日行时手工补数据。
不覆盖已有 llm 键(--force 才覆盖); 只回填与 auction.json.date 相同的日期, 防跨日串档。

用法:
  python3 scripts/llm_backfill_json.py                    # 回填 public/data(默认), 取当日最新 prompt_ver
  python3 scripts/llm_backfill_json.py --date 2026-09-11 --prompt-ver v1 --force
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "auction.db"


def load_row(date, prompt_ver):
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = "SELECT * FROM llm_review WHERE date=?"
    args = [date]
    if prompt_ver:
        q += " AND prompt_ver=?"
        args.append(prompt_ver)
    # 同日多版本取最新存档行(prompt_ver 字典序 v2>v1, 再按创建时间)
    r = conn.execute(q + " ORDER BY prompt_ver DESC, created_at DESC LIMIT 1", args).fetchone()
    conn.close()
    if not r:
        return None
    return {
        "date": r["date"],
        "regime": r["regime"],
        "why": r["why"],
        "position_today": r["position"] or "",
        "picks": json.loads(r["picks"] or "[]"),
        "avoid": r["avoid"] or "",
        "model": r["model"],
        "prompt_ver": r["prompt_ver"],
        "latency_s": r["latency_s"],
        "degraded": bool(r["degraded"]),
        "generated_at": r["created_at"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="默认取目标 auction.json 的 date")
    ap.add_argument("--prompt-ver", default=None, help="默认取该日最新 prompt_ver")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 llm 键")
    ap.add_argument("target", nargs="?", default=str(ROOT.parent / "stockboard-app" / "public" / "data" / "latest" / "auction.json"))
    a = ap.parse_args()

    tp = Path(a.target)
    data = json.loads(tp.read_text(encoding="utf-8"))
    date = a.date or data.get("date")
    if not date:
        sys.exit("❌ 目标文件无 date 且未传 --date")
    if data.get("date") != date:
        sys.exit(f"❌ 目标文件 date={data.get('date')} 与请求日期 {date} 不一致, 拒绝跨日回填")
    if "llm" in data and data["llm"] and not a.force:
        print(f"⏭ auction.json 已有 llm 键(regime={data['llm'].get('regime')}), 跳过(--force 可覆盖)")
        return

    row = load_row(date, a.prompt_ver)
    if not row:
        sys.exit(f"❌ llm_review 无 {date}" + (f" prompt_ver={a.prompt_ver}" if a.prompt_ver else "") + " 的存档行")
    data["llm"] = row
    tmp = tp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(tp)
    print(f"✅ 已回填 {tp.name}: {date} prompt_ver={row['prompt_ver']} regime={row['regime']} "
          f"picks={len(row['picks'])} degraded={row['degraded']}")


if __name__ == "__main__":
    main()
