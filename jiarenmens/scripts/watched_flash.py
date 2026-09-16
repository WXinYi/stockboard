#!/usr/bin/env python3
"""
竞价跟单快报(09:26, auction.yml 第一步):
只拉 13 名关注选手的组合接口(秒级, 不做全量采集) → 竞价阶段成交 + 最新持仓 → 钉钉。
全量采集与完整跟单日报仍由 09:30 的 crawl 流程完成, 本快报无状态不落库。

用法:
  python scripts/watched_flash.py                 # 推钉钉(交易日 09:26 由 auction.yml 调)
  python scripts/watched_flash.py --date 2026-08-28 --dry-run
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from main import WATCHED_PLAYERS  # noqa: E402
from src.spiders.api_detail import crawl_player_all_data  # noqa: E402
from src.notify.dingtalk import DingTalk  # noqa: E402
from src.utils import visibility  # noqa: E402

BASE_URL = "https://WXinYi.github.io/stockboard"
BJ_TZ = ZoneInfo("Asia/Shanghai")


def _stock_link(code: str, name: str) -> str:
    if not code:
        return name or "?"
    return f"[{name}]({BASE_URL}/#/stock/{code}?name={quote(name)})"


def fetch_all(date_str: str):
    """串行拉 13 名选手组合接口 → {zh: {name, trades_today, positions, ok}}"""
    out = {}
    for zh, nm in WATCHED_PLAYERS:
        try:
            detail, positions, trades = crawl_player_all_data(zh)
            today = [t for t in trades if t.get("trade_date") == date_str]
            out[zh] = {"name": nm, "trades": today, "positions": positions,
                       "ok": detail is not None}
        except Exception as e:
            # 网络等瞬时异常不算"隐藏"(隐藏由接口返回空判定), 明天自动重探
            out[zh] = {"name": nm, "trades": [], "positions": [], "ok": True, "err": str(e)}
    return out


def build_markdown(date_str: str, data: dict, hhmm: str) -> str:
    lines = [f"## 🔍 竞价跟单快报 · {date_str} {hhmm}",
             "竞价阶段成交如下(9:30 全量采集后的《跟单日报》为准)", ""]
    act, quiet, hidden = [], [], []
    for zh, nm in WATCHED_PLAYERS:
        o = data.get(zh, {})
        if not o.get("ok", True):
            hidden.append(nm)          # 组合已隐藏/删除 → 自动跳过, 恢复后自动回归
            continue
        if o.get("err"):
            quiet.append(f"{nm}(拉取失败)")
            continue
        trades = o.get("trades", [])
        if trades:
            act.append((zh, nm, o))
        else:
            quiet.append(nm)

    if act:
        for zh, nm, o in act:
            buys = [t for t in o["trades"] if t.get("direction") == "买入"]
            sells = [t for t in o["trades"] if t.get("direction") == "卖出"]
            bs = "、".join(_stock_link(t.get("stock_code", ""), t.get("stock_name", "")) +
                           (f" {t.get('position_ratio', '')}" if t.get("position_ratio") else "")
                           for t in buys) or "无"
            ss = "、".join(_stock_link(t.get("stock_code", ""), t.get("stock_name", "")) +
                           (f" {t.get('position_ratio', '')}" if t.get("position_ratio") else "")
                           for t in sells) or "无"
            lines.append(f"**[{nm}]({BASE_URL}/#/player/{zh})**")
            lines.append(f"🛒 买: {bs}")
            lines.append(f"🏃🏻‍♂️ 卖: {ss}")
            lines.append("")
    else:
        # 全部拉取失败 ≠ 全部无成交(2026-09-15 静默审计: 原文案把"接口全挂"说成"均无成交",
        # 是当天最容易误读成"今天没人动手"的一句假信号)
        fetched_ok = len(WATCHED_PLAYERS) - len(hidden) - sum(
            1 for zh, _ in WATCHED_PLAYERS if data.get(zh, {}).get("err"))
        n_shown = len(WATCHED_PLAYERS) - len(hidden)
        n_fail = sum(1 for zh, _ in WATCHED_PLAYERS if data.get(zh, {}).get("err"))
        if fetched_ok <= 0:
            lines.append(f"⚠️ {n_shown} 名关注选手全部拉取失败, "
                         "本次快报无法判定竞价成交 —— 请勿读作「无成交」。")
        elif n_fail:
            # 部分失败时不能下"均无成交"的结论: 只能说出"已取到的都没成交"
            lines.append(f"竞价阶段未见成交(其中 {n_fail} 名拉取失败, 结论不完整)。")
        else:
            lines.append(f"{n_shown} 名关注选手竞价阶段均无成交。")
        lines.append("")
    if quiet:
        lines.append(f"💤 无成交: {'、'.join(quiet)}")
        lines.append("")
    if hidden:
        lines.append(f"🔇 组合已隐藏(自动跳过, 恢复后自动回归): {'、'.join(hidden)}")
        lines.append("")
    lines.append("— 完整持仓/浮盈见 09:30 采集后的《超短跟单日报》")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="竞价跟单快报")
    ap.add_argument("--date", help="YYYY-MM-DD(默认今天)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送")
    ap.add_argument("--fail-flag", default="/tmp/watched_flash_failed",
                    help="推送失败写标志文件(workflow 在数据步骤后再判红, 不阻断竞价主链路)")
    args = ap.parse_args()

    date_str = args.date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    hhmm = datetime.now(BJ_TZ).strftime("%H:%M")
    data = fetch_all(date_str)
    # 可见性状态更新: 接口返回空的选手记为隐藏, 恢复公开自动回归(跟单日报同读此状态)
    visibility.update({zh: not o.get("ok", True) for zh, o in data.items()})
    text = build_markdown(date_str, data, hhmm)

    if args.dry_run:
        print(text)
        return 0
    try:
        DingTalk().send_markdown(f"竞价跟单快报 {date_str}", text)
        print(f"✅ 竞价跟单快报已推送 ({date_str})")
    except Exception as e:
        # 推送没送到必须可见: 写标志文件 + 非零退出, 由 workflow 末尾判红(不阻断竞价落库)
        try:
            Path(args.fail_flag).write_text(f"竞价跟单快报推送失败: {e}\n", encoding="utf-8")
        except Exception:
            pass
        print(f"❌ 竞价跟单快报推送失败: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
