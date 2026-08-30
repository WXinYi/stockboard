#!/usr/bin/env python3
"""清理 crawl_data.db 超过保留窗口的采集日并 VACUUM, 防止 db 无限增长。

- trades/positions 按 (zh_id, crawl_date) 先删后写, 删旧采集日不影响当日续采
- players 表很小(<7MB)且被 trades/positions 外键引用, 保留不删
- 默认 dry-run 只打印; crawl.yml 中以 --apply 执行
"""
import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "crawl_data.db"
KEEP_DATES = 40   # 保留最近 40 个采集日(约两个月), db 封顶在 ~90MB


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行删除+VACUUM(默认 dry-run)")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[prune] {DB_PATH} 不存在, 跳过")
        return

    conn = sqlite3.connect(DB_PATH)
    dates = [r[0] for r in conn.execute("SELECT DISTINCT crawl_date FROM trades ORDER BY crawl_date")]
    if len(dates) <= KEEP_DATES:
        print(f"[prune] 共 {len(dates)} 个采集日 <= 保留窗口 {KEEP_DATES}, 跳过")
        return

    drop = dates[:-KEEP_DATES]
    before = DB_PATH.stat().st_size
    n_trades = conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE crawl_date IN ({','.join('?' * len(drop))})", drop
    ).fetchone()[0]
    n_pos = conn.execute(
        f"SELECT COUNT(*) FROM positions WHERE crawl_date IN ({','.join('?' * len(drop))})", drop
    ).fetchone()[0]

    if args.apply:
        for d in drop:
            conn.execute("DELETE FROM trades WHERE crawl_date=?", (d,))
            conn.execute("DELETE FROM positions WHERE crawl_date=?", (d,))
        conn.commit()
        conn.execute("VACUUM")   # 回收空闲页, 否则 DELETE 只标记 free-list 不缩文件
        conn.commit()
        after = DB_PATH.stat().st_size
        print(f"[prune] 已删除 {len(drop)} 个采集日({drop[0]}~{drop[-1]}): trades {n_trades} 行 / positions {n_pos} 行, "
              f"{before / 1e6:.1f}MB → {after / 1e6:.1f}MB")
    else:
        print(f"[prune][dry-run] 将删除 {len(drop)} 个采集日({drop[0]}~{drop[-1]}): "
              f"trades {n_trades} 行 / positions {n_pos} 行 (加 --apply 执行)")
    conn.close()


if __name__ == "__main__":
    main()
