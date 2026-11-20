#!/usr/bin/env python3
"""
超短格局定时推送(搭现有钉钉管道的便车):
  午盘 13:05  crawl.yml 内调用  --session midday   → 上午战况 + 午后关注
  尾盘 14:48  crawl.yml 内调用  --session eod       → 尾盘格局 + 尾盘纪律 + 明日竞价观察名单

数据: 实时涨停池(带涨停时间/封单) + 实时涨跌炸板 → 注入周期引擎(当日口径)。
用法:
  python scripts/cycle_push.py --session eod            # 推钉钉
  python scripts/cycle_push.py --session midday --dry-run
  python scripts/cycle_push.py --session eod --force    # 忽略去重状态手动补推

2026-11-17 去重: 按 (交易日, session) 幂等, state 落 data/.cycle_push_state.json,
由 crawl.yml 提交步骤精确 add 后随数据提交持久化; 后到班跳过。--dry-run 不受状态影响。
任何异常(接口/计算/推送)统一写失败标志并返回非零, 避免被 continue-on-error 静默吞掉。
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.spiders.auction_spider import KPLSpider  # noqa: E402
from src.analysis.emotion_cycle import compute_cycle  # noqa: E402
from src.utils.atomic import atomic_text  # noqa: E402

BJ_TZ = ZoneInfo("Asia/Shanghai")
STATE_PATH = ROOT / "data" / ".cycle_push_state.json"


def state_key(date_str: str, session: str) -> str:
    """同一交易日同一场次只推一次(跨 crawl.yml / cycle-eod.yml 两个通道)。"""
    return f"{date_str}|{session}"


def _load_sent(path: Path = STATE_PATH) -> dict:
    """读状态 → {key: sent_at}。兼容旧格式(单个 "key" 字段, 无 "sent")。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}   # 文件缺失/损坏 → 当作未推过, 宁可多推不漏推
    sent = d.get("sent")
    sent = dict(sent) if isinstance(sent, dict) else {}
    if d.get("key"):                      # 旧格式: 迁移进来, 不丢历史去重
        sent.setdefault(d["key"], d.get("sent_at") or "")
    return sent


def already_sent(key: str, path: Path = STATE_PATH) -> bool:
    return key in _load_sent(path)


def mark_sent(key: str, path: Path = STATE_PATH) -> None:
    """按 key 累加(而非覆盖单键): midday/eod 交错补跑时不会互相抹掉对方。
    原子写(tmp+os.replace): 截断文件会让去重失效 → 重复推送。"""
    try:
        sent = _load_sent(path)
        sent[key] = datetime.now(BJ_TZ).isoformat(timespec="seconds")
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(path, json.dumps({
            "key": key, "sent_at": sent[key], "sent": sent,   # 保留 key/sent_at 供旧读者兼容
        }, ensure_ascii=False))
    except Exception as e:
        # 状态写不进去不影响本次已送达的推送, 但下一通道可能重复 → 留可见注解
        print(f"::warning::写入推送去重状态失败(不影响本次推送): {e}", file=sys.stderr)


def fetch_rt_today(spider: KPLSpider) -> dict:
    """实时当日: 涨停池(板位1-5全量) + 涨跌炸板"""
    rows = []
    for pid in (1, 2, 3, 4, 5):
        data = spider.zt_pool_rt(pid_type=pid, st=500)
        for g in data.get("info", []):
            if not isinstance(g, list):
                continue
            for r in g:
                if isinstance(r, list) and len(r) >= 14:
                    rows.append({"date": None, "code": str(r[0]), "name": str(r[1]),
                                 "pid_type": pid,
                                 "zt_time": r[4] if isinstance(r[4], (int, float)) else None,
                                 "seal_amount": r[6], "max_seal": r[7], "main_net": r[8],
                                 "amount": r[11], "plates": str(r[12])})
        time.sleep(0.15)
    rf = spider.rise_fall_rt().get("info") or [[None] * 7]
    b = rf[0] if isinstance(rf[0], list) else [None] * 7
    today = str(b[6]) if len(b) > 6 and b[6] else \
        datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    # RT 接口按板位组返回会有跨组重复 → 每只代码只保留最高板位行
    best: dict[str, dict] = {}
    for r in rows:
        r["date"] = today
        c0 = r["code"]
        if c0 not in best or r["pid_type"] > best[c0]["pid_type"]:
            best[c0] = r
    return {"rows": list(best.values()), "breadth": b, "date": today}


def fmt_pct(x):
    return f"{x:.0%}" if isinstance(x, (int, float)) else "无数据"


def fmt_time(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M") if isinstance(ts, (int, float)) else "?"


# ── A 股共识附加段（2026-11-16 用户要求：午盘顺便推调仓共识，重仓共识只推 A 股）──
# 规则与前端 stockboard-app/src/utils/stockMarket.js 完全一致：6 位=A 股，5 位=港股。
# 只推 A 股，与页面过滤口径相同；数据层 today.json / stocks.json 仍保留全量，不改契约。
DATA_LATEST = ROOT.parent / "stockboard-app" / "public" / "data" / "latest"
CONSENSUS_TOP_N = 5


def _is_a_stock(code) -> bool:
    import re
    return bool(re.fullmatch(r"\d{6}", str(code or "")))


def _safe_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _load_json(name: str):
    try:
        return json.loads((DATA_LATEST / name).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cycle_push] 读取 {name} 失败，跳过共识段: {e}", file=sys.stderr)
        return None


def build_consensus_markdown() -> str:
    """午盘附加：A 股调仓共识 + A 股重仓共识（各 Top5）。数据缺失/异常返回空串。"""
    today = _load_json("today.json") or {}
    stocks = _load_json("stocks.json") or {}
    # 两个都缺才是真的没数据；只缺一个时仍推另一半（宽容，不至于整段消失）
    if not today and not stocks:
        return ""

    lines = ["\n**📊 A股共识（收盘后快照 · 滞后信息，非买卖依据）**"]
    has_data = False

    # 调仓共识：today.json.tradeConsensus.stocks（买/卖人数按选手去重）
    tc = today.get("tradeConsensus") or {}
    trade_rows = [s for s in (tc.get("stocks") or []) if _is_a_stock(s.get("code"))]
    if trade_rows:
        has_data = True
        lines.append(f"- 调仓 Top{CONSENSUS_TOP_N}（净买入人数）:")
        for s in trade_rows[:CONSENSUS_TOP_N]:
            buy, sell = _safe_int(s.get("buy")), _safe_int(s.get("sell"))
            lines.append(f"  · {s.get('name', '')} {s.get('code')}: 买{buy}人/"
                         f"卖{sell}人 净{buy - sell:+d}")
    else:
        lines.append("- 调仓共识: 当日暂无 A 股调仓数据")

    # 重仓共识：stocks.json.stats（持有人数 h / 人均仓位 tp/h / 人均盈亏 ap/h）
    hold_rows = [s for s in (stocks.get("stats") or []) if _is_a_stock(s.get("code"))]
    if hold_rows:
        has_data = True
        lines.append(f"- 重仓 Top{CONSENSUS_TOP_N}（按合计仓位）:")
        for s in hold_rows[:CONSENSUS_TOP_N]:
            h = _safe_int(s.get("h")) or 1
            try:
                avg_ratio = float(s.get("tp") or 0) / h
            except (TypeError, ValueError):
                avg_ratio = 0.0
            lines.append(f"  · {s.get('name', '')} {s.get('code')}: {h}人持有 · "
                         f"人均仓位 {avg_ratio:.0f}%")
    else:
        lines.append("- 重仓共识: 暂无 A 股持仓数据")

    return "\n".join(lines) if has_data else ""


def build_markdown(res: dict, session: str) -> str:
    c, m = res, res["metrics"]
    title = "午间盘面格局" if session == "midday" else "尾盘格局"
    lines = [f"## 🧭 超短格局 · {title} · {c['date']}",
             f"**周期: {c['stage']}**（置信度 {c['confidence']}/9）"]
    lines += [f"- {x}" for x in c["reasons"]]
    lines.append(f"- 涨停 {m['zt']} 只 · 破板率 {m['broke_rate']}% · "
                 f"高度 {m['height']}B（昨 {m['height_prev']}B）")
    lines.append(f"- 梯队: 低位 {m['ladder']['low']} / 中位 {m['ladder']['mid']} / "
                 f"高位 {m['ladder']['high']} · 晋级率 低位{fmt_pct(m['promo']['low'])} "
                 f"中位{fmt_pct(m['promo']['mid'])}")
    if c["mainlines"]:
        ml = " · ".join(f"{a['board']}{a['count']}只" for a in c["mainlines"][:3])
        lines.append(f"- 主线: {ml}")

    lines.append("\n**🎯 龙头谱系**")
    for l in c["leaders"]:
        lines.append(f"- [{l['role']}] **{l['name']}** {l['pid']}板 — {l['note']}")

    # 当日时间线亮点(实时数据独有): 高位/中位股封板节奏 + 午后扩散
    rt_rows = [r for r in c.get("_rt_rows", []) if r["height"] >= 3]
    if rt_rows:
        lines.append("\n**⏱️ 高位梯队时间线**")
        for r in sorted(rt_rows, key=lambda x: -x["height"])[:8]:
            boom = " ⚠️炸板回落" if (r["max_seal"] or 0) > (r["seal_amount"] or 0) * 3 else ""
            lines.append(f"- {r['name']} {r['height']}板 · {fmt_time(r['zt_time'])}封板 · "
                         f"封单 {(r['seal_amount'] or 0)/1e8:.2f}亿 · 净买 {(r['main_net'] or 0)/1e8:+.1f}亿{boom}")
        afternoon = [r for r in rt_rows if isinstance(r["zt_time"], (int, float))
                     and fmt_time(r["zt_time"]) >= "13:00"]
        if afternoon:
            lines.append(f"- ☀️ 午后上板 {len(afternoon)} 只: "
                         + "、".join(f"{r['name']}({fmt_time(r['zt_time'])})" for r in afternoon[:6]))

    lines.append(f"\n**📌 阶段纪律: {c['playbook']}**")
    if session == "midday":
        lines.append("💡 午后重点: 高位股封单是否松动(周期拐点) · 主线是否继续扩散 · 回封只认放量站稳均价")
    else:
        obs = "、".join(f"{l['name']}({l['pid']}板)" for l in c["leaders"][:4]) or "无"
        lines.append(f"💡 明日竞价观察名单: {obs}")
        lines.append("💡 竞价三分钟定情景: 观察名单高开强=周期延续可接力；低开=转折，空仓纪律优先")
    return "\n".join(lines)


def _fail(args, tag, err):
    try:
        Path(args.fail_flag).write_text(f"{tag}格局推送失败: {err}\n", encoding="utf-8")
    except Exception:
        pass
    print(f"❌ {tag}格局推送失败: {err}", file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description="超短格局定时推送")
    ap.add_argument("--session", choices=["midday", "eod"], required=True)
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送(不受去重状态影响)")
    ap.add_argument("--force", action="store_true", help="忽略去重状态, 手动补推用")
    ap.add_argument("--fail-flag", default="/tmp/cycle_push_failed",
                    help="任一环节失败时写标志文件(workflow 据此判红, 不让失败无声无息)")
    args = ap.parse_args()
    tag = "午盘" if args.session == "midday" else "尾盘"

    try:
        spider = KPLSpider()
        rt = fetch_rt_today(spider)
        res = compute_cycle(rt["date"], persist=False, rt_today=rt)
        res["_rt_rows"] = rt["rows"]
        text = build_markdown(res, args.session)
        if args.session == "midday":
            consensus = build_consensus_markdown()
            if consensus:
                print("[cycle_push] 午盘共识段:\n" + consensus)
            text += consensus
        if args.dry_run:
            print(text)
            return 0
        key = state_key(res["date"], args.session)
        if not args.force and already_sent(key, STATE_PATH):
            print(f"[cycle_push] {res['date']} {args.session} 已推送过, 跳过(多通道去重)")
            return 0
        from src.notify.dingtalk import DingTalk
        resp = DingTalk().send_markdown(f"超短格局 {res['date']} {tag}", text)
        print(f"📣 已推送: {resp}")
        mark_sent(key, STATE_PATH)
    except Exception as e:
        return _fail(args, tag, e)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
