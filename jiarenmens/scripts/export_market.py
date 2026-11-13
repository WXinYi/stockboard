#!/usr/bin/env python3
"""P1 · 市场切片导出（feature ``player-data-refactor`` / Sprint 1，Story 1.3）。

用法::

  python scripts/export_market.py --market-only --out /tmp/pdr-s1/out

硬约束：
  * **只读 ``auction.db`` / ``hot_rank.db``**，``ALLOWED_DBS`` 白名单 +
    ``mode=ro`` 硬保证**绝不打开 ``crawl_data.db``**（Story 1.2 AC / 单测断言）。
  * 产物 **FLAT 平铺**在输出目录根（AQ1=A：零路径改动，不迁 ``market/`` 子目录）。
  * **不写 ``auction.json``**（F5：single-writer 归 ``auction.yml``）；
    **不补** ``my_positions`` / ``strike_review`` / ``six_history`` 三个下线切片。
  * 产物仅写 /tmp 或 --out 临时目录；``SB_DATA_V2=0`` → no-op kill switch。
  * 原子写统一走 ``src/utils/atomic.py``（F6）。零网络副作用（纯本地只读 SQLite）。
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.utils.atomic import _atomic_json  # noqa: E402  (F6 原子写)

# 白名单：仅两个市场库。任何其它库名（尤其 crawl_data.db）→ 显式拒绝。
ALLOWED_DBS = ("auction.db", "hot_rank.db")

# 平铺集合锁定（Story 1.2 AC / AQ1=A）：严格 4 个市场切片，不含 auction.json 与 3 下线切片。
MARKET_SLICES = ("lianban_bid.json", "market_breadth.json",
                 "broken_pool.json", "hot_rank.json")
FORBIDDEN_SLICES = ("auction.json", "my_positions.json",
                    "strike_review.json", "six_history.json")

ONLINE_MARKER = "public/data/latest"
DEFAULT_OUT = Path(tempfile.gettempdir()) / "player-data-refactor-s1" / "out"
DEFAULT_DB_DIR = ROOT / "data"

# M-03 加固：真实生产目录（大小写不敏感 FS 下 PUBLIC/DATA/LATEST 变体亦指向同一 inode）。
PROD_PUBLIC = ROOT.parent / "stockboard-app" / "public"
PROD_LATEST = PROD_PUBLIC / "data" / "latest"


# ═══════════════════════════════════════════
# 公共小工具（S2 由 AQ2=A 统一迁入 src/ 共享模块；S1 各自内联保持自包含）
# ═══════════════════════════════════════════

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def today_bj():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def kill_switch_active():
    """``SB_DATA_V2=0`` → no-op kill switch（Epic 4）。"""
    return os.environ.get("SB_DATA_V2", "").strip() == "0"


def _canonical(path):
    """M-03：realpath + normcase + casefold，抹平大小写 / symlink / ``..`` / ``//`` / 尾斜杠。"""
    return os.path.normcase(os.path.realpath(str(path))).casefold()


def _is_production_out(value):
    """M-03：大小写不敏感 + realpath + inode samefile 三层判定，S1 拒绝 ALL 生产路径。"""
    cand = _canonical(Path(value).expanduser().resolve())
    if os.path.normcase(ONLINE_MARKER).casefold() in cand:
        return True
    for base in (_canonical(PROD_LATEST), _canonical(PROD_PUBLIC)):
        if cand == base or cand.startswith(base + os.sep) \
                or base.startswith(cand.rstrip(os.sep) + os.sep):
            return True
    resolved = os.path.realpath(str(Path(value).expanduser().resolve()))
    for prod_path in (PROD_LATEST, PROD_PUBLIC):
        try:
            if os.path.exists(resolved) and os.path.exists(prod_path) \
                    and os.path.samefile(resolved, str(prod_path)):
                return True
        except OSError:
            pass
    return False


def resolve_out(value):
    """S1 硬约束：产物仅 /tmp 或 --out（AQ3=B）；S2 接入时才移除守卫。

    M-03：realpath + normcase + casefold 双向归一化 + inode samefile 兜底，
    覆盖大写变体 / ``..`` / ``//`` / symlink / 相对+尾斜杠等所有绕过形式。
    """
    if _is_production_out(value) and os.environ.get("SB_WRITE_PROD") != "1":
        out = Path(value).expanduser().resolve()
        raise SystemExit(
            f"拒绝写入线上路径 {out}；默认仅允许 /tmp 或 --out 临时目录（AQ3=B）；"
            f"CI 接入需显式设置 SB_WRITE_PROD=1")
    out = Path(value).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def connect_ro(path):
    """只读打开**白名单内**的市场库；越界（如 crawl_data.db）显式抛错。

    这是「18 个普通班不恢复 crawl_data.db」的硬保证：不依赖调用方自觉，
    而在连接层白名单 + ``mode=ro`` 双重拦截（F5 / Story 1.2 AC）。
    """
    path = Path(path)
    if path.name not in ALLOWED_DBS:
        raise ValueError(
            f"--market-only 禁止打开非市场库 {path.name}（仅允许 {ALLOWED_DBS}）")
    if not path.exists():
        raise FileNotFoundError(f"市场库不存在：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query_rows(conn, sql, params=()):
    """查询并转 dict 列表；表缺失/无数据返回空列表（Failure ≠ Fact，不静默崩）。"""
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []


# ═══════════════════════════════════════════
# 4 个平铺切片导出器（数据源仅 auction.db / hot_rank.db）
# ═══════════════════════════════════════════

def export_lianban_bid(conn, date):
    """lianban_bid.json：沿用现有 ``{date, prev_day, top}`` 形状，取 bid_pool 当日 Top5。"""
    rows = query_rows(conn, "SELECT code, name, turnover_ratio FROM bid_pool "
                            "WHERE date=? ORDER BY turnover_ratio DESC LIMIT 5", (date,))
    top = [{"code": r["code"], "name": r["name"],
            "hs": round(float(r.get("turnover_ratio") or 0.0), 2)} for r in rows]
    return {"date": date, "prev_day": None, "top": top, "src": "auction.db"}


def export_market_breadth(conn, date):
    """market_breadth.json：市场宽度最新一行（20 班当日口径）。"""
    rows = query_rows(conn, "SELECT * FROM market_breadth ORDER BY date DESC LIMIT 1")
    return {"date": rows[0]["date"] if rows else date,
            "row": rows[0] if rows else {}, "src": "auction.db"}


def export_broken_pool(conn, date):
    """broken_pool.json：炸板池最新交易日归档。"""
    latest = query_rows(conn, "SELECT MAX(date) AS d FROM broken_pool")
    day = (latest[0]["d"] if latest else None) or date
    rows = query_rows(conn, "SELECT code, name, break_times, change_pct, turnover, height "
                            "FROM broken_pool WHERE date=? ORDER BY code", (day,))
    return {"date": day, "rows": rows, "src": "auction.db"}


def export_hot_rank(conn, date):
    """hot_rank.json：东财人气榜最新 (date, snap) 快照，保留排名。"""
    latest = query_rows(conn, "SELECT MAX(date) AS d FROM hot_rank")
    day = (latest[0]["d"] if latest else None) or date
    snaps = query_rows(conn, "SELECT snap FROM hot_rank WHERE date=? "
                             "ORDER BY snap DESC LIMIT 1", (day,))
    snap = snaps[0]["snap"] if snaps else ""
    rows = query_rows(conn, "SELECT rank, code, name, rise FROM hot_rank "
                            "WHERE date=? AND snap=? ORDER BY rank", (day, snap))
    return {"date": day, "snap": snap, "rows": rows, "src": "hot_rank.db"}


# ═══════════════════════════════════════════
# 子命令
# ═══════════════════════════════════════════

def cmd_market_only(out, db_dir, date):
    """只读两个市场库 → 4 个 FLAT 切片（AQ1=A 零路径改动 / F5 不写 auction.json）。"""
    auction = connect_ro(Path(db_dir) / "auction.db")
    hot = connect_ro(Path(db_dir) / "hot_rank.db")
    try:
        docs = {
            "lianban_bid.json": export_lianban_bid(auction, date),
            "market_breadth.json": export_market_breadth(auction, date),
            "broken_pool.json": export_broken_pool(auction, date),
            "hot_rank.json": export_hot_rank(hot, date),
        }
    finally:
        auction.close()
        hot.close()
    for name in MARKET_SLICES:
        _atomic_json(out / name, docs[name])   # 平铺：out 根，无子目录
    return sorted(docs)


def build_parser():
    ap = argparse.ArgumentParser(description="P1 市场切片导出（Sprint 1，产物仅 /tmp）")
    ap.add_argument("--market-only", action="store_true", required=True,
                    help="只读 auction.db / hot_rank.db，导出平铺市场切片")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（拒绝线上路径）")
    ap.add_argument("--db-dir", default=str(DEFAULT_DB_DIR), help="市场库目录")
    ap.add_argument("--date", default="", help="覆盖交易日 YYYY-MM-DD（默认北京当日）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if kill_switch_active():
        print("[SB_DATA_V2=0] kill switch：no-op，未写任何文件")
        return 0
    out = resolve_out(args.out)
    date = args.date or today_bj()
    written = cmd_market_only(out, Path(args.db_dir), date)
    print(f"market-only → 平铺 {len(written)} 切片 {written} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
