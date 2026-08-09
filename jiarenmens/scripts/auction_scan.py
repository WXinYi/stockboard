#!/usr/bin/env python3
"""
竞价扫描入口: 采集 → 漏斗 → 候选 JSON → 钉钉推送

用法(在 jiarenmens/ 目录下执行):
  python scripts/auction_scan.py --probe            # T1 探测: 验证竞价接口可用性
  python scripts/auction_scan.py --dry-run          # 完整扫描, 不推钉钉(本地验证用)
  python scripts/auction_scan.py --date 2026-08-07  # 回放指定日期(历史数据)
  python scripts/auction_scan.py --confirm --candidates /tmp/auction_candidates.json  # E 层开盘确认(09:31)

时序: 09:25 cron 触发 → 扫描 ≈2-3min → 09:29 钉钉主结论 → 09:31 --confirm 补推 E 层。
"""
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from src.analysis import auction_funnel as funnel  # noqa: E402
from src.config import AUCTION_OUT  # noqa: E402
from src.notify.dingtalk import DingTalk  # noqa: E402
from src.spiders.auction_spider import AuctionStore, KPLSpider  # noqa: E402

BJ_TZ = ZoneInfo("Asia/Shanghai")
WORKERS = 20          # 并发
GENE_LIMIT = 60       # 涨停基因查询上限(候选池按初步分取前 N)
BID_LIMIT = 20        # 竞价分时/大单查询上限(最终候选前 N)
BOARD_LIMIT = 8       # 强势板块数
SCORE_THRESHOLD = 10  # 核心过线分(15 分制, 待回测校准); 备选线 = 过线-3


# =============================================================================
# 数据归一化 → 统一 dict, 字段与 funnel.score_stock 对应
# =============================================================================

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_bkjjbl(row):
    """GetBKJJBL 行: [代码,名称,现价,实时涨幅,竞价量比,竞价额,竞价涨幅,竞价净额,竞价换手,流通市值,板块标签,...]"""
    return {
        "code": str(row[0]), "name": row[1], "price": _f(row[2]),
        "change_pct": _f(row[3]), "vol_ratio": _f(row[4]),
        "limit_up_buy": _f(row[5]), "bid_pct": _f(row[6]), "bid_net": _f(row[7]),
        "turnover_ratio": _f(row[8]), "circ_mv": _f(row[9]),
        "plates": str(row[10] or "") if len(row) > 10 else "",
        "tag": str(row[13] or "") if len(row) > 13 else "",
    }


def normalize_bidlist(row):
    """MorningBiddingList 行: [代码,名称,竞价价,竞价涨幅,涨停委买额,连板数,竞价净额,竞价换手,主力净额,主力买,主力卖,板块标签,流通市值,...,tag]"""
    return {
        "code": str(row[0]), "name": row[1], "price": _f(row[2]),
        "change_pct": _f(row[3]), "vol_ratio": None,  # 无量比字段
        "limit_up_buy": _f(row[4]), "bid_pct": _f(row[3]), "bid_net": _f(row[6]),
        "turnover_ratio": _f(row[7]), "main_net": _f(row[8]),
        "circ_mv": _f(row[12]) if len(row) > 12 else None,
        "plates": str(row[11] or "") if len(row) > 11 else "",
        "tag": str(row[16] or "") if len(row) > 16 else "",
    }


def _pool_row_layout(item):
    """归一化 dict → save_bid_pool 需要的 MorningBiddingList 原始行布局(17 位, tag 在 r[16])"""
    return [
        item["code"], item["name"], item["price"] or 0, item["bid_pct"] or 0,
        item["limit_up_buy"] or 0, item["bid_pct"] or 0, item["bid_net"] or 0,
        item["turnover_ratio"] or 0, item.get("main_net") or 0, 0, 0,
        item.get("plates") or "", item["circ_mv"] or 0, 0, 0, 0, item["tag"] or "",
    ]


# =============================================================================
# 采集
# =============================================================================

def collect_env(spider, date_str):
    return {
        "mood": spider.env_mood(),
        "capacity": spider.env_capacity(date_str),
        "bid_total": spider.env_bid_total(date_str),
        "bid_count": spider.env_bid_count(date_str).get("info", []),
        "zt_expr": spider.env_zt_expression(date_str),
    }


def collect_boards(spider, date_str):
    """板块 → 成分股票池(去重, 记录所属板块) + 昨日涨停池(身位行情/连板标记)"""
    board_bid = spider.board_bid(date_str)
    ranking = spider.board_ranking(date_str)
    boards = funnel.board_select(board_bid, ranking, max_boards=BOARD_LIMIT)

    pool = {}
    def fetch_stocks(b):
        try:
            data = spider.board_stocks(b["code"], date_str, st=50)
            return b, data.get("List", [])
        except Exception as e:
            print(f"⚠️ 板块成分失败 {b['name']}: {e}")
            return b, []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for b, rows in ex.map(fetch_stocks, boards):
            for r in rows:
                if len(r) < 10:
                    continue
                item = normalize_bkjjbl(r)
                if item["code"] in pool:
                    pool[item["code"]]["boards"].append(b["name"])
                else:
                    item["boards"] = [b["name"]]
                    pool[item["code"]] = item

    # 竞价列表(涨停委买 Top100)并入, 补足板块成分外的强势票
    try:
        bid_rows = spider.bid_list(date_str, pid_type=0, st=100).get("info", [])
        for r in bid_rows:
            if len(r) < 13:
                continue
            item = normalize_bidlist(r)
            if item["code"] in pool:
                pool[item["code"]]["boards"] = pool[item["code"]].get("boards") or []
            else:
                item["boards"] = []
                pool[item["code"]] = item
    except Exception as e:
        print(f"⚠️ 竞价列表失败: {e}")

    # 昨日涨停池 5 个板位(PidType 1-5), 展平保存 + 提取连板标记
    zt_groups = []
    for pid in range(1, 6):
        try:
            data = spider.zt_pool(date_str, pid_type=pid, st=100)
            rows = [r for group in data.get("info", []) for r in group]
            zt_groups.append((pid, rows))
            for r in rows:
                # 连板标记在 r[18](r[21] 是数字字段), 仅文本标记才覆盖 GetBKJJBL 的板块标记
                if len(r) > 18 and r[18] and str(r[0]) in pool:
                    pool[str(r[0])]["tag"] = str(r[18])
        except Exception as e:
            print(f"⚠️ 涨停池 PidType={pid} 失败: {e}")
            zt_groups.append((pid, []))
    return boards, pool, board_bid, zt_groups


def collect_genes(spider, pool, limit=GENE_LIMIT):
    """涨停基因: 按初步分(B1+B2+B3)取前 N"""
    prelim = sorted(pool.values(), key=funnel.prelim_score, reverse=True)[:limit]
    genes = {}
    def fetch(item):
        try:
            return item["code"], spider.zt_gene(item["code"])
        except Exception as e:
            print(f"⚠️ 涨停基因失败 {item['code']}: {e}")
            return item["code"], []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for code, g in ex.map(fetch, prelim):
            if g:
                genes[code] = g
    return genes


def collect_bids(spider, candidates_top):
    """候选前 N 的竞价分时(GetStockBid, B4/B5 用)"""
    stock_bids = {}
    def fetch(code):
        try:
            return code, spider.stock_bid(code).get("bid", [])
        except Exception as e:
            print(f"⚠️ 竞价分时失败 {code}: {e}")
            return code, []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for code, bid in ex.map(fetch, candidates_top):
            stock_bids[code] = bid
    return stock_bids


# =============================================================================
# E 层开盘确认(09:31, 腾讯分时)
# =============================================================================

def _qq_symbol(code):
    if code.startswith(("6", "5")):
        return f"sh{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    return f"sz{code}"


def qq_minute(code):
    """腾讯分时: [[0930, 价, 量], ...] 首根 09:30 为开盘分钟"""
    sym = _qq_symbol(code)
    r = requests.get(
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={sym}",
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    return r.json()["data"][sym]["data"]


def e_confirm(candidates_path: Path):
    """E1 首分钟量>竞价末分钟量 / E2 首分钟价>竞价价 → 钉钉补推"""
    with open(candidates_path) as f:
        candidates = json.load(f)
    rows = []
    for c in candidates[:10]:
        try:
            minute = qq_minute(c["code"])
            first_vol = float(minute[0][2])
            first_px = float(minute[0][1])
        except Exception as e:
            print(f"⚠️ 腾讯分时失败 {c['code']}: {e}")
            continue
        bid_px = c["factors"].get("bid_price")
        bid_vol = c["factors"].get("bid_vol_last")
        e1 = bool(bid_vol and first_vol > bid_vol)
        e2 = bool(bid_px and first_px > bid_px * 1.001)  # 开盘价 > 竞价价 → 继续抢筹
        rows.append({"code": c["code"], "name": c["name"], "first_vol": first_vol,
                     "first_px": first_px, "bid_px": bid_px, "E1": e1, "E2": e2})
    if not rows:
        print("⚠️ 无可用分时数据, 跳过推送")
        return []
    ok = [r for r in rows if r["E1"] or r["E2"]]
    text = [
        f"## ⚡ 开盘确认 09:31",
        f"> {len(ok)}/{len(rows)} 只开盘走强",
        "",
    ]
    for r in rows[:10]:
        mark = "🟢" if r["E1"] or r["E2"] else "⚪"
        marks = ("E1量" if r["E1"] else "") + ("E2价" if r["E2"] else "") or "走弱"
        text.append(f"- {mark} **{r['name']}**({r['code']}) 开盘{r['first_px']:.2f} 首分钟量{r['first_vol']:.0f} [{marks}]")
    text.append("")
    text.append("📈 [复盘页面](https://WXinYi.github.io/stockboard/#/auction)")
    resp = DingTalk().send_markdown("竞价开盘确认", "\n".join(text))
    print(f"📣 钉钉推送: {resp}")
    return rows


# =============================================================================
# 钉钉消息
# =============================================================================

def build_message(date_str, result, boards, crawl_time) -> str:
    env = result["env"]
    e = env["data"]
    env_parts = [f"情绪{e['strong']}", f"连板{e['lbgd']}"]
    if e["capacity_ratio"] is not None:
        env_parts.append(f"量能比{e['capacity_ratio']:.2f}")
    if e["red_ratio"] is not None:
        env_parts.append(f"红盘占比{e['red_ratio']:.0%}")
    lines = [
        f"## 🏆 竞价抢筹候选池 {crawl_time}",
        f"> 日期: {date_str} | 环境: {'✅ 正常' if env['pass'] else '❌ 空仓'}",
        "",
        f"**环境**: {' '.join(env_parts)}",
        "",
    ]
    if not env["pass"]:
        lines += ["**空仓原因**: " + "; ".join(env["reasons"]),
                  "", "📈 [复盘页面](https://WXinYi.github.io/stockboard/#/auction)"]
        return "\n".join(lines)
    board_text = "、".join(f"{b['name']}({b['src']})" for b in boards[:6])
    lines += [f"**强势板块**: {board_text or '无'}", "", "**🎯 核心候选**:", ""]
    core = [c for c in result["candidates"] if c.get("tier") == "core"]
    for i, c in enumerate(core[:3], 1):
        lines += _cand_line(i, c)
    if not core:
        lines.append("   (无 — 竞价无真金白银抢筹, 观望)")
    watch = result.get("watch", [])
    if watch:
        lines += ["", f"**👀 备选观察**:", ""]
        for i, c in enumerate(watch[:5], 1):
            lines += _cand_line(i, c)
    lines += ["", "📈 [复盘页面](https://WXinYi.github.io/stockboard/#/auction)"]
    return "\n".join(lines)


def _cand_line(i: int, c: Dict) -> List[str]:
    """单只候选的消息行: 评分明细 + 因子 + 执行计划"""
    f_ = c["factors"]
    sub = c["sub"]
    lines = [f"{i}. **{c['name']}**({c['code']}) 评分 {c['score']}/{c['max']} "
             f"(资金{sub['S1资金']} 形态{sub['S2形态']} 共振{sub['S3共振']} "
             f"身位{sub['S4身位']} 量比{sub['S5量比']} 基因{sub['S6基因']}) {c['gene']['reason']}"]
    parts = []
    if f_["bid_pct"] is not None:
        parts.append(f"竞价{f_['bid_pct']:+.2f}%")
    if f_["bid_net"] is not None:
        parts.append(f"净买{f_['bid_net'] / 1e4:.0f}万")
    if f_["vol_ratio"] is not None:
        parts.append(f"量比{f_['vol_ratio']:.2f}")
    if c["tag"] and "板" in c["tag"]:  # 只展示连板类标记(过滤流通市值等杂字段)
        parts.append(f"标记[{c['tag']}]")
    if c["boards"]:
        parts.append("板块:" + ",".join(c["boards"][:3]))
    lines.append("   - " + " | ".join(parts))
    lines.append("   - 💡 竞价价买入 → 冲高+5%止盈 / 跌破竞价价-2%止损")
    return lines


# =============================================================================
# 主流程
# =============================================================================

def _fallback_trading_day(spider: KPLSpider, date_str: str) -> str:
    """周末/节假日无市场数据(errcode=1020) → 回退最近交易日(最多回溯5天)。
    工作日 cron-job 触发不受影响; 周末手动触发/测试用最近交易日数据。"""
    from datetime import timedelta
    cur = date_str
    for _ in range(6):
        try:
            spider.env_capacity(cur)
            return cur
        except RuntimeError:
            print(f"⚠️ {cur} 无市场数据(周末/节假日), 回退上一天")
            cur = (datetime.strptime(cur, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    raise RuntimeError(f"最近5天均无市场数据({date_str} 起回溯)")


def scan(date_str: str, dry_run: bool = False) -> int:
    t0 = time.time()
    spider = KPLSpider()
    date_str = _fallback_trading_day(spider, date_str)
    store = AuctionStore()
    crawl_time = datetime.now(BJ_TZ).strftime("%H:%M")

    print(f"[1/6] 环境层采集 {date_str}")
    env = collect_env(spider, date_str)
    store.save_mood(date_str, env["mood"], env["capacity"], env["bid_total"],
                    env["bid_count"], env["zt_expr"])

    print(f"[2/6] 板块层 + 候选池")
    boards, pool, board_bid, zt_data = collect_boards(spider, date_str)
    store.save_board_bid(date_str, board_bid)
    store.save_limit_pool(date_str, zt_data)

    print(f"[3/6] 涨停基因({len(pool)} 池 → top{GENE_LIMIT})")
    genes = collect_genes(spider, pool)
    store.save_genes(date_str, genes)

    print(f"[4/6] 过 B1 门槛全量拉竞价分时(消除前N名数据断层)")
    in_gate = [i for i in pool.values()
               if i.get("bid_pct") is not None and 1 <= i["bid_pct"] <= 7]
    stock_bids = collect_bids(spider, [i["code"] for i in in_gate])
    print(f"      {len(in_gate)} 只过门槛 → 竞价分时 {len(stock_bids)} 只")

    print(f"[5/6] 漏斗计算")
    result = funnel.run_funnel(
        env, boards, list(pool.values()), genes, stock_bids,
        score_threshold=SCORE_THRESHOLD)
    # E 层确认覆盖核心 + 备选(独立变量, 不污染 out 的 candidates)
    e_candidates = result["candidates"] + result.get("watch", [])

    # 落库候选池(板块成分 + 竞价列表合并)
    store.save_bid_pool(date_str, [_pool_row_layout(i) for i in pool.values()], "merged")

    print(f"[6/6] 输出 + 推送 (耗时 {time.time()-t0:.0f}s)")
    out = {
        "date": date_str, "generated_at": crawl_time,
        "env": result["env"], "boards": boards,
        "candidates": result["candidates"], "watch": result.get("watch", []),
        "empty_reason": result["empty_reason"],
        "rejected": result.get("rejected", []),
        "stats": {"pool": len(pool), "genes": len(genes), "boards": len(boards)},
    }
    AUCTION_OUT.parent.mkdir(parents=True, exist_ok=True)
    AUCTION_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    cand_path = Path("/tmp/auction_candidates.json")  # 供 --confirm 步骤使用
    cand_path.write_text(json.dumps(e_candidates, ensure_ascii=False), "utf-8")
    print(f"✅ auction.json 已写入 {AUCTION_OUT} (核心 {len(result['candidates'])} + 备选 {len(result.get('watch', []))} 只)")

    if not dry_run:
        text = build_message(date_str, result, boards, crawl_time)
        resp = DingTalk().send_markdown(f"竞价抢筹 {date_str} {crawl_time}", text)
        print(f"📣 钉钉推送: {resp}")
    return 0


def probe(date_str: str) -> int:
    """T1 探测: 验证竞价接口可用性(非交易日返回最近交易日属正常)"""
    spider = KPLSpider()
    checks = []
    def run(name, fn):
        try:
            data = fn()
            info = data.get("info") or data.get("List") or data.get("list")
            n = len(info) if isinstance(info, list) else "?"
            checks.append(f"  {'✅' if n else '⚠️'} {name}: {n} 条")
        except Exception as e:
            checks.append(f"  ❌ {name}: {e}")
    print(f"== 竞价接口探测 {date_str} ==")
    run("情绪 ChangeStatistics", spider.env_mood)
    run("量能 MarketCapacity", lambda: spider.env_capacity(date_str))
    run("竞价总体 MorningBidding", lambda: spider.env_bid_total(date_str))
    run("竞价数量 MorningBiddingNum", lambda: spider.env_bid_count(date_str))
    run("昨日涨停表现 ZhangTingExpression", lambda: spider.env_zt_expression(date_str))
    run("板块竞价异动 GetBKJJ_W36", lambda: spider.board_bid(date_str))
    run("板块强度 RealRankingInfo", lambda: spider.board_ranking(date_str))
    run("竞价列表 MorningBiddingList", lambda: spider.bid_list(date_str, pid_type=0))
    run("涨停池 DailyLimitPerformance", lambda: spider.zt_pool(date_str))
    print("\n".join(checks))
    print(f"== 探测完成: {sum('✅' in c for c in checks)}/{len(checks)} 可用 ==")
    return 0 if all("✅" in c for c in checks) else 1


def main():
    ap = argparse.ArgumentParser(description="竞价抢筹扫描")
    ap.add_argument("--date", help="扫描日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--probe", action="store_true", help="接口探测模式(T1)")
    ap.add_argument("--confirm", action="store_true", help="E 层开盘确认(09:31)")
    ap.add_argument("--candidates", default="/tmp/auction_candidates.json", help="候选清单路径(--confirm 用)")
    ap.add_argument("--dry-run", action="store_true", help="不推钉钉")
    args = ap.parse_args()

    date_str = args.date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    if args.probe:
        return probe(date_str)
    if args.confirm:
        e_confirm(Path(args.candidates))
        return 0
    return scan(date_str, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
