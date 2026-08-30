#!/usr/bin/env python3
"""crawl_data.db ↔ GitHub Release 三层存储工具（热/温/冷），见 docs/DATA_PIPELINE.md。

分层:
  热层 tag db-state     资产 crawl-latest.db.gz        最近40采集日全量, 每收盘后覆盖
  温层 tag db-w<ISO周>  资产 crawl-<ISO周>.db.gz       单周归档, 滚动保留 --retain-weeks 周
  冷层 tag db-m<YYYY-MM> 资产 crawl-<YYYY-MM>.db.gz    单月归档, 永久

用法(上传需 GITHUB_TOKEN/GH_TOKEN 环境变量; workflow 内用 secrets.GITHUB_TOKEN):
  python scripts/release_db.py --upload-latest          # 当前 db 快照 → 热层
  python scripts/release_db.py --archive-weeks          # 库内最近一周 → 温层
  python scripts/release_db.py --archive-months         # 库内"已完成月" → 冷层(永久)
  python scripts/release_db.py --sync                   # 以上三条 + 清理超龄温层(收盘后 run 一次调用)
  python scripts/release_db.py --init                   # 首次迁移: 热层 + 全部已完成月
  python scripts/release_db.py --download-latest        # 拉热层 → data/crawl_data.db (workflow 恢复用)
  python scripts/release_db.py --gz-only /tmp/x.db.gz   # 无 token, 本地生成快照 gz 自检

设计要点:
  - 快照用 sqlite backup API, WAL 下也一致; 上传前 PRAGMA integrity_check
  - 幂等: 同名资产先删后传, 重复执行结果一致
  - 月归档只封"已完成月"(库内存在下一月数据), 当月由周层覆盖
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "crawl_data.db"
REPO = os.environ.get("GITHUB_REPO", "WXinYi/stockboard")
API_BASE = f"https://api.github.com/repos/{REPO}"
UPLOAD_BASE = f"https://uploads.github.com/repos/{REPO}/releases"
HOT_TAG = "db-state"
HOT_ASSET = "crawl-latest.db.gz"


# ────────────────────────── GitHub API ──────────────────────────

def _token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not t:
        sys.exit("❌ 需要 GITHUB_TOKEN 环境变量(仓库内用 secrets.GITHUB_TOKEN)")
    return t


def _opt_token() -> str:
    """读操作(公开仓匿名也可), 有 token 就带上。"""
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def _api(method: str, url: str, *, token: str = "", data=None, ctype: str = "application/json"):
    """返回 (status, 解析后的json或bytes)。404 时 status=404 不抛错。"""
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "stockboard-release-db")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            try:
                return r.status, json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get_release(tag: str):
    st, rel = _api("GET", f"{API_BASE}/releases/tags/{tag}", token=_opt_token())
    return rel if st == 200 else None


def ensure_release(tag: str, title: str, body: str) -> dict:
    rel = get_release(tag)
    if rel:
        return rel
    st, rel = _api("POST", f"{API_BASE}/releases", token=_token(),
                   data=json.dumps({"tag_name": tag, "name": title, "body": body}).encode())
    if st not in (201, 200):
        sys.exit(f"❌ 创建 release {tag} 失败: {st} {rel}")
    print(f"[release] 已创建 {tag}")
    return rel


def upload_asset(tag: str, name: str, path: Path, body: str = ""):
    rel = ensure_release(tag, tag, body)
    # 同名资产先删(Release 资产不可覆盖)
    for a in rel.get("assets", []):
        if a["name"] == name:
            _api("DELETE", a["url"], token=_token())
            print(f"[release] 已删除旧资产 {tag}/{name}")
    with open(path, "rb") as f:
        st, resp = _api("POST", f"{UPLOAD_BASE}/{rel['id']}/assets?name={name}",
                        token=_token(), data=f.read(), ctype="application/octet-stream")
    if st != 201:
        sys.exit(f"❌ 上传 {tag}/{name} 失败: {st} {resp}")
    print(f"[release] ✅ 上传 {tag}/{name} ({path.stat().st_size / 1e6:.1f}MB)")


def delete_release(tag: str):
    rel = get_release(tag)
    if rel:
        _api("DELETE", rel["url"], token=_token())
        print(f"[release] 已删除超龄温层 {tag}")


def list_releases() -> list:
    _, rels = _api("GET", f"{API_BASE}/releases?per_page=100", token=_opt_token())
    return rels if isinstance(rels, list) else []


# ────────────────────────── 快照 / 归档 ──────────────────────────

def snapshot_db(src: Path) -> Path:
    """backup API 生成一致性副本(WAL 下安全), 并校验完整性。"""
    tmp = Path(tempfile.mkstemp(suffix=".db")[1])
    s = sqlite3.connect(src)
    d = sqlite3.connect(tmp)
    s.backup(d)
    d.close()
    s.close()
    ic = sqlite3.connect(tmp).execute("PRAGMA integrity_check").fetchone()[0]
    if ic != "ok":
        sys.exit(f"❌ 快照完整性校验失败: {ic}")
    return tmp


def db_dates(db: Path) -> list:
    c = sqlite3.connect(db)
    dates = [r[0] for r in c.execute("SELECT DISTINCT crawl_date FROM trades ORDER BY crawl_date")]
    c.close()
    return dates


def filter_dates(db: Path, keep: set):
    """就地只保留指定采集日(players 保留全量, 体积小且被外键引用)。"""
    c = sqlite3.connect(db)
    dates = [r[0] for r in c.execute("SELECT DISTINCT crawl_date FROM trades")]
    c.executemany("DELETE FROM trades WHERE crawl_date=?", [(d,) for d in dates if d not in keep])
    c.executemany("DELETE FROM positions WHERE crawl_date=?", [(d,) for d in dates if d not in keep])
    c.commit()
    c.execute("VACUUM")
    c.commit()
    c.close()


def make_gz(src: Path, dst: Path):
    with open(src, "rb") as f, gzip.open(dst, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)


def make_manifest(db: Path) -> dict:
    c = sqlite3.connect(db)
    lo, hi = c.execute("SELECT MIN(crawl_date), MAX(crawl_date) FROM trades").fetchone()
    m = {
        "integrity_check": "ok",
        "trades": c.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
        "positions": c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
        "players": c.execute("SELECT COUNT(*) FROM players").fetchone()[0],
        "date_range": [lo, hi],
        "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
        "generated_at": date.today().isoformat(),
    }
    c.close()
    return m


def _upload_snapshot(tag: str, asset: str, dates: set = None):
    """快照 → (可选按采集日过滤) → gz + manifest → 上传。"""
    tmp_db = snapshot_db(DB_PATH)
    try:
        if dates is not None:
            filter_dates(tmp_db, dates)
        gz = Path(str(tmp_db) + ".gz")
        make_gz(tmp_db, gz)
        mf = make_manifest(tmp_db)
        mf_path = Path(str(tmp_db) + ".manifest.json")
        mf_path.write_text(json.dumps(mf, ensure_ascii=False, indent=2))
        upload_asset(tag, asset, gz)
        upload_asset(tag, asset.replace(".db.gz", ".manifest.json"), mf_path)
        print(f"[manifest] {tag}: {mf['trades']} trades, {mf['positions']} positions, {mf['date_range']}")
    finally:
        tmp_db.unlink(missing_ok=True)
        Path(str(tmp_db) + ".gz").unlink(missing_ok=True)
        Path(str(tmp_db) + ".manifest.json").unlink(missing_ok=True)


# ────────────────────────── 命令 ──────────────────────────

def cmd_upload_latest():
    _upload_snapshot(HOT_TAG, HOT_ASSET)


def cmd_archive_weeks():
    dates = db_dates(DB_PATH)
    if not dates:
        print("[archive-week] 库为空, 跳过")
        return
    # 库内最新采集日所在 ISO 周
    y, w, _ = date.fromisoformat(dates[-1]).isocalendar()
    tag = f"db-w{y}-W{w:02d}"
    keep = {d for d in dates
            if date.fromisoformat(d).isocalendar()[:2] == (y, w)}
    _upload_snapshot(tag, f"crawl-{y}-W{w:02d}.db.gz", keep)


def cmd_archive_months():
    """把所有"已完成月"(存在下一月采集数据)封存为冷层, 重复执行幂等覆盖。"""
    dates = db_dates(DB_PATH)
    if not dates:
        return
    months = sorted({d[:7] for d in dates})
    last_month = months[-1]
    for m in months:
        if m >= last_month:
            continue  # 当月未封版, 由周层覆盖
        keep = {d for d in dates if d[:7] == m}
        _upload_snapshot(f"db-m{m}", f"crawl-{m}.db.gz", keep)


def cmd_retain_weeks(weeks: int):
    cutoff = date.today() - timedelta(weeks=weeks)
    for rel in list_releases():
        tag = rel.get("tag_name", "")
        if not tag.startswith("db-w"):
            continue
        try:
            y, w = tag[4:].split("-W")
            week_start = date.fromisocalendar(int(y), int(w), 1)
        except ValueError:
            continue
        if week_start < cutoff:
            delete_release(tag)


def cmd_download_latest(dest: Path):
    rel = get_release(HOT_TAG)
    if not rel:
        return 1
    asset = next((a for a in rel.get("assets", []) if a["name"] == HOT_ASSET), None)
    if not asset:
        return 1
    gz = Path(tempfile.mkstemp(suffix=".db.gz")[1])
    req = urllib.request.Request(asset["browser_download_url"],
                                 headers={"User-Agent": "stockboard-release-db"})
    with urllib.request.urlopen(req) as r, open(gz, "wb") as f:
        shutil.copyfileobj(r, f)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gz, "rb") as s, open(dest, "wb") as d:
        shutil.copyfileobj(s, d)
    gz.unlink()
    ic = sqlite3.connect(dest).execute("PRAGMA integrity_check").fetchone()[0]
    if ic != "ok":
        sys.exit(f"❌ 热层恢复后完整性校验失败: {ic}")
    print(f"[download] ✅ {HOT_TAG}/{HOT_ASSET} → {dest} (integrity ok)")
    return 0


def cmd_sync():
    cmd_upload_latest()
    cmd_archive_weeks()
    cmd_archive_months()
    cmd_retain_weeks(12)


def cmd_init():
    cmd_upload_latest()
    cmd_archive_months()
    print("[init] ✅ 热层 + 全部已完成月已上传")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upload-latest", action="store_true")
    ap.add_argument("--archive-weeks", action="store_true")
    ap.add_argument("--archive-months", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--download-latest", action="store_true")
    ap.add_argument("--dest", default=str(DB_PATH), help="--download-latest 目标路径")
    ap.add_argument("--retain-weeks", type=int, metavar="N")
    ap.add_argument("--gz-only", metavar="OUT", help="无 token, 本地生成快照 gz 自检")
    args = ap.parse_args()

    if args.gz_only:
        tmp = snapshot_db(DB_PATH)
        make_gz(tmp, Path(args.gz_only))
        print(json.dumps(make_manifest(tmp), ensure_ascii=False))
        tmp.unlink()
        return
    if args.upload_latest:
        cmd_upload_latest()
    if args.archive_weeks:
        cmd_archive_weeks()
    if args.archive_months:
        cmd_archive_months()
    if args.retain_weeks:
        cmd_retain_weeks(args.retain_weeks)
    if args.sync:
        cmd_sync()
    if args.init:
        cmd_init()
    if args.download_latest:
        raise SystemExit(cmd_download_latest(Path(args.dest)))
    if not any([args.upload_latest, args.archive_weeks, args.archive_months,
                args.sync, args.init, args.download_latest, args.retain_weeks, args.gz_only]):
        ap.print_help()


if __name__ == "__main__":
    main()
