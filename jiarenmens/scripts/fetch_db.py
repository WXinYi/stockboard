#!/usr/bin/env python3
"""回测取数: 从 GitHub Release 三层存储拉取 crawl_data.db 归档到本地(公开仓, 匿名可用)。

  python scripts/fetch_db.py --list                  # 列出所有可用归档(热/温/冷层)
  python scripts/fetch_db.py --latest                # 热层(最近40采集日) → data/crawl_data.db
  python scripts/fetch_db.py --week 2026-W35         # 温层周档 → data/archive/crawl-2026-W35.db
  python fetch_db.py --month 2026-08                 # 冷层月档 → data/archive/crawl-2026-08.db
  python fetch_db.py --range 2026-07 2026-08         # 逐月拉取并合并 → data/archive/merged-*.db

合并规则: crawl_date 不跨月重叠, 按 crawl_date 去重合并; players 以 zh_id 为主键 OR REPLACE。
"""
import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "WXinYi/stockboard"
API = f"https://api.github.com/repos/{REPO}"
ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "data" / "archive"
RETRIES = 3


def _headers():
    h = {"User-Agent": "stockboard-fetch-db"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"   # 私有仓/提高限额(可选)
    return h


def _get(url, raw=False):
    """带重试: 大陆直连 GitHub 常见 SSL 瞬断/重置, 3 次退避; 支持走 HTTPS_PROXY 代理。"""
    last = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers=_headers())
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read() if raw else json.loads(r.read())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if i < RETRIES - 1:
                wait = 3 * (i + 1)
                print(f"⚠️ 请求失败({e}), {wait}s 后重试 {i + 2}/{RETRIES}...", file=sys.stderr)
                time.sleep(wait)
    raise last


def get_release(tag: str):
    try:
        return _get(f"{API}/releases/tags/{tag}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def download_gz(tag: str, asset_name: str, dest_db: Path) -> Path:
    rel = get_release(tag)
    if not rel:
        sys.exit(f"❌ Release {tag} 不存在(用 --list 查看可用归档)")
    asset = next((a for a in rel.get("assets", []) if a["name"] == asset_name), None)
    if not asset:
        sys.exit(f"❌ {tag} 下无资产 {asset_name}: {[a['name'] for a in rel.get('assets', [])]}")
    dest_db.parent.mkdir(parents=True, exist_ok=True)
    gz = Path(tempfile.mkstemp(suffix=".db.gz")[1])
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"],
                                                       headers=_headers()), timeout=120) as r, open(gz, "wb") as f:
        shutil.copyfileobj(r, f)
    with gzip.open(gz, "rb") as s, open(dest_db, "wb") as d:
        shutil.copyfileobj(s, d)
    gz.unlink()
    ic = sqlite3.connect(dest_db).execute("PRAGMA integrity_check").fetchone()[0]
    if ic != "ok":
        sys.exit(f"❌ {dest_db} 完整性校验失败: {ic}")
    print(f"✅ {tag}/{asset_name} → {dest_db} ({dest_db.stat().st_size / 1e6:.1f}MB)")
    return dest_db


def cmd_list():
    rels = _get(f"{API}/releases?per_page=100")
    for rel in sorted(rels, key=lambda r: r["tag_name"]):
        assets = ", ".join(f"{a['name']}({a['size'] / 1e6:.1f}MB)" for a in rel.get("assets", []))
        print(f"{rel['tag_name']:<16} {assets}")


def merge_files(out: Path, parts: list):
    """按 crawl_date 去重合并多个归档库。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    shutil.copyfile(parts[0], out)
    conn = sqlite3.connect(out)
    for i, p in enumerate(parts[1:], 1):
        conn.execute(f"ATTACH ? AS db{i}", (str(p),))
        for table, cols in (("players", None), ("trades", "crawl_date"), ("positions", "crawl_date")):
            if cols:  # 只插主库没有的采集日(月份间日期不重叠, 此处去重防重叠周/月档)
                conn.execute(f"""
                    INSERT INTO {table}
                    SELECT * FROM db{i}.{table}
                    WHERE {cols} NOT IN (SELECT DISTINCT {cols} FROM {table})
                """)
            else:
                conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM db{i}.{table}")
        conn.execute(f"DETACH db{i}")
    conn.commit()
    n = conn.execute("SELECT COUNT(*), MIN(crawl_date), MAX(crawl_date) FROM trades").fetchone()
    conn.close()
    print(f"✅ 合并完成 → {out}: trades {n[0]}, 范围 {n[1]}~{n[2]}")


def cmd_range(m_from: str, m_to: str):
    months = []
    y, m = map(int, m_from.split("-"))
    end_y, end_m = map(int, m_to.split("-"))
    while (y, m) <= (end_y, end_m):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    parts = []
    for mo in months:
        dest = ARCHIVE_DIR / f"crawl-{mo}.db"
        if not dest.exists():
            download_gz(f"db-m{mo}", f"crawl-{mo}.db.gz", dest)
        parts.append(dest)
    merge_files(ARCHIVE_DIR / f"merged-{m_from}_{m_to}.db", parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--latest", action="store_true", help="热层 → data/crawl_data.db")
    ap.add_argument("--week", metavar="YYYY-Www")
    ap.add_argument("--month", metavar="YYYY-MM")
    ap.add_argument("--range", nargs=2, metavar=("月", "月"), help="如 2026-03 2026-08")
    args = ap.parse_args()

    if args.list:
        cmd_list()
    elif args.latest:
        download_gz("db-state", "crawl-latest.db.gz", ROOT / "data" / "crawl_data.db")
    elif args.week:
        download_gz(f"db-w{args.week}", f"crawl-{args.week}.db.gz",
                    ARCHIVE_DIR / f"crawl-{args.week}.db")
    elif args.month:
        download_gz(f"db-m{args.month}", f"crawl-{args.month}.db.gz",
                    ARCHIVE_DIR / f"crawl-{args.month}.db")
    elif args.range:
        cmd_range(args.range[0], args.range[1])
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
