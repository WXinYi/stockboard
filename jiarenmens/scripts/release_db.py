#!/usr/bin/env python3
"""crawl_data.db ↔ GitHub Release 三层存储工具（热/温/冷），见 docs/DATA_PIPELINE.md。

分层:
  热层 tag db-state     资产 crawl-latest.db.gz        最近40采集日全量, 每收盘后覆盖
  温层 tag db-w<ISO周>  资产 crawl-<ISO周>.db.gz       单周归档, 滚动保留 --retain-weeks 周
  冷层 tag db-m<YYYY-MM> 资产 crawl-<YYYY-MM>.db.gz    单月归档, 永久

用法(上传需 GITHUB_TOKEN/GH_TOKEN 环境变量; workflow 内用 secrets.GITHUB_TOKEN):
  python scripts/release_db.py --upload-latest          # 当前 db 快照 → 热层
  python scripts/release_db.py --what auction --upload-latest    # auction.db → 热层 + 当日快照(留7天) + 周冷层(留26周)
  python scripts/release_db.py --archive-weeks          # 库内最近一周 → 温层
  python scripts/release_db.py --archive-months         # 库内"已完成月" → 冷层(永久)
  python scripts/release_db.py --sync                   # 以上三条 + 清理超龄温层(收盘后 run 一次调用)
  python scripts/release_db.py --init                   # 首次迁移: 热层 + 全部已完成月
  python scripts/release_db.py --download-latest        # 拉热层 → data/crawl_data.db (workflow 恢复用)
  python scripts/release_db.py --what auction --download-latest  # auction latest 失败自动回退最新日快照
  python scripts/release_db.py --gz-only /tmp/x.db.gz   # 无 token, 本地生成快照 gz 自检

设计要点:
  - 快照用 sqlite backup API, WAL 下也一致; 上传前 PRAGMA integrity_check
  - 幂等: 同名资产先删后传, 重复执行结果一致
  - 月归档只封"已完成月"(库内存在下一月数据), 当月由周层覆盖
  - auction 周冷层: 当前 ISO 周资产(auction-YYYY-Www.db.gz)复用同一个 gz 随每班覆盖刷新,
    周切换后旧周资产自然冻结为周末状态, 无需按周定时; 滚动保留 26 个 ISO 周(约半年)
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
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

# 数据库目标注册表: crawl_data.db(热/温/冷三层 sync) / auction.db(热层 + 日快照 + 周冷层)
# auction 单独成档原因: 竞价班(auction.yml)/打标班(auction-label.yml)/crawl班 三个 workflow
# 都写它, 且 bid_pool 等竞价时点档案不可重采 —— 每班 sha 变更即传, 不能套 crawl 的收盘闸门。
# auction 保留策略: latest 覆盖写 + 日快照滚动留 daily_keep=7 天(严格< cutoff 才删)
# + ISO 周快照滚动留 weekly_keep=26 周(cutoff 周本身删, 见 _stale_auction_assets)。
TARGETS = {
    "crawl": {"db": DB_PATH, "tag": HOT_TAG, "asset": HOT_ASSET},
    "auction": {"db": ROOT / "data" / "auction.db", "tag": "auction-state",
                "asset": "auction-latest.db.gz", "prefix": "auction",
                "daily_keep": 7, "weekly_keep": 26},
}


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


def _local_fingerprint(src: Path, keep: set) -> list:
    """归档内容指纹: 每个采集日的 trades/positions 行数。

    已完成月/周的内容不会再变(除非回填)——指纹一致即跳过上传,
    避免每晚重传全部历史月份(5年后≈60个月×20MB/晚的纯浪费)。
    """
    c = sqlite3.connect(src)
    q = ",".join("?" * len(keep))
    rows = []
    for table in ("trades", "positions"):
        for d, n in c.execute(
            f"SELECT crawl_date, COUNT(*) FROM {table} WHERE crawl_date IN ({q}) "
            f"GROUP BY crawl_date ORDER BY crawl_date", tuple(sorted(keep))):
            rows.append(f"{table}:{d}={n}")
    c.close()
    return rows


def _remote_fingerprint(tag: str, manifest_asset: str):
    """取远端 manifest 里的指纹; 不存在返回 None。"""
    rel = get_release(tag)
    if not rel:
        return None
    asset = next((a for a in rel.get("assets", []) if a["name"] == manifest_asset), None)
    if not asset:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(
                asset["browser_download_url"],
                headers={"User-Agent": "stockboard-release-db"}), timeout=60) as r:
            return json.loads(r.read()).get("fingerprint")
    except Exception as e:
        print(f"[fingerprint] ⚠️ 读取远端指纹失败({e}), 按需重传", file=sys.stderr)
        return None


def _upload_snapshot(tag: str, asset: str, dates: set = None):
    """快照 → (可选按采集日过滤) → gz + manifest → 上传。

    dates 给定时先比对内容指纹, 与远端一致则跳过(冷层每晚 sync 的关键省流逻辑)。
    """
    keep = dates if dates is not None else set(db_dates(DB_PATH))
    manifest_asset = asset.replace(".db.gz", ".manifest.json")
    fp = _local_fingerprint(DB_PATH, keep)
    if dates is not None and _remote_fingerprint(tag, manifest_asset) == fp:
        print(f"[skip] {tag}: 内容指纹一致({len(fp)}个采集日), 跳过上传")
        return

    tmp_db = snapshot_db(DB_PATH)
    try:
        if dates is not None:
            filter_dates(tmp_db, dates)
        gz = Path(str(tmp_db) + ".gz")
        make_gz(tmp_db, gz)
        mf = make_manifest(tmp_db)
        mf["fingerprint"] = fp
        mf_path = Path(str(tmp_db) + ".manifest.json")
        mf_path.write_text(json.dumps(mf, ensure_ascii=False, indent=2))
        upload_asset(tag, asset, gz)
        upload_asset(tag, manifest_asset, mf_path)
        print(f"[manifest] {tag}: {mf['trades']} trades, {mf['positions']} positions, {mf['date_range']}")
    finally:
        tmp_db.unlink(missing_ok=True)
        Path(str(tmp_db) + ".gz").unlink(missing_ok=True)
        Path(str(tmp_db) + ".manifest.json").unlink(missing_ok=True)


# ────────────────────────── 命令 ──────────────────────────

def cmd_upload_latest():
    _upload_snapshot(HOT_TAG, HOT_ASSET)


def _stale_auction_assets(names: list[str], today: date, daily_keep: int, weekly_keep: int) -> list[str]:
    """纯函数: 资产名列表 + 今天 + 两个保留参数 → 应删除的资产名列表(便于单测)。

    只匹配 auction 前缀的两类快照, 其余(auction-latest / crawl-* / 未知名字)一律不动:
      - 日快照 auction-YYYY-MM-DD.db.gz: 日期严格早于 today - daily_keep 天才删
        —— 恰好等于 cutoff 的保留, 即完整保留最近 daily_keep 天(cutoff 当天仍在保留期)。
      - 周快照 auction-YYYY-Www.db.gz: ISO 年周不晚于 today - weekly_keep 周所在
        ISO 年周即删(cutoff 周本身删) —— 恰好保留最近 weekly_keep 个 ISO 周(含当前周)。
        注意与日快照边界不同: 周资产在周末冻结, 满 weekly_keep 周即出保留期。
    名字形似日期但非法(如 2026-13-45)时宁留勿删。
    """
    day_cutoff = today - timedelta(days=daily_keep)
    week_cutoff = (today - timedelta(weeks=weekly_keep)).isocalendar()[:2]
    stale: list[str] = []
    for n in names:
        m = re.fullmatch(r"auction-(\d{4}-\d{2}-\d{2})\.db\.gz", n)
        if m:
            try:
                d = date.fromisoformat(m.group(1))
            except ValueError:
                continue
            if d < day_cutoff:
                stale.append(n)
            continue
        m = re.fullmatch(r"auction-(\d{4}-W\d{2})\.db\.gz", n)
        if m:
            y, w = m.group(1).split("-W")
            if (int(y), int(w)) <= week_cutoff:
                stale.append(n)
    return stale


def cmd_upload_what(what: str):
    """--what 分发: crawl 走原三层 sync 语义; auction 全量快照直传 + 日/周快照滚动留存。"""
    t = TARGETS[what]
    if what == "crawl":
        cmd_upload_latest()
        return
    tmp_db = snapshot_db(t["db"])
    try:
        gz = Path(str(tmp_db) + ".gz")
        make_gz(tmp_db, gz)
        upload_asset(t["tag"], t["asset"], gz)
        # 日快照 + 当前 ISO 周快照: 热层被后写者覆盖/损坏时, 最近 N 天任意时点与最近
        # N 周的周末状态可回滚。周资产复用同一个 gz、同名覆盖, 每班刷新当前周,
        # 周切换后旧周资产自然冻结为周末状态, 无需按周定时。
        daily = f"{t['prefix']}-{date.today().isoformat()}.db.gz"
        upload_asset(t["tag"], daily, gz)
        y, w, _ = date.today().isocalendar()
        upload_asset(t["tag"], f"{t['prefix']}-{y}-W{w:02d}.db.gz", gz)
        # 滚动清理: 日快照留 daily_keep 天 + 周快照留 weekly_keep 周(纯函数可单测)
        rel = get_release(t["tag"])
        assets = rel.get("assets", []) if rel else []
        stale = set(_stale_auction_assets([a["name"] for a in assets],
                                          date.today(), t["daily_keep"], t["weekly_keep"]))
        for a in assets:
            if a["name"] in stale:
                _api("DELETE", a["url"], token=_token())
                print(f"[cleanup] 已删除超龄快照 {a['name']}")
        print(f"[upload] ✅ {t['tag']}/{t['asset']} + 当日快照 + 当周快照")
    finally:
        tmp_db.unlink(missing_ok=True)
        Path(str(tmp_db) + ".gz").unlink(missing_ok=True)


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


def _download_asset_to_db(tag: str, asset: str, dest: Path, retries: int = 3) -> int:
    """单个 gz 资产 → 解压覆盖 dest。失败返回 1(由调用方决定终止/降级), 网络错误重试。"""
    last_err = ""
    for i in range(retries):
        rel = get_release(tag)
        if not rel:
            return 1
        a = next((x for x in rel.get("assets", []) if x["name"] == asset), None)
        if not a:
            return 1
        gz = Path(tempfile.mkstemp(suffix=".db.gz")[1])
        try:
            req = urllib.request.Request(a["browser_download_url"],
                                         headers={"User-Agent": "stockboard-release-db"})
            with urllib.request.urlopen(req, timeout=120) as r, open(gz, "wb") as f:
                shutil.copyfileobj(r, f)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(gz, "rb") as s, open(dest, "wb") as d:
                shutil.copyfileobj(s, d)
            ic = sqlite3.connect(dest).execute("PRAGMA integrity_check").fetchone()[0]
            if ic != "ok":
                sys.exit(f"❌ 热层恢复后完整性校验失败: {ic}")
            print(f"[download] ✅ {tag}/{asset} → {dest} (integrity ok)")
            return 0
        except Exception as e:   # 网络抖动重试, 不能让单次失败断链
            last_err = str(e)
            print(f"[download] ⚠️ 第{i + 1}次失败: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
        finally:
            gz.unlink(missing_ok=True)
    print(f"[download] ❌ 重试耗尽: {last_err}", file=sys.stderr)
    return 1


def cmd_download_latest(dest: Path = None, retries: int = 3):
    """拉取 crawl 热层覆盖 dest(默认 DB_PATH)。失败返回 1。"""
    return _download_asset_to_db(HOT_TAG, HOT_ASSET, dest or DB_PATH, retries)


def cmd_download_what(what: str, dest: Path = None):
    """--what 分发下载: auction 先 latest, 失败回退最新日期快照(防 latest 被损坏/覆盖丢失)。"""
    t = TARGETS[what]
    if what == "crawl":
        return cmd_download_latest(dest)
    dest = dest or t["db"]
    rel = get_release(t["tag"])
    if not rel:
        print(f"[download] ❌ Release {t['tag']} 不存在", file=sys.stderr)
        return 1
    daily = sorted((a["name"] for a in rel.get("assets", [])
                    if re.fullmatch(rf"{re.escape(t['prefix'])}-\d{{4}}-\d{{2}}-\d{{2}}\.db\.gz", a["name"])),
                   reverse=True)
    for name in [t["asset"]] + daily:
        if _download_asset_to_db(t["tag"], name, dest, retries=2) == 0:
            return 0
    return 1


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
    ap.add_argument("--what", choices=sorted(TARGETS), default="crawl",
                    help="目标库: crawl_data.db(crawl, 默认) / auction.db(auction)")
    ap.add_argument("--upload-latest", action="store_true")
    ap.add_argument("--archive-weeks", action="store_true")
    ap.add_argument("--archive-months", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--download-latest", action="store_true")
    ap.add_argument("--dest", default=None, help="--download-latest 目标路径(默认按 --what 取注册表)")
    ap.add_argument("--retain-weeks", type=int, metavar="N")
    ap.add_argument("--gz-only", metavar="OUT", help="无 token, 本地生成快照 gz 自检")
    args = ap.parse_args()

    if args.gz_only:
        tmp = snapshot_db(TARGETS[args.what]["db"])
        make_gz(tmp, Path(args.gz_only))
        if args.what == "crawl":
            print(json.dumps(make_manifest(tmp), ensure_ascii=False))
        else:
            print(f"[gz-only] {args.what}: {Path(args.gz_only).stat().st_size / 1e6:.1f}MB")
        tmp.unlink()
        return
    if args.upload_latest:
        cmd_upload_what(args.what)
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
        raise SystemExit(cmd_download_what(args.what, Path(args.dest) if args.dest else None))
    if not any([args.upload_latest, args.archive_weeks, args.archive_months,
                args.sync, args.init, args.download_latest, args.retain_weeks, args.gz_only]):
        ap.print_help()


if __name__ == "__main__":
    main()
