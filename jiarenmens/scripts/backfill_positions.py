#!/usr/bin/env python3
"""断档回填: 从 git 历史某次导出的 JSON 重建指定采集日的 positions/trades 批次。

背景(2026-08-31 断档): 收盘 sync 缺位导致当日 db 批次未持久化; 但每次 run 的
导出 JSON 都进了 git——players/*.json 的 p 数组=当日持仓快照, t 数组=全历史调仓。
本脚本把指定 commit 的导出内容回填为 crawl_date=DATE 的批次, 字段与 schema 一一对应:
  p: sn→stock_name, sc→stock_code, cp→cost_price, np→current_price,
     pr→profit_ratio, rr→position_ratio
  t(仅 td==DATE): td→trade_date, dr→direction, sn/sc 同上, tc→trades_count,
     rr→position_ratio, pr→price; position_value/position_change 无源置 0

用法(在 jiarenmens/ 下):
  python3 scripts/backfill_positions.py --commit e35897d2ef --date 2026-08-31           # dry-run
  python3 scripts/backfill_positions.py --commit e35897d2ef --date 2026-08-31 --apply   # 实际执行

回填后必须手动 `release_db.py --upload-latest` 上热层, 否则下个收盘 run 会用
没有该日的库重采并覆盖热层, 回填即失效。
"""
import argparse
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"
REPO = ROOT.parent
EXPORT_PATH = "stockboard-app/public/data/latest/players"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--commit", required=True, help="含当日导出的 git commit/branch")
    ap.add_argument("--date", required=True, help="回填批次日期 YYYY-MM-DD")
    ap.add_argument("--apply", action="store_true", help="实际执行(默认 dry-run)")
    args = ap.parse_args()

    # 1. 从 git 历史解出当时的 players 导出
    import io
    tmp = Path(tempfile.mkdtemp(prefix="backfill-"))
    print(f"[archive] git archive {args.commit} → {tmp}")
    out = subprocess.run(["git", "archive", args.commit, EXPORT_PATH], cwd=REPO,
                         check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(out)) as tf:
        tf.extractall(tmp)
    players_dir = tmp / EXPORT_PATH
    files = list(players_dir.glob("*.json"))
    print(f"[archive] 解出 {len(files)} 个选手 JSON")

    # 2. 解析 → 待插入行
    conn = sqlite3.connect(DB_PATH)
    known = {r[0] for r in conn.execute("SELECT zh_id FROM players")}
    pos_rows, trade_rows, new_players = [], [], {}
    for f in files:
        zh = f.stem
        d = json.loads(f.read_text())
        if zh not in known:
            # 当日新面孔(其档案行随当日断档批次丢失): 补最小档案行, 满足外键/名称查询
            new_players[zh] = d.get("name", "")
        for p in d.get("p") or []:
            pos_rows.append((zh, p.get("sn", ""), p.get("sc", ""), p.get("cp") or 0,
                             p.get("np") or 0, p.get("pr") or 0, p.get("rr") or 0))
        for t in d.get("t") or []:
            if t.get("td") != args.date:
                continue
            trade_rows.append((zh, t.get("sn", ""), t.get("sc", ""),
                               t.get("tc") or 1, t.get("rr", ""), t.get("pr") or 0,
                               t.get("td", "")))
    print(f"[parse] positions={len(pos_rows)} 行, trades(td=={args.date})={len(trade_rows)} 行, "
          f"需补最小档案的新选手={len(new_players)} 人")
    if pos_rows:
        print("[sample]", pos_rows[0])
    if trade_rows:
        print("[sample]", trade_rows[0])
    if not args.apply:
        print("[dry-run] 加 --apply 实际执行")
        return 0

    # 3. 先删后插(幂等), 与 crawl 幂等约定一致
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO players (zh_id, name) VALUES (?,?)",
        list(new_players.items()))
    cur.execute("DELETE FROM positions WHERE crawl_date=?", (args.date,))
    cur.execute("DELETE FROM trades WHERE crawl_date=?", (args.date,))
    cur.executemany(
        "INSERT INTO positions (zh_id, stock_name, stock_code, cost_price, current_price,"
        " profit_ratio, position_ratio, update_time, crawl_date) VALUES (?,?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], "", args.date) for r in pos_rows])
    cur.executemany(
        "INSERT INTO trades (zh_id, stock_name, stock_code, trades_count, position_ratio,"
        " price, trade_date, crawl_date) VALUES (?,?,?,?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], args.date) for r in trade_rows])
    conn.commit()

    # 4. 校验
    ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
    dates = [r[0] for r in conn.execute("SELECT DISTINCT crawl_date FROM trades ORDER BY crawl_date")]
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE crawl_date=?", (args.date,)).fetchone()[0]
    conn.close()
    print(f"[verify] integrity={ic}, {args.date} 批次 trades={n} 行")
    print(f"[verify] 库内采集日 {dates[0]}~{dates[-1]} 共 {len(dates)} 天")
    tmp.joinpath(EXPORT_PATH.split("/")[0]).exists() and None  # noop
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    if ic != "ok":
        sys.exit("❌ 完整性校验失败, 请勿上传")
    print("[done] 记得执行: python3 scripts/release_db.py --upload-latest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
