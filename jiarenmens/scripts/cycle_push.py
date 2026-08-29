#!/usr/bin/env python3
"""
超短格局定时推送(搭现有钉钉管道的便车):
  午盘 13:05  crawl.yml 内调用  --session midday   → 上午战况 + 午后关注
  收盘前 14:45 cycle-eod.yml 调用 --session eod     → 尾盘格局 + 尾盘纪律 + 明日竞价观察名单

数据: 实时涨停池(带涨停时间/封单) + 实时涨跌炸板 → 注入周期引擎(当日口径)。
用法:
  python scripts/cycle_push.py --session eod            # 推钉钉
  python scripts/cycle_push.py --session midday --dry-run
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.spiders.auction_spider import KPLSpider  # noqa: E402
from src.analysis.emotion_cycle import compute_cycle  # noqa: E402

BJ_TZ = ZoneInfo("Asia/Shanghai")


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
        lines.append("💡 午后重点: 高位股封单是否松动(周期拐点) · 主线是否继续扩散 · 弱转强只认放量站稳均价")
    else:
        obs = "、".join(f"{l['name']}({l['pid']}板)" for l in c["leaders"][:4]) or "无"
        lines.append(f"💡 明日竞价观察名单: {obs}")
        lines.append("💡 竞价三分钟定情景: 观察名单高开强=周期延续可接力；低开=转折，空仓纪律优先")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="超短格局定时推送")
    ap.add_argument("--session", choices=["midday", "eod"], required=True)
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送")
    args = ap.parse_args()

    spider = KPLSpider()
    rt = fetch_rt_today(spider)
    res = compute_cycle(rt["date"], persist=False, rt_today=rt)
    res["_rt_rows"] = rt["rows"]
    text = build_markdown(res, args.session)
    if args.dry_run:
        print(text)
        return 0
    from src.notify.dingtalk import DingTalk
    tag = "午盘" if args.session == "midday" else "尾盘"
    resp = DingTalk().send_markdown(f"超短格局 {res['date']} {tag}", text)
    print(f"📣 已推送: {resp}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
