#!/usr/bin/env python3
"""P1 · EM rtV2 契约哨兵（feature ``player-data-refactor`` / Sprint 1，Story 1.4）。

每日 09:26 与 ``--rank`` 同批，用**浏览器 UA + 无登录 token + 假 clientVersion**
探测 EM rtV2 契约，写 ``today.json.emContract.{ok, checkedAt}``。
``ok=false`` → 下游跳过直连（S4 前端 ``useEmPlayer`` 落地）、全站 🟡、不发版
（PRD-Q4=5A **P1 MVP 硬门槛**）。本 Sprint 交付契约字段 + 成功/失败两分支 +
0 请求契约级验证；前端消费属 S4 范围。

4 项检查（架构 §7.3）：① HTTP 200 ② ``Access-Control-Allow-Origin`` 回显存在
③ **无 token** payload 下 ``code`` 成功 ④ 数据非空。任一失败 → ``ok=false``。

硬约束：产物仅写 /tmp 或 --out 临时目录；``SB_DATA_V2=0`` → no-op kill switch；
``--demo`` / ``--fixtures`` 离线 dry-run 零网络副作用；原子写走 F6 模块。
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.utils.atomic import _atomic_json  # noqa: E402  (F6 原子写)

BJ_TZ = ZoneInfo("Asia/Shanghai")
PROBE_URL = "https://spzhapi.dfcfs.cn/rtV2"
# 浏览器 UA（**非 EMProjJs**）：探测的是浏览器直连契约，不是 APP WebView 契约。
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# 公开占位参数（[A6]）：绝无 ctToken/utToken 登录态，也绝不写进静态 JSON。
FAKE_CLIENT_VERSION = "11.5.5_3.0.0.261105130728749"
PROBE_USER_ID = "6838395742604432"       # 公开标识符（rtV1 榜单可见），非凭据
PROBE_COMBINATION_ID = "900293400"

CHECK_KEYS = ("http200", "cors", "no_token_success", "data_non_empty")
ONLINE_MARKER = "public/data/latest"
DEFAULT_OUT = Path(tempfile.gettempdir()) / "player-data-refactor-s1" / "out"

# M-03 加固：真实生产目录（大小写不敏感 FS 下 PUBLIC/DATA/LATEST 变体亦指向同一 inode）。
PROD_PUBLIC = ROOT.parent / "stockboard-app" / "public"
PROD_LATEST = PROD_PUBLIC / "data" / "latest"

# 离线 dry-run 的成功样例（4 项全真）。
DEMO_CONTRACT = {
    "status": 200,
    "headers": {"Access-Control-Allow-Origin": "*", "Content-Type": "application/json"},
    "body": {"code": 0, "data": {"combinationId": PROBE_COMBINATION_ID, "name": "月儿兰"}},
    "token_present": False,
}


# ═══════════════════════════════════════════
# 公共小工具（S2 由 AQ2=A 统一迁入 src/ 共享模块；S1 各自内联保持自包含）
# ═══════════════════════════════════════════

def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def bj_now():
    return datetime.now(BJ_TZ)


def today_bj():
    return bj_now().strftime("%Y-%m-%d")


def bj_iso():
    return bj_now().replace(microsecond=0).isoformat()


def kill_switch_active():
    return os.environ.get("SB_DATA_V2", "").strip() == "0"


def _canonical(path):
    """M-03：realpath + normcase + casefold，抹平大小写 / symlink / ``..`` / ``//`` / 尾斜杠。

    macOS 的 ``os.path.normcase`` 不做大小写折叠，故必须叠加 ``casefold()`` 才能
    覆盖 ``PUBLIC/DATA/LATEST`` 这类大小写变体绕过。
    """
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


# ═══════════════════════════════════════════
# 契约判定（纯函数，成功/失败两分支均可单测）
# ═══════════════════════════════════════════

def evaluate_probe(result, checked_at):
    """4 项契约检查 → ``{ok, checkedAt, checks}``；任一失败 → ``ok=false``。

    ``result`` 为标准化探测结果：``{status, headers, body, token_present}``。
    ``token_present=True`` 直接判 ``no_token_success=False``（免登录态契约被破坏）。
    """
    headers = {str(k).lower(): v for k, v in (result.get("headers") or {}).items()}
    body = result.get("body") or {}
    checks = {
        "http200": int(result.get("status", 0) or 0) == 200,
        "cors": bool(headers.get("access-control-allow-origin")),
        "no_token_success": (not result.get("token_present", False))
                            and body.get("code") == 0,
        "data_non_empty": bool(body.get("data")),
    }
    return {"ok": all(checks.values()), "checkedAt": checked_at, "checks": checks}


def should_skip_direct_connect(em_contract):
    """**契约级定义**（S4 ``useEmPlayer.js`` 对齐）：``ok=false`` → 跳过直连。

    返回 True 时下游必须发起 **0 次** EM 请求、直接全站 🟡（K9 / N4 / 5A）。
    本函数与 ``today.json.emContract`` 字段同批交付，供 S4 与单测共同引用。
    """
    return not bool((em_contract or {}).get("ok", False))


def write_em_contract(out, contract, date, updated_at):
    """只写 ``today.json.emContract.{ok,checkedAt}``；其它字段原样保留。

    09:26 首次运行时 today.json 尚不存在 → 建最小骨架（emContract 默认
    ``ok=false`` 是 fail-safe 兜底）。11:30/15:15 由 player_snapshot 读取
    git 里的既有值 sticky 保留，不重置为 true（架构 §3.3）。
    """
    doc = read_json(out / "today.json", None) or {
        "date": date, "updatedAt": updated_at, "tradedPlayerIds": []}
    doc["emContract"] = {"ok": bool(contract["ok"]),
                         "checkedAt": contract["checkedAt"]}
    _atomic_json(out / "today.json", doc)
    return doc


# ═══════════════════════════════════════════
# 探测请求 / 离线装载
# ═══════════════════════════════════════════

def send_probe():
    """真实探测（S1 **不启用**）：浏览器 UA + Origin、无 token、假 clientVersion。"""
    import requests
    payload = {"args": {"userId": PROBE_USER_ID,
                        "combinationId": PROBE_COMBINATION_ID},
               "method": "CombinationInfoHandler", "client": "ios",
               "clientType": "cfw", "clientVersion": FAKE_CLIENT_VERSION}
    resp = requests.post(PROBE_URL, json=payload, timeout=8, headers={
        "User-Agent": BROWSER_UA, "Origin": "https://groupwap.eastmoney.com",
        "Content-Type": "application/json", "Accept": "application/json, text/plain, */*"})
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return {"status": resp.status_code, "headers": dict(resp.headers),
            "body": body, "token_present": False}


def load_probe_result(args):
    if args.live:
        return send_probe()                  # S2 接入；S1 零网络副作用
    if args.fixtures:
        return read_json(Path(args.fixtures) / "contract.json", {}) or {}
    if args.demo:
        return dict(DEMO_CONTRACT)
    raise SystemExit("S1 离线 dry-run：需 --demo 或 --fixtures <dir>（真实探测属 S2 接入）")


def build_parser():
    ap = argparse.ArgumentParser(description="P1 EM rtV2 契约哨兵（Sprint 1，产物仅 /tmp）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（拒绝线上路径）")
    ap.add_argument("--fixtures", default="", help="离线 fixture 目录（含 contract.json）")
    ap.add_argument("--demo", action="store_true", help="用内置成功样例离线 dry-run")
    ap.add_argument("--live", action="store_true", help="真实探测（S2 接入，S1 不启用）")
    ap.add_argument("--date", default="", help="覆盖交易日 YYYY-MM-DD（默认北京当日）")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if kill_switch_active():
        print("[SB_DATA_V2=0] kill switch：no-op，未写任何文件")
        return 0
    out = resolve_out(args.out)
    date = args.date or today_bj()
    contract = evaluate_probe(load_probe_result(args), bj_iso())
    doc = write_em_contract(out, contract, date, bj_iso())
    checks = ",".join(f"{k}={'Y' if v else 'N'}" for k, v in contract["checks"].items())
    print(f"emContract.ok={contract['ok']} [{checks}] → today.json → {out}")
    print(f"  {'直连启用(S4)' if not should_skip_direct_connect(doc['emContract']) else 'ok=false → 下游 0 请求 / 全站 🟡(不发版)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
