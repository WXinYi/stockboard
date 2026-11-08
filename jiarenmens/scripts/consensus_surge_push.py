#!/usr/bin/env python3
"""重仓共识·日度飙升汇总推送（2026-11-08 立项，预注册规格）。

规格（写死不再改）:
- 时点: 仅 15:15 收盘 sync 专班（crawl-eod 事件）调用一次/交易日, 单条汇总卡, 无状态。
- 基线: 上一交易日的 stocks.json（actions/cache 每日接力: 收盘班保存当日版,
  次日收盘班恢复即为基线; CI 是浅克隆, git 历史不可用）。
- 飙升定义: 持有人 h_today - h_yest ≥ 8 且 h_today ≥ 15（绝对量级门槛防小样本噪音）,
  按 Δh 降序取 Top10。
- 诚实边界（写进卡片脚注）: h 为当日采集样本口径, 班间有漂移(小米曾 8→120 的跳变
  多为样本换血而非真实买入); 回测: 共识热度与次日上涨无关(追高均值 -0.95%)。
- 基线缺失(首日/缓存丢失) → 不推, 打日志(宁可沉默不发假数据)。

用法: python scripts/consensus_surge_push.py --date YYYY-MM-DD \
        --baseline data/.consensus_baseline.json \
        --cur stockboard-app/public/data/latest/stocks.json [--dry-run]
"""
import sys
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = ROOT.parent
IDX = "stockboard-app/public/data/latest/stocks.json"
DH_MIN, H_MIN, TOP_N = 8, 15, 10

BASE_URL = "https://WXinYi.github.io/stockboard"


def last_stocks_before(baseline_path: str):
    """基线 = 缓存接力的上一交易日 stocks.json → (来源描述, index) 或 (None, None)"""
    p = Path(baseline_path)
    if not p.is_file():
        return None, None
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))["stockStats"]
        return "缓存基线", {r["c"]: r for r in rows}
    except Exception as e:
        print(f"⚠️ 基线文件解析失败({e}), 不推送")
        return None, None


def pick_surges(cur: dict, prev: dict):
    """Δh≥DH_MIN 且 h≥H_MIN → 按 Δh 降序 TopN"""
    out = []
    for c, s in cur.items():
        p = prev.get(c)
        if not p:
            continue
        dh = s["h"] - p["h"]
        if dh >= DH_MIN and s["h"] >= H_MIN:
            out.append({"code": c, "name": s.get("n", c), "h0": p["h"], "h1": s["h"],
                        "dh": dh, "tp0": p["tp"], "tp1": s["tp"]})
    out.sort(key=lambda x: -x["dh"])
    return out[:TOP_N]


def build_message(date: str, surges) -> str:
    from urllib.parse import quote
    lines = [f"## 📊 重仓共识飙升 · {date}", ""]
    for s in surges:
        link = f"[{s['name']}]({BASE_URL}/#/stock/{s['code']}?name={quote(s['name'])})"
        lines.append(f"- {link}({s['code']}) 持有人 {s['h0']}→**{s['h1']}**(+{s['dh']}) "
                     f"· 总仓位 {s['tp0']:.0f}%→{s['tp1']:.0f}%")
    lines += ["", "— 采集样本口径, 班间有漂移; 热度≠买入信号(回测: 追高次日均值 -0.95%)",
              f"— [全量共识榜]({BASE_URL}/)"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="当日 YYYY-MM-DD(数据日期)")
    ap.add_argument("--baseline", required=True, help="上一交易日 stocks.json(缓存接力)")
    ap.add_argument("--cur", default=None, help="当日 stocks.json 路径(缺省=线上导出路径)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不发钉钉")
    args = ap.parse_args()
    cur_path = args.cur or str(ROOT.parent / IDX)

    try:
        cur = {r["c"]: r for r in json.loads(Path(cur_path).read_text(encoding="utf-8"))["stockStats"]}
    except Exception as e:
        print(f"❌ 当日 stocks.json 不可读({e}), 不推送")
        return 1
    src, prev = last_stocks_before(args.baseline)
    if not prev:
        print("⚠️ 无上一交易日 stocks.json 基线, 不推送(宁缺毋假)")
    else:
        surges = pick_surges(cur, prev)
        if not surges:
            print(f"当日({args.date})无共识飙升票, 不推送")
        else:
            msg = build_message(args.date, surges)
            if args.dry_run:
                print(msg)
            else:
                from src.notify.dingtalk import DingTalk
                DingTalk().send_markdown(f"📊 重仓共识飙升 {args.date}", msg)
                print(f"✅ 已推送共识飙升 {len(surges)} 只 (基线={src})")
                for s in surges:
                    print(f"   {s['name']}({s['code']}) {s['h0']}→{s['h1']}")
    # 基线接力: 无论推没推, 当日版存为下一交易日的基线(缓存 save 步骤持久化)
    Path(args.baseline).write_text(Path(cur_path).read_text(encoding="utf-8"), encoding="utf-8")
    print(f"基线已更新为当日版 → {args.baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
