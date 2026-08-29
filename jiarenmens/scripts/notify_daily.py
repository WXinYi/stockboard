#!/usr/bin/env python3
"""超短跟单日报（由 crawl.yml 的「钉钉通知」步骤调用，crawl 每天触发 12 次）

一条合并消息覆盖全部 13 名关注选手：
  🧭 环境线(周期引擎) → 🔥 当日共识(≥2人同向) → 13 人逐人卡片(买/卖/持仓/跟随) → ➤ 跟单纪律

增量机制：trades 带 _id，推送后记入 last_notify_state.json；同日无新增操作且已推送过
则跳过（防 12 次 crawl dispatch 重复轰炸）。首条消息列出当日全部操作。

「金额」说明：东财接口无股数/总资产字段，用「成交价@仓位」表达操作规模。
现价/涨幅来自腾讯实时行情（新浪降级）。

环境变量: DINGTALK_URL / DINGTALK_SECRET
用法（在 jiarenmens/ 目录下）:
    python3 scripts/notify_daily.py                     # 正式推送
    python3 scripts/notify_daily.py --date 2026-08-28 --dry-run
"""
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.notify.dingtalk import DingTalk  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "stockboard-app" / "public" / "data" / "latest"
BASE_URL = "https://WXinYi.github.io/stockboard"
STATE_FILE = REPO_ROOT / "jiarenmens" / "data" / "last_notify_state.json"

# 关注名单单一数据源 = main.py 的 WATCHED_PLAYERS（全部选手一个组, 顺序即置顶顺序）
from main import WATCHED_PLAYERS  # noqa: E402
from src.utils import visibility  # noqa: E402
WATCHED = {zh: name for zh, name in WATCHED_PLAYERS}
# 操作风格标签
STYLES = {
    "900456476": "满仓单票每日接力", "900450475": "跨周期中段龙头", "900351276": "高频试错",
    "900401128": "空间锚接力", "900422074": "满仓隔日超短", "900443192": "打板+波段",
    "900315547": "埋伏型", "900240956": "波段", "900438148": "医药波段", "900376763": "高频切换",
    "900013608": "科技埋伏", "900369020": "多线持仓", "900439290": "短线收割",
}
# 回撤警示(周线为负 → 暂停跟单)
PAUSE_IF_WEEK_NEG = {"900443192"}


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": {}}


def save_state(seen: dict, sent_date: str | None = None) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    st = {"seen": {k: sorted(v) for k, v in seen.items()}}
    if sent_date:
        st["sent_date"] = sent_date
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2))


def _player_detail(wid: str, date_str: str):
    """读选手详情 → (当日 trades, 持仓列表)；缺失返回 (None, [])"""
    path = DATA_DIR / "players" / f"{wid}.json"
    if not path.exists():
        return None, []
    d = json.loads(path.read_text())
    trades = [t for t in d.get("t", []) if t.get("td") == date_str]
    return trades, d.get("p", []) or []


# ── 腾讯实时行情（当日涨跌幅, 新浪降级）────────────────────────────
def _market(code: str) -> str:
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _quotes_tencent(codes) -> dict:
    out = {}
    for i in range(0, len(codes), 30):
        symbols = ",".join(f"{_market(c)}{c}" for c in codes[i:i + 30])
        try:
            with urllib.request.urlopen(f"http://qt.gtimg.cn/q={symbols}", timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            m = re.match(r'v_s[hz](\d+)="(.*)"', line.strip())
            if not m:
                continue
            fields = m.group(2).split("~")
            if len(fields) <= 32:
                continue
            try:
                price = float(fields[3])
            except (ValueError, TypeError):
                price = None
            try:
                pct = float(fields[32].strip())
            except (ValueError, TypeError):
                pct = None
            if price is not None:
                out[m.group(1)] = {"price": price, "pct": pct}
    return out


def _quotes_sina(codes) -> dict:
    out = {}
    for i in range(0, len(codes), 30):
        symbols = ",".join(f"{_market(c)}{c}" for c in codes[i:i + 30])
        try:
            req = urllib.request.Request(
                f"http://hq.sinajs.cn/list={symbols}",
                headers={"Referer": "https://finance.sina.com.cn"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            m = re.search(r'hq_str_s[hz](\d+)="(.*)";', line.strip())
            if not m:
                continue
            fields = m.group(2).split(",")
            if len(fields) < 6:
                continue
            try:
                price, prev = float(fields[3]), float(fields[2])
            except (ValueError, TypeError):
                continue
            if price <= 0:
                continue
            out[m.group(1)] = {"price": price,
                               "pct": (price - prev) / prev * 100 if prev > 0 else None}
    return out


def fetch_quotes(codes) -> dict:
    if not codes:
        return {}
    out = _quotes_tencent(codes)
    missing = [c for c in codes if c not in out]
    if missing:
        out.update(_quotes_sina(missing))
    return out


def _player_link(wid: str, name: str) -> str:
    return f"[{name}]({BASE_URL}/#/player/{wid})"


def _stock_link(code: str, name: str) -> str:
    return f"[{name}]({BASE_URL}/#/stock/{code}?name={quote(name)})"


def _side_line(label: str, trades: list, quotes: dict, seen_set: set, first_run: bool,
               new_counter: list) -> str:
    """一行买卖明细: 买: [票](链接) 仓位 @成交价｜现价(+x%)🆕、..."""
    if not trades:
        return f"{label}: 无"
    parts = []
    for t in trades:
        is_new = (not first_run) and t.get("_id") not in seen_set
        if t.get("_id"):
            seen_set.add(t["_id"])
            new_counter[0] += 1 if is_new else 0
        flag = "🆕 " if is_new else ""
        seg = _stock_link(t.get("sc", ""), t.get("sn", "")) + f" {t.get('rr', '') or ''}"
        price = t.get("pr")
        if price:
            seg += f" @{price:.2f}"
        q = quotes.get(t.get("sc", ""))
        if q and q.get("price") is not None:
            seg += f"（现价 {q['price']:.2f}"
            if q.get("pct") is not None:
                seg += f" {q['pct']:+.2f}%"
            seg += "）"
        parts.append(flag + seg)
    return f"{label}: " + "、".join(parts)


def build_follow_report(date_str: str, seen: dict, first_run: bool):
    """合并版跟单日报。返回 (text, new_count, updated_seen)
    组合已隐藏/删除的选手自动跳过(visibility 状态由 watched_flash 每早探测更新)"""
    vis_state = visibility.load()
    hidden = [wid for wid in WATCHED if visibility.is_hidden(wid, vis_state)]
    active = [wid for wid in WATCHED if wid not in hidden]

    updated = {wid: set(seen.get(wid, set())) for wid in active}
    new_counter = [0]

    details = {}
    quote_codes = set()
    for wid in active:
        trades, positions = _player_detail(wid, date_str)
        details[wid] = (trades or [], positions)
        for t in trades or []:
            quote_codes.add(t.get("sc", ""))
        for p in positions:
            quote_codes.add(p.get("sc", ""))
    quotes = fetch_quotes(sorted(c for c in quote_codes if c))

    # ── 环境线(周期引擎, 失败静默) ──
    lines = [f"## 📋 超短跟单日报 · {date_str}"]
    try:
        from src.analysis.emotion_cycle import compute_cycle
        c = compute_cycle(date_str, persist=False)
        m = c["metrics"]
        ml = "/".join(a["board"] for a in c["mainlines"][:3]) or "无"
        lines.append(f"**🧭 环境**: 周期 **{c['stage']}**(置信度 {c['confidence']}/9) · "
                     f"主线 {ml} · 高度 {m['height']}B · 涨停 {m['zt']} 只 · 破板率 {m['broke_rate']}%")
        lines.append(f"**📌 阶段纪律**: {c['playbook']}")
        lines.append("")
    except Exception as e:
        print(f"⚠️ 周期引擎不可用({e}), 日报不含环境线")

    # ── 当日共识(≥2人同向) ──
    buy_cnt, sell_cnt = defaultdict(set), defaultdict(set)
    names = {}
    for wid in active:
        for t in details[wid][0]:
            names[t.get("sc", "")] = t.get("sn", "")
            (buy_cnt if t.get("dr") == "买入" else sell_cnt)[t.get("sc", "")].add(wid)
    lines.append("**🔥 当日共识**")
    hot = [(s, len(v), "🛒") for s, v in buy_cnt.items() if len(v) >= 2] + \
          [(s, len(v), "🏃") for s, v in sell_cnt.items() if len(v) >= 2]
    if hot:
        hot.sort(key=lambda x: -x[1])
        for s, n, ic in hot[:6]:
            who = " / ".join(WATCHED[w] for w in (buy_cnt if ic == "🛒" else sell_cnt)[s])
            lines.append(f"- {ic} **{_stock_link(s, names.get(s, s))}**: {who} {n}人同向")
    else:
        lines.append("- 无 ≥2 人同向的票(单人动作见下)")
    lines.append("")

    # ── 逐人卡片 ──
    for wid in active:
        trades, positions = details[wid]
        nm = WATCHED[wid]
        head = f"**{_player_link(wid, nm)}**"
        p = None
        if trades:
            head += f"（当日 {len(trades)} 笔）"
        lines.append(head)
        if trades is None:
            lines.append("⚠️ 数据缺失")
            lines.append("")
            continue
        buys = [t for t in trades if t.get("dr") == "买入"]
        sells = [t for t in trades if t.get("dr") == "卖出"]
        lines.append(_side_line("买", buys, quotes, updated[wid], first_run, new_counter))
        lines.append(_side_line("卖", sells, quotes, updated[wid], first_run, new_counter))
        lines.append("")

    if hidden:
        lines.append(f"🔇 组合已隐藏(自动跳过, 恢复后自动回归): "
                     + "、".join(WATCHED[w] for w in hidden))
        lines.append("")
    lines.append("---")
    lines.append("**➤ 跟单纪律**")
    lines.append("- 跟随任何选手动作前, 先看次日竞价确认(高开强才跟)")
    lines.append("- 共识≥2人同向才构成信号; 退潮期通知后只看不做")
    text = "\n".join(lines)
    return text, new_counter[0], updated


def main():
    import argparse
    ap = argparse.ArgumentParser(description="超短跟单日报")
    ap.add_argument("--date", help="YYYY-MM-DD(默认取 summary.json)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送不落状态")
    args = ap.parse_args()

    if not args.dry_run and not os.environ.get("DINGTALK_URL"):
        print("缺少 DINGTALK_URL，跳过钉钉通知")
        return 0
    if args.date:
        date_str = args.date
    else:
        s = json.loads((DATA_DIR / "summary.json").read_text())
        date_str = s["date"]

    state = load_state()
    seen = {k: set(v) for k, v in state.get("seen", {}).items()}
    first_run = not STATE_FILE.exists()

    text, new_count, updated = build_follow_report(date_str, seen, first_run)

    if args.dry_run:
        print(text)
        return 0

    # 同日无新增且已推送过 → 跳过(crawl 每天触发 12 次, 防重复轰炸)
    if new_count == 0 and state.get("sent_date") == date_str:
        print(f"无新增操作且今日已推送, 跳过 ({date_str})")
        return 0

    dt = DingTalk()
    dt.send_markdown(f"超短跟单日报 {date_str}", text)
    print(f"✅ 钉钉推送(超短跟单日报) {date_str} (新增 {new_count} 笔)")
    save_state({k: sorted(v) for k, v in updated.items()}, sent_date=date_str)
    print(f"✅ 推送状态已保存: {STATE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
