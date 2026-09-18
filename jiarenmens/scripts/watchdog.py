#!/usr/bin/env python3
"""盯盘 watchdog（交易时段每 5 分钟由 cron-job.org → repository_dispatch:watchdog 触发）

拉关注选手组合接口（秒级/人）→ 与当日已推状态比对 → 新操作即时推钉钉，
同时叠加更新该选手 players/{zh}.json 并轻量部署 Pages（详情页即开即最新）。

设计要点（详见 docs/DATA_PIPELINE.md「盯盘 watchdog」一节）:
- 东财调仓明细只有日粒度(tzrq 无盘中时间戳) → "即时" = 当日该笔"首次出现/成交次数
  增加"的时刻, 推送里写首次发现时刻而非成交时刻;
- 去重键 = (zh, code, 方向) + 成交次数: 次数变 → 补推"追加"(修掉日内回买/加量的盲区);
- 状态放 actions/cache 按日滚动(watchdog-state-*, 本文件路径见 STATE_FILE);
  日报 notify_daily.load_watchdog_pushed() 读同一份缓存, 跳过已即时推送的操作
  → 一条操作只响一次铃; watchdog 失灵时日报自动兜底(读不到缓存 = 空集 = 全量推);
- 叠加更新以"线上已部署的选手文件"为底稿(积累历史一行不丢, 东财数据不全不构成风险),
  只刷 p(实时持仓) + 今日 t; 不产生 git 提交(下一班 crawl 的权威导出自愈覆盖);
- 连续 MAX_FAILS 班全员拉取失败 → 主动推"盯盘已降级"告警(不可无声断档), 任一班成功即复位;
- 隐藏选手只读 visibility.is_hidden 跳过, 不回写状态(回写归快报/日报)。

用法(在 jiarenmens/ 目录):
    python scripts/watchdog.py                     # 正式: 检测+推送+叠加
    python scripts/watchdog.py --dry-run           # 只打印会推什么/会改谁, 不落任何状态
    python scripts/watchdog.py --date 2026-09-17 --dry-run   # 回放指定日期
    python scripts/watchdog.py --force             # 无视交易时段闸门(排障用)
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import date as _date
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from main import WATCHED_PLAYERS  # noqa: E402
from src.spiders.api_detail import crawl_player_all_data  # noqa: E402
from src.notify.dingtalk import DingTalk  # noqa: E402
from src.utils import visibility  # noqa: E402
from notify_daily import fetch_quotes  # noqa: E402 (复用腾讯→新浪行情链)

BASE_URL = "https://WXinYi.github.io/stockboard"
BJ = ZoneInfo("Asia/Shanghai")
STATE_FILE = ROOT / "data" / ".watchdog_state.json"
CHANGED_FILE = ROOT / "data" / ".watchdog_changed"
PLAYER_DIR = ROOT.parent / "stockboard-app" / "public" / "data" / "latest" / "players"
TRADING_CAL_JS = ROOT.parent / "stockboard-app" / "src" / "utils" / "tradingCalendar.js"
WINDOW_START, WINDOW_END = "0931", "1505"   # 09:31 起(竞价段由 09:26 快报覆盖, 避免开盘重复响铃); 15:05 收盘班前停
MAX_FAILS = 3                                # 连续 N 班全员失败 → 降级自曝

_HOLIDAY_CACHE: dict | None = None


def parse_holidays(js_text: str) -> dict:
    """解析 tradingCalendar.js → {年份: Set('YYYY-MM-DD')}。单一数据源: 法定节假日
    只维护在前端这份日历里(上交所通知), 此处运行时读取, 避免双份日历漂移。"""
    out = {}
    for m in re.finditer(r"const\s+HOLIDAYS_(\d{4})\s*=\s*new Set\(\[([^\]]*)\]", js_text):
        year = int(m.group(1))
        out[year] = set(re.findall(r"'(\d{4}-\d{2}-\d{2})'", m.group(2)))
    return out


def _holidays() -> dict:
    global _HOLIDAY_CACHE
    if _HOLIDAY_CACHE is None:
        try:
            _HOLIDAY_CACHE = parse_holidays(TRADING_CAL_JS.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"⚠️ 交易日历读取失败({e}), 退回周一~周五近似")
            _HOLIDAY_CACHE = {}
    return _HOLIDAY_CACHE


def is_trading_day(d: _date) -> bool:
    """周末 + 法定节假日休市; 日历未覆盖的年份退回周末近似(与前端 tradingCalendar.js 同语义)。"""
    if d.weekday() >= 5:
        return False
    hol = _holidays().get(d.year)
    return d.isoformat() not in hol if hol is not None else True


def bj_now() -> datetime:
    return datetime.now(BJ)


def in_trading_window(now: datetime) -> bool:
    """交易日 + 盘中时段(午休 11:30~13:00 不开机, 免空烧); 法定节假日不开机, 免误触降级告警。"""
    if not is_trading_day(now.date()):
        return False
    hm = now.strftime("%H%M")
    if "1130" <= hm < "1300":          # 午休: A股 11:30-13:00 停牌, 期间无新操作
        return False
    return WINDOW_START <= hm <= WINDOW_END


def fresh_state(date_str: str) -> dict:
    return {"date": date_str, "pushed": {}, "fails": 0, "alerted": 0}


def load_state(date_str: str, path: Path = STATE_FILE) -> dict:
    """跨日翻篇: 状态的 date 不是今天 → 全新状态(昨天的已推集合不带入)。"""
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        st = None
    if not isinstance(st, dict) or st.get("date") != date_str:
        return fresh_state(date_str)
    st.setdefault("pushed", {})
    st.setdefault("fails", 0)
    st.setdefault("alerted", 0)
    return st


def save_state(st: dict, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def decide_pushes(trades_today: list, pushed_player: dict):
    """按 (code, 方向) 比对当日已推集合 → (new_list, known_list)。

    pushed_player = pushed[zh] 引用 {code: {方向: 成交次数}}, 原地更新。
    new_list = [(trade, is_add)]: 首次出现(is_add=False)或次数增加(补量/回买, is_add=True),
    需要本轮推送; known_list = 次数不变的(已在早前班次推送过, 消息里不带 🆕 仅作台账展示)。"""
    new, known = [], []
    for t in trades_today:
        code = t.get("stock_code", "")
        dr = t.get("direction", "")
        try:
            cnt = int(t.get("trades_count") or 1)
        except (TypeError, ValueError):
            cnt = 1
        by_dr = pushed_player.setdefault(code, {})
        prev = by_dr.get(dr)
        if prev is None:
            new.append((t, False))
        elif cnt > prev:
            new.append((t, True))
        else:
            known.append(t)
        by_dr[dr] = max(prev or 0, cnt)
    return new, known


def seed_baseline(pushed_player: dict, base_file: dict, date_str: str) -> int:
    """首班基线: 线上已部署快照里"今天已有的操作"视为已推送过(防与早班日报重复)。"""
    n = 0
    for t in base_file.get("t") or []:
        if t.get("td") != date_str or t.get("dr") not in ("买入", "卖出"):
            continue
        try:
            cnt = int(t.get("tc") or 1)
        except (TypeError, ValueError):
            cnt = 1
        by_dr = pushed_player.setdefault(t.get("sc", ""), {})
        by_dr[t["dr"]] = max(by_dr.get(t["dr"]) or 0, cnt)
        n += 1
    return n


def fetch_base_player(zh_id: str, retries: int = 3):
    """拉线上已部署的选手文件作叠加底稿; 失败返回 None(宁可跳过叠加, 不可丢历史)。"""
    url = f"{BASE_URL}/data/latest/players/{zh_id}.json"
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:          # 大陆网络对 Pages 常见 SSL 瞬断, 退避重试
            last = e
            time.sleep(1 + i)
    print(f"  ⚠️ {zh_id} 线上底稿拉取失败({retries}次), 跳过叠加: {last}")
    return None


def merge_player(base: dict, positions: list, trades_today: list, date_str: str) -> dict:
    """叠加: 历史调仓(含 _k/_id)原样保留, 只替换 p(实时持仓)与今日 t。"""
    p = [{"sn": x.get("stock_name", ""), "sc": x.get("stock_code", ""),
          "cp": x.get("cost_price"), "np": x.get("current_price"),
          "pr": x.get("profit_ratio"), "rr": x.get("position_ratio")}
         for x in positions]
    fresh = [{"td": date_str, "dr": x.get("direction", ""),
              "sn": x.get("stock_name", ""), "sc": x.get("stock_code", ""),
              "tc": x.get("trades_count"), "rr": x.get("position_ratio"),
              "pr": x.get("price")} for x in trades_today]
    old = [x for x in (base.get("t") or []) if x.get("td") != date_str]
    t = sorted(old + fresh, key=lambda x: x.get("td") or "", reverse=True)
    return {"id": base.get("id"), "name": base.get("name"),
            "p": p, "t": t, "i": base.get("i") or []}


def write_player_json(zh_id: str, detail: dict) -> None:
    PLAYER_DIR.mkdir(parents=True, exist_ok=True)
    dst = PLAYER_DIR / f"{zh_id}.json"
    tmp = dst.with_suffix(".tmp")
    tmp.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dst)


def fetch_all(players, date_str: str) -> dict:
    out = {}
    for zh, nm in players:
        if visibility.is_hidden(zh):
            out[zh] = {"name": nm, "hidden": True}
            continue
        try:
            detail, positions, trades = crawl_player_all_data(zh)
            today = [t for t in trades if t.get("trade_date") == date_str]
            out[zh] = {"name": nm, "trades": today, "positions": positions,
                       "ok": detail is not None}
        except Exception as e:
            # 瞬时异常不判"隐藏"; 计入本轮失败数(全员失败才累计降级)
            out[zh] = {"name": nm, "trades": [], "positions": [], "ok": True, "err": str(e)}
    return out


def build_message(date_str: str, hhmm: str, data: dict, new_mark: dict, quotes: dict) -> str:
    """全员关注列表格式(快报同款): 有今日操作的选手逐个出卡, 本轮新增标 🆕(次数增加标 ↩️追加),
    已推送过的不标; 无操作/拉取失败/已隐藏分别汇总。仅在有新增操作时被调用。"""
    from urllib.parse import quote
    lines = [f"## 🚨 盯盘提醒 · {date_str} {hhmm}", ""]
    quiet, fails, hidden = [], [], []
    for zh, nm in WATCHED_PLAYERS:
        o = data.get(zh) or {}
        if o.get("hidden"):
            hidden.append(nm)
        elif o.get("err") or not o.get("ok", True):
            fails.append(nm)
        elif not (o.get("trades") or []):
            quiet.append(nm)

    cur = None
    for zh, nm in WATCHED_PLAYERS:
        o = data.get(zh) or {}
        trades_today = o.get("trades") or []
        if o.get("hidden") or o.get("err") or not o.get("ok", True) or not trades_today:
            continue
        marks = new_mark.get(zh, {})
        if zh != cur:
            if cur is not None:
                lines.append("")   # 选手块之间必须空行: 钉钉渲染会把下个名字贴在上块尾部
            head = f"**[{nm}]({BASE_URL}/#/player/{zh})**"
            if any(id(t) in marks for t in trades_today):
                head += " 🆕"
            lines.append(head)
            cur = zh
        for t in trades_today:
            is_new = id(t) in marks
            is_add = marks.get(id(t), False)
            dr = t.get("direction", "")
            label = ("↩️追加" if is_add else "") + ("买入" if dr == "买入" else "卖出")
            sname, scode = t.get("stock_name", "?"), t.get("stock_code", "")
            seg = f"- {'🆕 ' if is_new else ''}{label} [{sname}]({BASE_URL}/#/stock/{scode}?name={quote(sname)})"
            rr = t.get("position_ratio")
            if rr:
                seg += f" {rr}"
            price = t.get("price")
            if price:
                seg += f" @{float(price):.2f}"
            q = quotes.get(t.get("stock_code", ""))
            if q and q.get("price") is not None:
                seg += f"，现价 {q['price']:.2f}"
                if price:
                    seg += f" 较成交 {(q['price'] - price) / price * 100:+.1f}%"
            lines.append(seg)
        lines.append("")
    if quiet:
        lines += [f"💤 今日暂无操作: {'、'.join(quiet)}", ""]
    if fails:
        lines += [f"⚠️ 拉取失败(下一班自动重试): {'、'.join(fails)}", ""]
    if hidden:
        lines += [f"🔇 组合已隐藏(自动跳过, 恢复公开自动回归): {'、'.join(hidden)}", ""]
    lines += ["— 盯盘 5 分钟一轮(明细只有日粒度, 时刻=首次发现); "
              "持仓浮盈见[选手页](https://wxinyi.github.io/stockboard/)"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="关注选手盯盘 watchdog")
    ap.add_argument("--date", help="YYYY-MM-DD(默认今天; 回放用)")
    ap.add_argument("--dry-run", action="store_true", help="只打印, 不推钉钉/不写文件/不落状态")
    ap.add_argument("--force", action="store_true", help="无视交易时段闸门(排障用)")
    ap.add_argument("--state-file", default=str(STATE_FILE))
    ap.add_argument("--changed-file", default=str(CHANGED_FILE))
    args = ap.parse_args()
    state_file, changed_file = Path(args.state_file), Path(args.changed_file)

    now = bj_now()
    date_str = args.date or now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")

    if not args.dry_run and not args.force and not in_trading_window(now):
        print(f"非盯盘时段({now.strftime('%H:%M')} %a), 跳过")
        return 0

    st = load_state(date_str, state_file)
    first_of_day = not any(st["pushed"].values())
    data = fetch_all(WATCHED_PLAYERS, date_str)

    n_ok = sum(1 for zh, o in data.items()
               if not o.get("hidden") and o.get("ok", True) and not o.get("err"))
    n_hidden = sum(1 for o in data.values() if o.get("hidden"))
    if n_ok == 0 and (len(data) - n_hidden) > 0:
        st["fails"] += 1
    else:
        st["fails"], st["alerted"] = 0, 0

    base_cache, changed = {}, []
    new_pushes = []   # (zh, nm, trade, is_add)
    new_mark = {}     # zh -> {id(trade): is_add}, 供消息标记 🆕/↩️追加
    for zh, nm in WATCHED_PLAYERS:
        o = data.get(zh) or {}
        if o.get("hidden"):
            continue
        pushed_player = st["pushed"].setdefault(zh, {})
        if o.get("err") or not o.get("ok", True):
            # 拉取失败: 不动已推集合(下次成功后按次数补推), 降级由 fails 计数负责
            continue
        if first_of_day:
            base_cache.setdefault(zh, fetch_base_player(zh))
            base = base_cache.get(zh)
            if base:
                seed_baseline(pushed_player, base, date_str)
        newt, _known = decide_pushes(o["trades"], pushed_player)
        if newt:
            new_mark[zh] = {id(t): is_add for t, is_add in newt}
            for t, is_add in newt:
                new_pushes.append((zh, nm, t, is_add))

    if new_pushes:
        codes = sorted({t.get("stock_code", "") for _, _, t, _ in new_pushes if t.get("stock_code")})
        msg = build_message(date_str, hhmm, data, new_mark, fetch_quotes(codes))
        if args.dry_run:
            print(msg)
        else:
            DingTalk().send_markdown(f"🚨 盯盘提醒 {date_str} {hhmm}", msg)
            print(f"✅ 已推送 {len(new_pushes)} 笔新操作(全员列表)")

    if st["fails"] >= MAX_FAILS and not st["alerted"] and not args.dry_run:
        try:
            DingTalk().send_markdown(
                f"⚠️ 盯盘已降级 {date_str}",
                f"连续 {st['fails']} 班关注选手全部拉取失败, 盘中操作提醒暂停。\n"
                f"日报兜底仍在; 请查 [watchdog 运行日志]"
                f"(https://github.com/WXinYi/stockboard/actions/workflows/watchdog.yml)。")
            st["alerted"] = 1
            print("⚠️ 已推送盯盘降级告警")
        except Exception as e:
            print(f"❌ 降级告警推送失败: {e}", file=sys.stderr)

    # 叠加更新: 只对"本轮有推送"的选手做(无操作不动页面文件), 按选手去重
    if not args.dry_run:
        for zh in dict.fromkeys(zh for zh, _nm, _t, _a in new_pushes):
            base = base_cache.get(zh)
            if base is None:
                base = fetch_base_player(zh)
                base_cache[zh] = base
            if base is None:
                continue
            o = data[zh]
            write_player_json(zh, merge_player(base, o["positions"], o["trades"], date_str))
            changed.append(zh)
        changed_file.write_text("\n".join(changed), encoding="utf-8")
        save_state(st, state_file)
        print(f"盯盘检测: {n_ok}/{len(WATCHED_PLAYERS)} 人可用(隐藏 {n_hidden}), "
              f"新操作 {len(new_pushes)} 笔, 页面更新 {len(changed)} 人, "
              f"连续失败 {st['fails']}")
    else:
        will_fix = [zh for zh, nm, _t, _a in new_pushes]
        print(f"[dry-run] 将推送 {len(new_pushes)} 笔; 将叠加更新 {len(will_fix)} 个选手文件: "
              f"{will_fix}; 状态不落盘")
    return 0


if __name__ == "__main__":
    sys.exit(main())
