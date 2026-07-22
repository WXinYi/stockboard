"""
导出 SQLite 数据为 JSON，供 Vue 看板使用

用法:
    python scripts/export_json.py [--date 2026-07-22] [--out ../stockboard-app/public/data]
"""
import sqlite3, json, os, argparse
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"

def export(db_path, crawl_date, out_dir):
    out_dir = Path(out_dir)
    date_dir = out_dir / crawl_date
    date_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # players
    players = [dict(r) for r in conn.execute("SELECT * FROM players").fetchall()]
    for p in players:
        p.pop("updated_at", None)
        p.pop("created_at", None)

    # positions
    positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE crawl_date=?", (crawl_date,)
    ).fetchall()]
    for p in positions:
        p.pop("id", None)
        p.pop("updated_at", None)

    # trades（关联选手名称）
    trades = [dict(r) for r in conn.execute(
        """SELECT t.*, pl.name as player_name
           FROM trades t LEFT JOIN players pl ON t.zh_id = pl.zh_id
           WHERE t.crawl_date=?""", (crawl_date,)
    ).fetchall()]
    for t in trades:
        t.pop("id", None)
        t.pop("updated_at", None)

    conn.close()

    for name, data in [("players", players), ("positions", positions), ("trades", trades)]:
        with open(date_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    # 更新 index.json
    index_path = out_dir / "index.json"
    existing_dates = []
    if index_path.exists():
        existing_dates = json.loads(index_path.read_text()).get("dates", [])
    if crawl_date not in existing_dates:
        existing_dates.append(crawl_date)
        existing_dates.sort()
    index_path.write_text(json.dumps({"dates": existing_dates}, ensure_ascii=False), encoding="utf-8")

    print(f"✅ 导出完成: {len(players)} 选手, {len(positions)} 持仓, {len(trades)} 调仓 → {date_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出 SQLite 数据为 JSON")
    parser.add_argument("--date", type=str, help="指定日期 (YYYY-MM-DD)，默认最新")
    parser.add_argument("--out", type=str, default=str(ROOT.parent / "stockboard-app" / "public" / "data"), help="输出目录")
    args = parser.parse_args()

    if not args.date:
        conn = sqlite3.connect(str(DB_PATH))
        latest = conn.execute("SELECT MAX(crawl_date) FROM positions WHERE crawl_date != ''").fetchone()[0]
        conn.close()
        args.date = latest or date.today().isoformat()

    export(DB_PATH, args.date, args.out)
