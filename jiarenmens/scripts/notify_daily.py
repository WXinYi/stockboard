#!/usr/bin/env python3
"""超短跟单日报（由 crawl.yml 的「钉钉通知」步骤调用，crawl 每天触发 12 次）

一条合并消息覆盖全部 13 名关注选手：
  🧭 环境线(周期引擎) → 🔥 当日共识(≥2人同向) → 逐人卡片(每笔调仓一行, 行首买入/卖出,
  行尾「较成交」浮动幅度) → ➤ 跟单纪律

增量机制：每笔调仓带稳定键 _k（export_json 内容哈希；勿用 db 自增 _id——每次重采都
重新分配，曾致每班 run 全部误判新增→重复推送+全标🆕），推送后记入 last_notify_state.json。
🆕 语义 = 与上一条推送的差异：当日首条消息为基线（整批不标，仅记入 state），之后每条
消息只标相对上一条新增的单笔；同日无新增且已推送过则跳过（防 crawl 12 次触发重复轰炸）。

「金额」说明：东财接口无股数/总资产字段，用「成交价@仓位」表达操作规模。
现价来自腾讯实时行情（新浪降级），「较成交」=(现价-成交价)/成交价。

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


def _trade_line(t: dict, quotes: dict, seen_set: set, same_day: bool, new_counter: list) -> str:
    """单笔调仓一行: - 买入 [票](链接) 仓位 @成交价，现价 x.xx 较成交 +x.x% 🆕

    🆕 仅在 same_day(当日已有过推送) 且该笔 _k 未推送过时标记;
    当日首条消息是基线, 整批不标。
    _k = export_json 生成的内容哈希(稳定), 勿用 db _id(每次重采都变号)。
    键一律 str(): 旧 state 残留 int 键, int/str 混存会使 save_state 的 sorted() 抛 TypeError。
    """
    raw = t.get("_k") or t.get("_id")
    key = str(raw) if raw is not None else None
    is_new = same_day and key is not None and key not in seen_set
    if key:
        seen_set.add(key)
        new_counter[0] += 1 if is_new else 0
    dr = t.get("dr") or ""
    label = "买入" if dr == "买入" else ("卖出" if dr == "卖出" else (dr or "操作"))
    seg = label + " " + _stock_link(t.get("sc", ""), t.get("sn", "")) + f" {t.get('rr', '') or ''}"
    price = t.get("pr")
    if price:
        seg += f" @{price:.2f}"
    q = quotes.get(t.get("sc", ""))
    if q and q.get("price") is not None:
        seg += f"，现价 {q['price']:.2f}"
        if price:
            # 较成交价的浮动幅度(买入后盈亏; 卖出后为负=卖在高点)
            seg += f" 较成交 {(q['price'] - price) / price * 100:+.1f}%"
    return f"- {seg}" + (" 🆕" if is_new else "")


def build_follow_report(date_str: str, seen: dict, same_day: bool):
    """合并版跟单日报。返回 (text, new_count, updated_seen)
    组合已隐藏/删除的选手自动跳过(visibility 状态由 watched_flash 每早探测更新)
    same_day=当日已推送过 → 新增单笔标 🆕; 当日首条为基线不标"""
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
        for t in sorted(trades, key=lambda x: x.get("_id") or 0):
            lines.append(_trade_line(t, quotes, updated[wid], same_day, new_counter))
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
    # str() 归一: 兼容旧 state 残留的 int(_id) 键, 避免 int/str 混合集合 sorted() 崩溃
    seen = {k: {str(x) for x in v} for k, v in state.get("seen", {}).items()}
    # 当日首条推送为基线: 不标 🆕(只记录), 之后每条消息相对上一条推送标新增
    first_run = not STATE_FILE.exists()
    same_day = (not first_run) and state.get("sent_date") == date_str

    text, new_count, updated = build_follow_report(date_str, seen, same_day)

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
