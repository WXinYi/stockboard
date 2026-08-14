#!/usr/bin/env python3
"""每日钉钉通知（由 crawl.yml 的「钉钉通知」步骤调用）

读 stockboard-app/public/data/latest/*.json，发送两条 markdown：
  1. 「StockBoard 日期」—— 特别关注(置顶9人)调仓 + 周榜前5有调仓
  2. 「龙头战法 日期」—— 独立一条消息：4 名龙头战法跟踪选手今日操作
     （含成交价@仓位 + 实时当日涨幅）+ 操作风格说明

「金额」说明：东财接口 tradeSummary/position 无股数与总资产字段，
精确成交金额不可得，故用「成交价@仓位」表达操作规模。
「当下涨幅」来自腾讯实时行情（qt.gtimg.cn，当日涨跌%）。

环境变量: DINGTALK_URL / DINGTALK_SECRET（与 crawl.yml 一致）
用法（在 jiarenmens/ 目录下）:
    python3 scripts/notify_daily.py
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.notify.dingtalk import DingTalk  # noqa: E402

DATA_DIR = (Path(__file__).resolve().parents[2] / "stockboard-app" / "public" / "data" / "latest")
REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://WXinYi.github.io/stockboard"

# 推送状态（记录上次已推送的操作 _id，用于对比"本次新增"）
STATE_FILE = REPO_ROOT / "jiarenmens" / "data" / "last_notify_state.json"


def load_state() -> dict:
    """上次推送状态 → {"seen": {player_id: [id, ...]}}；无状态返回空"""
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": {}}


def save_state(seen: dict) -> None:
    """写回推送状态（seen: {player_id: sorted [id,...]}）"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"seen": seen}, ensure_ascii=False, indent=2))

# 置顶特别关注（原 crawl.yml 内联逻辑，保持行为不变）
WATCHED = {
    "900240956": "股得猫咛",
    "900354116": "不服输不认命MG",
    "900438148": "我嘚财富",
    "900376763": "东之福气娟子",
    "900013608": "wxm1988蒙",
    "900429191": "涛哥啦",
    "900369020": "年年纳福地风清扬",
    "900223455": "鑫泰和周星星",
    "900372673": "五年内财富自由",
}

# 龙头战法跟踪选手: id -> (名称, 操作风格标签)
DRAGON = {
    "900315547": ("西门星辰啊", "埋伏型龙头"),
    "900428477": ("多多易战", "纯龙头接力"),
    "900351276": ("新生代柚子04", "重仓龙头+小仓试错"),
    "900018239": ("新缘众妙之门", "短线接力"),
}


def _player_link(wid: str, name: str) -> str:
    return f"[{name}]({BASE_URL}/#/player/{wid})"


def _today_trades(wid: str, date_str: str):
    """读选手详情，返回当日原始调仓记录列表；详情缺失返回 None"""
    detail_path = DATA_DIR / "players" / f"{wid}.json"
    if not detail_path.exists():
        return None
    d = json.loads(detail_path.read_text())
    return [t for t in d.get("t", []) if t.get("td") == date_str]


# ── 腾讯实时行情（当日涨跌幅）────────────────────────────
def _market(code: str) -> str:
    """A股代码 → 市场前缀（sh/sz/bj）"""
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(("4", "8")):
        return "bj"
    return "sz"


def _quotes_tencent(codes) -> dict:
    """腾讯行情 → {code: {"price": 现价, "pct": 涨跌%}}"""
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
    """新浪行情降级 → 同上结构（涨跌%由现价/昨收推算）"""
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
                price = float(fields[3])
                prev = float(fields[2])
            except (ValueError, TypeError):
                continue
            if price <= 0:
                continue
            pct = (price - prev) / prev * 100 if prev > 0 else None
            out[m.group(1)] = {"price": price, "pct": pct}
    return out


def fetch_quotes(codes) -> dict:
    """行情查询（腾讯为主，新浪降级）→ {code: {price, pct}}；失败返回 {}（不阻塞推送）"""
    if not codes:
        return {}
    out = _quotes_tencent(codes)
    missing = [c for c in codes if c not in out]
    if missing:
        out.update(_quotes_sina(missing))
    return out


def _stock_link(code: str, name: str) -> str:
    """股票 → 看板股票详情页链接（/stock/:code，StockTab 同款）"""
    return f"[{name}]({BASE_URL}/#/stock/{code}?name={quote(name)})"


def _op_line(t: dict, quotes: dict, is_buy: bool, is_new: bool = False) -> str:
    """单笔操作行：方向 股票 仓位｜成交价｜现价｜当前涨幅（每值带字段名）；新操作行首加 🆕"""
    emoji = "🟢 买入" if is_buy else "🔴 卖出"
    rr = t.get("rr", "") or ""
    name = t.get("sn", "") or ""
    head = f"{_stock_link(t.get('sc', ''), name)} {rr}" if rr else _stock_link(t.get("sc", ""), name)
    flag = "🆕 " if is_new else ""
    head = flag + head
    cols = []
    price = t.get("pr")
    if price:
        cols.append(f"成交价 {price:.2f}")
    q = quotes.get(t.get("sc", ""))
    if q and q.get("price") is not None:
        cols.append(f"现价 {q['price']:.2f}")
        if q.get("pct") is not None:
            cols.append(f"当前涨幅 {q['pct']:+.2f}%")
    return f"{emoji} {head}" + ("｜" + "｜".join(cols) if cols else "")


def build_watched(date_str: str, crawl_time: str) -> str:
    """原「特别关注」消息体（保持原内联逻辑：仅股票名，不带仓位）"""
    watched_lines = []
    for wid, wname in WATCHED.items():
        link = _player_link(wid, wname)
        trades = _today_trades(wid, date_str)
        if trades is not None:
            buys = [t["sn"] for t in trades if t.get("dr") == "买入"]
            sells = [t["sn"] for t in trades if t.get("dr") == "卖出"]
            parts = []
            if buys:
                parts.append(f"买入 {', '.join(buys)}")
            if sells:
                parts.append(f"卖出 {', '.join(sells)}")
            watched_lines.append(f"{link}: {' | '.join(parts)}" if parts else f"{link}: 今日无调仓")
        else:
            watched_lines.append(f"{link}: —")

    # 周榜前5中有调仓的（players_index.json: [id,name,followers,T,d,w,m,y,v,dd,wr,dy,lb,rk,tp,q,ss]）
    s = json.loads((DATA_DIR / "summary.json").read_text())
    traded_set = set(s.get("tradedPlayerIds", []))
    players_idx = json.loads((DATA_DIR / "players_index.json").read_text())
    weekly_top = sorted(players_idx, key=lambda p: p[6] or 0, reverse=True)[:5]  # p[6]=weekly_return
    top_traded = [p for p in weekly_top if p[0] in traded_set]  # p[0]=id
    if top_traded:
        top_links = ["、".join(_player_link(p[0], p[1]) for p in top_traded)]  # p[1]=name
        top5_text = "".join(top_links) + " 有调仓"
    else:
        top5_text = "今日均无调仓"

    return (
        f"## 📊 东财实盘排行榜已更新\n\n"
        f"> 采集时间: {crawl_time}\n\n"
        f"---\n\n"
        f"### ⭐ 特别关注\n\n"
        + "\n\n".join(watched_lines)
        + f"\n\n---\n\n"
        f"### 🏆 周榜前5\n\n"
        f"{top5_text}\n\n"
        f"---\n\n"
        f"📈 [打开看板](https://WXinYi.github.io/stockboard/)"
    )


def build_dragon(date_str: str, crawl_time: str, seen: dict = None):
    """短线选手跟踪消息体（单独一条）：操作 + 成交价/涨幅 + 风格说明 + 新增标识

    seen: {"player_id": set(已推送 _id)}。返回 (text, new_count, updated_seen)。
    """
    seen = seen or {}
    first_run = not STATE_FILE.exists()  # 首次推送无上次基线，全部不标 🆕
    per_player = []
    all_codes = set()
    for wid, (wname, style) in DRAGON.items():
        trades = _today_trades(wid, date_str)
        per_player.append((wid, wname, style, trades))
        if trades:
            all_codes.update(t["sc"] for t in trades if t.get("sc"))
    quotes = fetch_quotes(sorted(all_codes)) if all_codes else {}
    live_note = "  ·  现价/涨幅为实时行情" if quotes else ""

    updated = {wid: set(seen.get(wid, set())) for wid in DRAGON}
    dragon_lines = []
    new_count = 0
    for wid, wname, style, trades in per_player:
        link = _player_link(wid, wname)
        if trades is None:
            dragon_lines.append(f"▸ {link} · {style}\n—（详情缺失）")
            continue
        buys = [t for t in trades if t.get("dr") == "买入"]
        sells = [t for t in trades if t.get("dr") == "卖出"]
        parts = []
        for t in buys + sells:
            if t.get("_id"):
                updated[wid].add(t["_id"])
            is_new = (not first_run) and t.get("_id") not in seen.get(wid, set())
            if is_new:
                new_count += 1
            parts.append(_op_line(t, quotes, t.get("dr") == "买入", is_new))
        if not parts:
            parts = ["⚪ 今日无调仓"]
        # 列表项强制每笔单独一行（钉钉 markdown 单换行会被合并）
        op_lines = "\n".join(f"- {p}" for p in parts)
        dragon_lines.append(f"▸ {link} · {style}\n" + op_lines)

    new_note = f"  ·  🆕 本次新增 {new_count} 笔" if new_count else ""
    text = (
        f"## 🐉 短线选手跟踪 · 成交日 {date_str}\n\n"
        f"> 采集 {crawl_time}{live_note}{new_note}\n\n"
        + "\n\n".join(dragon_lines)
        + f"\n\n📈 [打开看板](https://WXinYi.github.io/stockboard/)"
    )
    updated_seen = {k: sorted(v) for k, v in updated.items()}
    return text, new_count, updated_seen


def main():
    if not os.environ.get("DINGTALK_URL"):
        print("缺少 DINGTALK_URL，跳过钉钉通知")
        return 0
    s = json.loads((DATA_DIR / "summary.json").read_text())
    date_str = s["date"]
    crawl_time = s.get("crawl_time", "")

    dt = DingTalk()
    dt.send_markdown(f"StockBoard {date_str}", build_watched(date_str, crawl_time))
    print(f"✅ 钉钉通知(特别关注) {date_str}")

    state = load_state()
    seen = {k: set(v) for k, v in state.get("seen", {}).items()}
    text, new_count, updated_seen = build_dragon(date_str, crawl_time, seen)
    dt.send_markdown(f"短线选手 {date_str}", text)
    print(f"✅ 钉钉通知(短线选手) {date_str} (新增 {new_count} 笔)")

    # 推送成功后写回状态（供下次对比），随 crawl.yml「提交数据」步骤提交
    save_state(updated_seen)
    print(f"✅ 推送状态已保存: {STATE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
