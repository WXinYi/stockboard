#!/usr/bin/env python3
"""
竞价扫描入口: 采集 → 周期 → 出击选股 → 钉钉推送

09:29 推送 = 出击选股 Top5(盘面页出击Tab 的 9:26 口径存档, stage_candidates.stage_pool)
         + 昨日连板 · 今日竞价换手 Top5。存量漏斗选股(B1-S9 融合分候选/备选)已停跑
         (2026-09-06): 漏斗评分/涨停基因/全池竞价分时采集全部移除, 省去每交易日数百请求。
         V5 竞价首枪仍保留为**内部喂养**(不推送): stage_pool 的"V5容量方向"(发酵/高潮)
         从 auction.json v5 段读数, v5_results 继续落库供 --v5-report 回测积累。

用法(在 jiarenmens/ 目录下执行):
  python scripts/auction_scan.py --probe            # T1 探测: 验证竞价接口可用性
  python scripts/auction_scan.py --dry-run          # 完整扫描, 不推钉钉不写生产快照(本地验证用)
  python scripts/auction_scan.py --date 2026-08-07  # 回放指定日期(历史数据)
  python scripts/auction_scan.py --confirm          # 出击选股开盘确认(09:31, 读 strike_pool)
  python scripts/auction_scan.py --label            # 结果标签: 收盘表现写 candidate_results(收盘后跑)
  python scripts/auction_scan.py --label 2026-08-12 # 结果标签: 历史日期回补(回测样本)
  python scripts/auction_scan.py --backfill-factors # 历史候选日K因子回填(ma60/ret20/macd/kdj; 存量候选专用)

时序: 09:25 cron 触发 → 扫描 → 09:29 钉钉出击推送 → 09:31 --confirm 补推开盘确认。

数据源(2026-08-13 改造): 开盘啦 His 接口(板块/量能/涨停池)只服务**已完成**交易日,
09:25 对"今天"一律 1020 → 当天扫描走**实时路径**(apphwhq 主机, 无日期参数):
实时板块异动 GetBKJJ_W36 + 板块强度 RealRankingInfo(Type=1) → 强势板块 → GetBKJJBL
成分(**含竞价换手**) + MorningBiddingList 四类买入榜单(连板标记 r[16]→身位)
合并成候选池; 情绪用实时 ChangeStatistics。历史回放(--date 过去日期)走原 His 完整路径。
"""
import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from src.analysis import auction_funnel as funnel  # noqa: E402
from src.analysis.emotion_cycle import compute_cycle  # noqa: E402
from src.config import AUCTION_OUT, DATA_DIR  # noqa: E402
from src.notify.dingtalk import DingTalk  # noqa: E402
from src.spiders.auction_spider import AuctionStore, HotRankStore, KPLSpider  # noqa: E402
try:
    from export_json import _tencent_auction_amt  # noqa: E402  # 竞价换手0值补算(同 scripts/ 目录)
except ImportError:  # 以包形式导入(scripts.auction_scan, 单测)时 scripts/ 不在 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from export_json import _tencent_auction_amt  # noqa: E402

BJ_TZ = ZoneInfo("Asia/Shanghai")
WORKERS = 20          # 并发
BOARD_LIMIT = 20      # 强势板块数(8→20: 提高量比覆盖, 减少榜单独有票无量比)
CONTROL_SAMPLE = 20   # 对照组: 过B1门槛池随机抽样数(--label 打标, 存量候选时代样本)
FADE_SAMPLE = 20      # 对照组: 高开低走被拒组抽样数(--label 打标, 负对照, 存量候选时代样本)
STRIKE_TOP = 5        # 09:29 出击选股推送条数
BIDRANK_TOP = 5       # 09:29 昨日连板·竞价换手推送条数

# 阶段闸门镜像(与 stockboard-app/src/utils/leaderBattle.js STAGE_GATE 同步, 仅用于推送文案):
# cap=仓位上限(成), banner=阶段纪律一句话
STAGE_GATE_CN = {
    "退潮": (0, "空仓纪律：退潮期不出击，高位接力亏损率最高，只观察空间锚"),
    "冰点": (30, "冰点期：只做 1进2 套利与新周期火种观察，仓位轻"),
    "启动": (100, "启动期：打低位首板/1进2 为主，情绪低点做龙头"),
    "发酵": (100, "发酵期：上主线龙头/同梯队强者，五板封住定龙头"),
    "高潮": (100, "高潮期：只做龙头接力(秒板/放量分歧板)，跟风不碰"),
    "分歧": (60, "分歧期：只抱团龙头低吸，避开中位股(核按钮高发)"),
}


# =============================================================================
# 数据归一化 → 统一 dict, 字段与 funnel.score_stock 对应
# =============================================================================

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def normalize_bkjjbl(row):
    """GetBKJJBL 行: [代码,名称,现价,实时涨幅,竞价量比,竞价额,竞价涨幅,竞价净额,竞价换手,流通市值,板块标签,...]
    ⚠️ 历史回放时该接口的"现价"已定格为收盘价 → 竞价价须由 现价×实时涨幅/竞价涨幅 反推:
    竞价价 = 现价 × (1+竞价涨幅%) / (1+实时涨幅%)。否则 bid_price 存成收盘价, pct_bid 恒为 0。"""
    price, change_pct, bid_pct = _f(row[2]), _f(row[3]), _f(row[6])
    # 反推失败(竞价涨幅缺失等)时置 None(诚实), 由 MorningBiddingList r[2] 竞价价在合并时补上;
    # 严禁回退成"现价"——收盘后重跑现价=收盘价, 会把 pct_bid 污染成恒 0。
    bid_price = None
    if price is not None and change_pct is not None and change_pct != -100 and bid_pct is not None:
        bid_price = price * (1 + bid_pct / 100) / (1 + change_pct / 100)
    return {
        "code": str(row[0]), "name": row[1], "price": bid_price,  # 竞价价(回放反推, 实时=现价)
        "change_pct": change_pct, "vol_ratio": _f(row[4]),
        "limit_up_buy": _f(row[5]), "bid_pct": bid_pct, "bid_net": _f(row[7]),
        "turnover_ratio": _f(row[8]), "circ_mv": _f(row[9]),
        "plates": str(row[10] or "") if len(row) > 10 else "",
        "tag": str(row[13] or "") if len(row) > 13 else "",
    }


def normalize_bidlist(row):
    """MorningBiddingList 行(实时): [代码,名称,现价,实时涨幅,涨停委买额,竞价涨幅,竞价净额,
    竞价换手,竞价成交额,主力买,主力卖,板块标签,流通市值,...,连板标记]
    ⚠️ 字段修正 2026-08-13: **r[2] 是现价/最新价, 不是竞价价!**(收盘后=收盘价, 曾误当竞价价 →
    pct_bid 恒 0 + E2 判定错). r[3]=实时涨幅, r[5]=竞价涨幅 → 竞价价反推:
    竞价价 = 现价 × (1+竞价涨幅%)/(1+实时涨幅%)。实测: 有研新材 50.21×1.0198/0.9483=54.0=开盘 ✓。
    r[16]=连板标记(身位), 原样本蓝盾光电在涨停价上现价=竞价价恰好重合, 掩盖了错位。"""
    price, change_pct, bid_pct = _f(row[2]), _f(row[3]), _f(row[5])
    bid_price = None
    if price is not None and change_pct is not None and change_pct != -100 and bid_pct is not None:
        bid_price = price * (1 + bid_pct / 100) / (1 + change_pct / 100)
    return {
        "code": str(row[0]), "name": row[1], "price": bid_price,  # 反推竞价价
        "change_pct": change_pct, "vol_ratio": None,  # 无量比字段(量比走 GetBKJJBL 板块成分)
        "limit_up_buy": _f(row[4]), "bid_pct": bid_pct, "bid_net": _f(row[6]),
        "turnover_ratio": _f(row[7]), "main_net": _f(row[8]),
        "unfilled_buy": _f(row[9]) if len(row) > 9 else None,  # r[9]=20分后委买(不可撤单未成交委托, 力3)
        "circ_mv": _f(row[12]) if len(row) > 12 else None,
        "plates": str(row[11] or "") if len(row) > 11 else "",
        "tag": str(row[16] or "") if len(row) > 16 else "",  # '4连板'/'首板' 连板标记(身位)
    }


def _pool_row_layout(item):
    """归一化 dict → save_bid_pool 需要的 MorningBiddingList 原始行布局(17 位, tag 在 r[16])"""
    return [
        item["code"], item["name"], item["price"] or 0, item["bid_pct"] or 0,
        item["limit_up_buy"] or 0, item["bid_pct"] or 0, item["bid_net"] or 0,
        item["turnover_ratio"] or 0, item.get("main_net") or 0,
        item.get("unfilled_buy") or 0, 0,
        item.get("plates") or "", item["circ_mv"] or 0, 0, 0, 0, item["tag"] or "",
    ]


# =============================================================================
# 采集
# =============================================================================

def collect_env(spider, date_str, live=False):
    """环境层采集(容错): 单个接口失败返回 {} 降级, 不打断扫描。
    当天(live=True)用实时变体: 竞价总体/竞价数量(MorningBidding RT)当天可取 → 红盘占比/涨停委买数有值;
    量能 MarketCapacity / 昨日涨停表现 ZhangTingExpression 只服务已完成日(对今天恒 1020) → 当天不调用(无意义),
    缺失由 env_check 处理为报告不阻塞。历史回放(live=False)走 His 完整路径。"""
    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            print(f"⚠️ 环境接口失败: {e}")
            return {}
    bid_total = safe(spider.env_bid_total_live if live else lambda: spider.env_bid_total(date_str))
    bid_count = safe(spider.env_bid_count_live if live else lambda: spider.env_bid_count(date_str)).get("info", [])
    if not live:
        # 量能/昨日涨停表现: His-only, 仅历史回放可取值; 当天跳过(否则必然 1020 噪音)
        capacity = safe(spider.env_capacity, date_str)
        zt_expr = safe(spider.env_zt_expression, date_str)
    else:
        capacity, zt_expr = {}, {}
    return {
        "mood": safe(spider.env_mood),
        "capacity": capacity,
        "bid_total": bid_total,
        "bid_count": bid_count,
        "zt_expr": zt_expr,
    }


def _collect_boards_his(spider, date_str, board_bid=None):
    """历史/回放路径(His 接口, 仅已完成交易日): 板块竞价异动 → 成分股票池 + 昨日涨停池。
    板块成分含竞价量比(GetBKJJBL), 回放完整评分; 涨停池提供连板标记(身位)。"""
    if board_bid is None:
        try:
            board_bid = spider.board_bid(date_str)
        except Exception as e:
            print(f"⚠️ 板块竞价异动失败: {e}")
            board_bid = {}
    try:
        ranking = spider.board_ranking(date_str)
    except Exception as e:
        print(f"⚠️ 板块强度失败: {e}")
        ranking = {"list": []}
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

    # 竞价列表(涨停委买 Top100)并入, 补足板块成分外的强势票(实时接口, 历史回放返回 0 行)
    try:
        bid_rows = spider.bid_list(pid_type=0, st=100).get("info", [])
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


def _collect_boards_live(spider, date_str):
    """当天实时候选池(2026-08-13): 实时板块接口(GetBKJJ_W36 / RealRankingInfo Type=1, 无日期参数)
    返回**当天**板块 → GetBKJJBL 实时成分(**含竞价量比**, S5 当天可用) + MorningBiddingList
    四类买入榜单(0涨停委买/1撮合>2000w/2热门/3主力净额>1000w, 连板标记 r[16]→身位) 合并。
    与 His 路径唯一差别: 接口换实时变体, 板块成分用量比、榜单补身位/补强势票。"""
    # 板块竞价异动 + 强度(实时, 无 Day 参数 → 当天数据)
    try:
        board_bid = spider.board_bid_live()
    except Exception as e:
        print(f"⚠️ 板块竞价异动(实时)失败: {e}")
        board_bid = {}
    try:
        ranking = spider.board_ranking_live()
    except Exception as e:
        print(f"⚠️ 板块强度(实时)失败: {e}")
        ranking = {"list": []}
    boards = funnel.board_select(board_bid, ranking, max_boards=BOARD_LIMIT)

    # 强势板块成分(GetBKJJBL 实时, 量比/净额/换手/流通市值; 收盘后仍返回当日数据)
    pool = {}
    def fetch_stocks(b):
        try:
            data = spider.board_stocks_live(b["code"], st=50)
            return b, data.get("List", [])
        except Exception as e:
            print(f"⚠️ 板块成分失败(实时) {b['name']}: {e}")
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

    # MorningBiddingList 四类买入榜单并入: 补板块成分外的强势票 + 连板标记(身位)
    for pid in range(4):
        try:
            rows = spider.bid_list(pid_type=pid, st=100).get("info", [])
        except Exception as e:
            print(f"⚠️ 竞价列表 PidType={pid} 失败: {e}")
            continue
        for r in rows:
            if len(r) < 13:
                continue
            item = normalize_bidlist(r)
            code = item["code"]
            # KPL 板块标签用中文顿号"、"分隔(如"医药、流感"), 必须纳入拆分
            plates = [p.strip() for p in re.split(r"[,，、/|;；]", item["plates"]) if p.strip()] if item["plates"] else []
            if code in pool:
                old = pool[code]
                old["boards"] = list(dict.fromkeys(old.get("boards", []) + plates))
                for k in ("price", "bid_pct", "bid_net", "turnover_ratio", "main_net",
                          "circ_mv", "limit_up_buy", "tag"):
                    if old.get(k) in (None, "") and item.get(k) not in (None, ""):
                        old[k] = item[k]
            else:
                item["boards"] = plates
                pool[code] = item
    return boards, pool, board_bid, []


def collect_boards(spider, date_str, live=False):
    """板块 → 成分股票池(去重, 记录所属板块) + 昨日涨停池(身位行情/连板标记)。
    live=True(当天扫描): His 接口只服务已完成交易日, 对"今天"一律 1020;
    先试 His 板块路径(若对当天可用则保留完整量比/共振), 失败降级实时竞价列表。
    live=False(历史回放): 原 His 完整路径。"""
    if live:
        try:
            bb = spider.board_bid(date_str)
            if bb.get("List1") or bb.get("List2"):
                print("⚠️ 当天 His 板块接口可用(异常情形), 走完整板块路径")
                return _collect_boards_his(spider, date_str, board_bid=bb)
        except Exception as e:
            # His 对"今天"恒 1020(只服务已完成日), 这是预期路由结果, 非异常 → info 级
            print(f"ℹ️ 走当天实时板块路径(His 不服务当天: {str(e)[:60]})")
        return _collect_boards_live(spider, date_str)
    return _collect_boards_his(spider, date_str)


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
    """腾讯分时: [[0930, 价, 量], ...] 首根 09:30 为开盘分钟。
    返回结构 data[sym]['data']['data'] 是双层, 每行是空格分隔字符串 → split 解析。
    ⚠️ 用 ifzq 主机(web. 曾被 501 风控)。"""
    sym = _qq_symbol(code)
    r = requests.get(
        f"https://ifzq.gtimg.cn/appstock/app/minute/query?code={sym}",
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    rows = r.json()["data"][sym]["data"]["data"]
    return [row.split() for row in rows]


def _mkline_0931(code: str, date_str: str) -> Optional[float]:
    """历史 09:31 价格(腾讯 mkline 1分钟, 带日期戳)。E 层 09:31 确认入场价。
    320 根 ≈ 1.3 交易日, 只覆盖最近 ~1 个完整交易日; 滑出窗口(旧日期)返回 None。
    09:31 bar 无则退化为 09:30(开盘)价。"""
    sym = _qq_symbol(code)
    r = requests.get(f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={sym},m1,,320",
                     timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    d = r.json().get("data", {}).get(sym) or {}
    rows = d.get("m1") or []
    p0931 = p0930 = None
    for row in rows:
        t = str(row[0])
        if not t.startswith(date_str.replace("-", "")):
            continue
        hhmm = t[8:12]
        if hhmm == "0931":
            p0931 = float(row[2])  # close
        elif hhmm == "0930":
            p0930 = float(row[2])
    return p0931 if p0931 is not None else p0930


def _load_strike_picks(date_str: str) -> tuple:
    """读 strike_pool 当时存档(9:26 口径, 与 09:29 推送同一份): (date, stage, picks)。
    date 当日无存档时回退库内最近一条(手动重跑/复盘场景)。"""
    import sqlite3
    db = DATA_DIR / "auction.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT date, stage, picks FROM strike_pool WHERE date=?",
                           (date_str,)).fetchone()
        if row is None:
            row = conn.execute("SELECT date, stage, picks FROM strike_pool "
                               "ORDER BY date DESC LIMIT 1").fetchone()
    if not row:
        return None, None, []
    return row["date"], row["stage"], json.loads(row["picks"])


def e_confirm(date_str: str) -> int:
    """出击选股开盘确认(09:31) → 钉钉补推。
    候选 = strike_pool 存档的出击 Top5(pick_strike_top 与 09:29 推送同口径),
    竞价价取 bid_pool(merged) 撮合价。
    走强标准: 09:31 最新价 ≥ 竞价价 → 守住(兑现); 跌破 → 诱多回落。
    ⚠️ A 股开盘价=集合竞价撮合价, "开盘价>竞价价"机制上恒不成立(2026-08-13 修正, 沿用)。
    存量 E1(首分钟放量 vs 竞价末分钟量)随全池竞价分时采集一并停跑(无 bid_vol 数据源)。"""
    a_date, stage, picks_all = _load_strike_picks(date_str)
    if not picks_all:
        print("⚠️ strike_pool 无存档(扫描未跑或当日周期引擎不可用), 跳过确认推送")
        return 1
    picks, watch_mode = pick_strike_top(picks_all)
    if watch_mode:
        print("⚠️ 当日无出击/备选候选(纪律优先), 跳过确认推送")
        return 0
    store = AuctionStore()
    bid_px_map = {}
    for r in store.load_bid_pool(a_date):
        bid_px_map.setdefault(r["code"], r.get("price"))
    rows = []
    for p in picks:
        try:
            minute = qq_minute(p["code"])
            last_px = float(minute[min(1, len(minute) - 1)][1])
        except Exception as e:
            print(f"⚠️ 腾讯分时失败 {p['code']}: {e}")
            continue
        bid_px = bid_px_map.get(p["code"])
        # 09:31 最新价 ≥ 竞价价 → 守住(未跌破)
        e2 = bool(bid_px and last_px >= bid_px)
        rows.append({"code": p["code"], "name": p["name"], "last_px": last_px,
                     "bid_px": bid_px, "bid_pct": p.get("bid_pct"),
                     "height": p.get("height"), "reason": p.get("reason"), "E2": e2})
    if not rows:
        print("⚠️ 无可用分时数据, 跳过推送")
        return 1
    text = _confirm_text(rows, a_date, stage)
    resp = DingTalk().send_markdown(f"🎯 出击开盘确认 {a_date}", text)
    print(f"📣 钉钉推送: {resp}")
    return 0


def _confirm_text(rows: List[Dict], date_str: str, stage: Optional[str]) -> str:
    """E 层确认消息文本: 09:31 最新价 vs 竞价价(守住/跌破) + 当日身位/理由"""
    ok = [r for r in rows if r["E2"]]
    text = [
        "## ⚡ 出击开盘确认 09:31",
        f"> {date_str}" + (f" · {stage}期" if stage else "") + f" · {len(ok)}/{len(rows)} 只守住竞价价",
        "",
    ]
    for r in rows[:STRIKE_TOP]:
        mark = "🟢" if r["E2"] else "🔴"
        line = f"- {mark} {_stock_link(r['name'], r['code'])} 最新{r['last_px']:.2f}"
        if r["bid_px"]:
            chg = (r["last_px"] - r["bid_px"]) / r["bid_px"] * 100
            line += f" (较竞价{chg:+.2f}%)"
        if r.get("bid_pct") is not None:
            line += f" · 竞价{r['bid_pct']:+.1f}%"
        h = r.get("height") or 0
        if h >= 1:
            line += f" · {h}板"
        text.append(line)
        if r.get("reason"):
            text.append("    " + str(r["reason"])[:60])
    text.append("")
    text.append("📈 [复盘页面](https://WXinYi.github.io/stockboard/#/auction)")
    return "\n".join(text)


# =============================================================================
# 钉钉消息
# =============================================================================

def collect_em_hot(top: int = 100) -> List[Dict]:
    """东财股吧人气榜 TOP100(实时, 无历史接口)。
    返回 [{rank, code, name, rise}];名称用 qt.gtimg 批量补(查不到存空串不阻塞)。"""
    url = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
    body = json.dumps({"appId": "appId01", "globalId": "786e4c21-70dc-435a-93bb-38",
                       "marketType": "", "pageNo": 1, "pageSize": top}).encode()
    req = requests.post(url, data=body, timeout=10,
                        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    req.raise_for_status()
    d = req.json()
    if d.get("code") != 0:
        raise RuntimeError(f"人气榜接口异常: code={d.get('code')}")
    items = d.get("data") or []
    rows = []
    for it in items[:top]:
        sc = it.get("sc", "")            # 形如 SH688836 / SZ002716
        mkt = sc[:2].lower()             # sh / sz
        code = sc[2:]
        rows.append({"rank": it.get("rk"), "code": code, "name": "",
                     "rise": it.get("rc") or 0, "_mkt": mkt})
    # 批量补名称(qt.gtimg 一次可拼多只)
    try:
        q = ",".join(f"{r['_mkt']}{r['code']}" for r in rows)
        resp = requests.get(f"https://qt.gtimg.cn/q={q}", timeout=10,
                            headers={"User-Agent": "Mozilla/5.0"})
        text = resp.content.decode("gbk", "replace")
        name_map = {}
        for seg in text.split(";"):
            seg = seg.strip()
            if "=" not in seg or "~" not in seg:
                continue
            parts = seg.split("~")
            full = parts[0].split("=")[0].strip().lstrip("v_")   # v_sh603618 → sh603618
            code = full[2:] if full[:2] in ("sh", "sz") else full
            name_map[code] = parts[1]
        for r in rows:
            r["name"] = name_map.get(r["code"], "")
    except Exception as e:
        print(f"      ⚠️ 名称补充失败(存空串): {e}")
    for r in rows:
        r.pop("_mkt", None)
    return rows


def hot_rank_job(snap: Optional[str] = None, dry_run: bool = False) -> int:
    """--hot-rank 独立入口: 抓东财人气榜并落 hot_rank.db(crawl.yml 午后调用)。
    snap 自动判定: 当日无快照→'am'(竞价失败兜底), 已有→'pm'。同(date,snap)重复写入跳过。"""
    from src.spiders.auction_spider import HotRankStore
    date_str = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    hr = HotRankStore()
    if snap is None:
        snap = "am" if not hr.has_snapshot(date_str, "am") else "pm"
    try:
        rows = collect_em_hot(top=100)
    except Exception as e:
        print(f"❌ 人气榜采集失败: {e}")
        return 1
    print(f"东财人气榜 {len(rows)} 条 → {date_str}/{snap}" + (" (dry-run 不落库)" if dry_run else ""))
    if not dry_run:
        saved = hr.save_hot_rank(date_str, snap, rows)
        print("已存在同快照,跳过" if not saved else f"✅ 已落库 hot_rank.db ({snap}, 前{len(rows)})")
    return 0


def market_regime(date_str: str) -> Dict:
    """市况判别(三尺): 主线 / 过渡 / 电风扇。
    尺1 接力健康 = 昨日涨停今日再板率(limit_pool 相邻两日交集);
    尺2 空间高度 = 昨日涨停池最高 pid_type(连板位);
    尺3 板块轮动 = board_bid 近3日竞价爆量 TOP1 是否同向。
    判分: 电风扇≥2票 / 主线≥2票(相反方向) / 其余=过渡。数据缺失的尺不投票。"""
    import sqlite3
    out = {"regime": "过渡", "relay": None, "max_board": None, "top1_days": None,
           "detail": []}
    try:
        conn = sqlite3.connect(DATA_DIR / "auction.db")
        conn.row_factory = sqlite3.Row
        # 尺1+尺2: limit_pool 相邻交易日
        rows = list(conn.execute(
            "SELECT date, code, MAX(pid_type) pid FROM limit_pool WHERE date<=? GROUP BY date, code ORDER BY date",
            (date_str,)))
        by_day = {}
        for r in rows:
            by_day.setdefault(r["date"], {})[r["code"]] = r["pid"]
        days = sorted(by_day)
        if len(days) >= 2:
            prev_codes = set(by_day[days[-2]])
            today = by_day[days[-1]]
            if prev_codes:
                relay = len(set(today) & prev_codes) / len(prev_codes)
                out["relay"] = relay
            # 最高板取昨日(昨日涨停的高度决定今日接力预期); 今日池若已有数则并取更大
            heights = list(today.values()) + (list(by_day[days[-2]].values()) if not today else [])
            out["max_board"] = max(heights) if heights else None
        # 尺3: board_bid 近3日竞价爆量 TOP1 同向天数(board_code 按 burst 排)
        bday = {}
        for r in conn.execute("SELECT date, board_code, burst FROM board_bid WHERE date<=?", (date_str,)):
            d = r["date"]
            if d not in bday or (r["burst"] or 0) > (bday[d][1] or 0):
                bday[d] = (r["board_code"], r["burst"])
        bdays = sorted(bday)[-3:]
        if len(bdays) >= 2:
            tops = [bday[d][0] for d in bdays]
            same = sum(1 for t in tops if t == tops[-1])
            out["top1_days"] = same
        conn.close()
    except Exception as e:
        out["detail"].append(f"市况计算异常: {e}")
    # 投票(实盘09:25时库内最新=昨日, 尺1实为"昨日再板率"——已完结数据, 语义正确但文案须准确)
    votes_fan, votes_main = 0, 0
    if out["relay"] is not None:
        if out["relay"] < 0.15:
            votes_fan += 1
        elif out["relay"] > 0.25:
            votes_main += 1
        out["detail"].append(f"昨日再板率{out['relay']:.0%}")
    if out["max_board"] is not None:
        if out["max_board"] <= 3:
            votes_fan += 1
        elif out["max_board"] >= 5:
            votes_main += 1
        out["detail"].append(f"最高{out['max_board']}板")
    if out["top1_days"] is not None:
        if out["top1_days"] <= 1:
            votes_fan += 1
        elif out["top1_days"] >= 3:
            votes_main += 1
        out["detail"].append(f"TOP1连续{out['top1_days']}日")
    if votes_fan >= 2:
        out["regime"] = "电风扇"
    elif votes_main >= 2:
        out["regime"] = "主线"
    return out


def screen_v5(date_str: str, pool: Dict[str, Dict], limit_codes: set,
              stock_bids: Optional[Dict[str, List]] = None,
              cycle_res: Optional[Dict] = None) -> List[Dict]:
    """V5.2 开盘首枪筛选(电风扇版): 竞价涨幅2~6% + 流通市值>50亿 + 换手≥0.15% + 非ST。
    身位分层: 首板/2连板=正常仓可主攻; 3连板以上 或 昨日大阳≥6%(非涨停) = ⚠️半仓。
    分时形态软化(v5.2): 竞价高开低走(复用老系统 _is_high_open_fade)不再一票否决 → ⚠️半仓
    (8/21 实证: 该门拦掉沃森+2.4/通鼎+5.7/飞龙+4.8 三只大肉, 也拦掉中京-4.9 一只大坑 → 降级为仓位调节器)。
    昨日涨幅按 date_str 显式锚定取前一交易日(回放/实时窗口一致)。
    第五刀·周期闸门(v5.3): 选股池由情绪周期产生 —
      退潮/冰点 → V5 静默(全部转 v5_off_cycle, 只落库供回测);
      分歧     → 仅主线板块内, 且一律半仓;
      发酵/高潮 → 仅主线板块内;
      引擎不可用 → 不闸门(降级为 v5.2 原行为)。"""
    def _prev_pct(code):
        """date_str 前一交易日的涨幅(腾讯日K, 锚定日期防回放错位)"""
        code = str(code)
        mkt = "sh" if code[0] in "659" else ("bj" if code[0] in "48" else "sz")
        sym = f"{mkt}{code}"
        url = f"http://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,15,qfq"
        try:
            with requests.get(url, timeout=8) as r:
                d = r.json()
            kline = d.get("data", {}).get(sym, {}).get("qfqday") \
                or d.get("data", {}).get(sym, {}).get("day") or []
            prior = [float(k[2]) for k in kline if len(k) >= 3 and k[0] < date_str]
            if len(prior) >= 2 and prior[-2] > 0:
                return (prior[-1] / prior[-2] - 1) * 100
        except Exception:
            pass
        return None

    def _board_height(tag: str) -> int:
        """连板标记 → 板高: '首板'=1, '3连板'=3, 无标记=0"""
        t = tag or ""
        if "首板" in t:
            return 1
        m = re.search(r"(\d+)\s*连板", t)
        return int(m.group(1)) if m else 0

    out = []
    for item in pool.values():
        bid = item.get("bid_pct")
        mv = item.get("circ_mv") or 0
        turn = item.get("turnover_ratio") or 0
        name = item.get("name") or ""
        if bid is None or not (2.0 <= bid < 6.0):
            continue
        # 对照组采集: 竞价达标但被换手/市值门槛拒掉的票 → group_tag 标记后继续走
        # (v5_rej_turn=换手不足被拒, 验证"换手≥0.15硬门"是否真的剔除假转强; 市值不足同理)
        if "ST" not in name.upper():
            if turn < 0.15:
                out.append({
                    "code": item["code"], "name": name,
                    "bid_pct": bid, "turnover": turn, "circ_mv": mv,
                    "prev_pct": None, "height": 0,
                    "was_limit": False, "fade": False, "half_pos": True,
                    "pos_tag": None, "group_tag": "v5_rej_turn",
                    "boards": [b for b in re.split(r"[,，、/|;；]", item.get("plates") or "") if b][:2],
                })
                continue
            if mv <= 5e9:
                out.append({
                    "code": item["code"], "name": name,
                    "bid_pct": bid, "turnover": turn, "circ_mv": mv,
                    "prev_pct": None, "height": 0,
                    "was_limit": False, "fade": False, "half_pos": True,
                    "pos_tag": None, "group_tag": "v5_rej_mv",
                    "boards": [b for b in re.split(r"[,，、/|;；]", item.get("plates") or "") if b][:2],
                })
                continue
        else:
            continue
        height = _board_height(item.get("tag"))
        prev = _prev_pct(item["code"])
        was_limit = item["code"] in limit_codes
        prev_yang = (prev is not None and prev >= 6.0 and not was_limit)
        # 分时形态(软化): 高开低走 → 半仓标记, 不否决。分时来自 scan 第4步 GetStockBid(内存直传)
        bid_rows = (stock_bids or {}).get(item["code"])
        fade = bool(bid_rows) and funnel._is_high_open_fade(bid_rows)
        half = height >= 3 or prev_yang or fade  # 高位板 / 昨日大阳 / 竞价高开低走 → 半仓
        out.append({
            "code": item["code"], "name": name,
            "bid_pct": bid, "turnover": turn, "circ_mv": mv,
            "prev_pct": prev, "height": height,
            "was_limit": was_limit, "fade": fade, "half_pos": half,
            "pos_tag": None, "group_tag": "v5",
            "boards": [b for b in re.split(r"[,，、/|;；]", item.get("plates") or "") if b][:2],
        })
        time.sleep(0.03)
    # ── 第五刀(周期闸门): 选股池由情绪周期产生, 对照组(v5_rej_*)不受闸门影响 ──
    stage = (cycle_res or {}).get("stage")
    mainlines = {m["board"] for m in (cycle_res or {}).get("mainlines", [])} or None
    for v in out:
        v["cycle_stage"] = stage
        v["in_main"] = bool(mainlines) and bool(set(v["boards"]) & mainlines)
    if stage:
        if stage in ("退潮", "冰点"):
            for v in out:
                if v["group_tag"] == "v5":
                    v["group_tag"] = "v5_off_cycle"
        else:
            for v in out:
                if v["group_tag"] == "v5":
                    if not v["in_main"]:
                        v["group_tag"] = "v5_off_cycle"
                    elif stage == "分歧":
                        v["half_pos"] = True  # 分歧期一律半仓
    # 排序纯看质量: 换手高优先 → 竞价涨幅高优先(身位只做仓位标注, 不参与排序/资格)
    # 排序后授予 pos_tag(main/sub, 与推送一致), 落库供回测区分主攻/次攻收益
    # 主攻/次攻只在通过周期闸门的 v5 行里授予
    out.sort(key=lambda x: (-x["turnover"], -x["bid_pct"]))
    main_idx = next((i for i, v in enumerate(out)
                     if v["group_tag"] == "v5" and not v["half_pos"]), None)
    sub_idx = next((i for i, v in enumerate(out)
                    if v["group_tag"] == "v5" and not v["half_pos"] and i != main_idx), None)
    if main_idx is not None:
        out[main_idx]["pos_tag"] = "main"
    if sub_idx is not None:
        out[sub_idx]["pos_tag"] = "sub"
    return out


def _stock_link(name: str, code: str) -> str:
    """钉钉 markdown 链接 → 原生股票详情页(/stock/:code, 与 app 内部及 notify_daily/watched_flash 一致)"""
    url = f"https://WXinYi.github.io/stockboard/#/stock/{code}?name={quote(name)}"
    return f"[{name}]({url})"


def pick_strike_top(picks: List[Dict], top_n: int = STRIKE_TOP) -> tuple:
    """出击选股 Top5(纯函数, 单测覆盖)。
    状态映射(与盘面页 leaderBattle 对齐): 可做*=出击, 可做(矩阵谨慎)=备选, 其余=观察。
    优先出击 → 备选; 都没有则回退观察名单(watch_mode=True, 纪律优先只展示不买)。
    保持 stage_pool 存档顺序(谱系→扩展→弱转强→容量), 不重排。"""
    go = [p for p in picks if (p.get("status") or "").startswith("可做")
          and "矩阵谨慎" not in (p.get("status") or "")]
    care = [p for p in picks if "矩阵谨慎" in (p.get("status") or "")]
    picked = (go + care)[:top_n]
    if picked:
        return picked, False
    return [p for p in picks if (p.get("status") or "").startswith("观察")][:top_n], True


def rank_lianban_bid(rows: List[Dict], top_n: int = BIDRANK_TOP) -> List[Dict]:
    """昨日连板 · 今日竞价换手 Top5(纯函数, 单测覆盖)。
    rows: 已装配的候选行 [{code,name,height,bid_pct,turnover}] (装配口径在 scan():
    连板名单=limit_pool 前一交易日 pid_type>=2; 换手=KPL turnover_ratio 优先,
    0值腾讯 0930 竞价额/流通市值补算 —— 与 export_json.build_lianban_bid / 盘面页
    bidTop 标记同口径)。规则: 换手>0 才参与 → 换手高优先 → 竞价涨幅高优先。"""
    rows = [r for r in rows if (r.get("turnover") or 0) > 0
            and "ST" not in (r.get("name") or "").upper()]
    rows.sort(key=lambda x: (-x["turnover"], -(x.get("bid_pct") or 0)))
    return rows[:top_n]


def _strike_line(i: int, p: Dict) -> List[str]:
    """出击选股单只消息行: 状态图标 + 名称(链接) + 身位 + 竞价 + 状态与理由"""
    st = p.get("status") or ""
    icon = "🟡" if "矩阵谨慎" in st else ("🔴" if st.startswith("可做") else "⚪")
    h = p.get("height") or 0
    pos = f"{h}连板" if h >= 2 else ("首板" if h == 1 else "")
    bid = f" 竞价{p['bid_pct']:+.1f}%" if p.get("bid_pct") is not None else ""
    return [f"{i}. {icon} {_stock_link(p['name'], p['code'])} {pos}{bid}".rstrip(),
            f"   {st} · {(p.get('reason') or '')[:60]}"]


def build_strike_message(date_str: str, crawl_time: str, cycle_res: Optional[Dict],
                         regime: Optional[Dict], env: Dict, picks: List[Dict],
                         watch_mode: bool, bidrank: List[Dict]) -> str:
    """09:29 推送正文: 周期+纪律+环境 → 出击选股 Top5 → 昨日连板·竞价换手 Top5"""
    e = env["data"]
    lines = [f"## 🎯 今日出击 {crawl_time}", f"> {date_str}"]
    if cycle_res:
        cap, banner = STAGE_GATE_CN.get(cycle_res["stage"], (100, ""))
        cap_txt = "禁买" if cap == 0 else f"{cap}成"
        reg_txt = f" · 📡 市况{regime['regime']}" if regime else ""
        lines.append(f"**周期: {cycle_res['stage']}**(置信度 {cycle_res['confidence']}/9)"
                     f" · 仓位上限 {cap_txt}{reg_txt}")
        if banner:
            lines.append(f"> {banner}")
    env_parts = []
    if e["red_ratio"] is not None:
        env_parts.append(f"竞价红盘{e['red_ratio']:.0%}")
    bc = e.get("bid_count") or []
    if len(bc) >= 2 and bc[0]:
        env_parts.append(f"竞价委买{bc[0]}只")
    tj, lj = e.get("bid_total"), e.get("bid_total_prev")
    if tj:
        env_parts.append(f"竞价{tj}" + (f"(昨{lj})" if lj else ""))
    env_parts.append(f"昨情绪{e['strong']}·昨连板{e['lbgd']}")
    lines.append(f"**环境**: {' · '.join(env_parts)}")
    lines.append("")
    if watch_mode:
        lines.append("**本阶段无出击候选(纪律优先)** — 仅观察名单:")
    else:
        lines.append(f"**🎯 出击选股 {len(picks)} 只**:")
    for i, p in enumerate(picks, 1):
        lines += _strike_line(i, p)
    if bidrank:
        lines += ["", f"**🪜 昨日连板 · 竞价换手 Top{len(bidrank)}**:"]
        for i, b in enumerate(bidrank, 1):
            h = b.get("height") or 0
            pos = f"{h}连板" if h >= 2 else ("首板" if h == 1 else "")
            bid = f" 竞价{b['bid_pct']:+.1f}%" if b.get("bid_pct") is not None else ""
            lines.append(f"{i}. {_stock_link(b['name'], b['code'])} {pos}{bid} "
                         f"· 换手{b['turnover']:.2f}%".strip())
        lines.append("> 换手=竞价实际成交换手(09:25口径); 竞价无成交的连板股不参与排名")
    lines += ["", "📈 [复盘页面](https://WXinYi.github.io/stockboard/#/auction)"]
    return "\n".join(lines)


# =============================================================================
# 结果标签(收盘后 15:05 定时或手动回补): 选股 → 当日实际表现
# =============================================================================

def _day_ohlc(code: str, date_str: str) -> Optional[List[float]]:
    """腾讯日K(fqkline)取指定日期 [open, high, low, close]; 无该日K线(停牌/未上市)返回 None。
    行格式 [date, open, close, high, low, volume, ...]。"""
    sym = _qq_symbol(code)
    r = requests.get(f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,320,qfq",
                     timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json().get("data", {}).get(sym) or {}
    rows = data.get("qfqday") or data.get("day") or []
    for row in rows:
        parts = row if isinstance(row, (list, tuple)) else row.split(",")
        if not parts or not str(parts[0]).startswith(date_str):
            continue
        return [float(parts[1]), float(parts[3]), float(parts[4]), float(parts[2])]  # O H L C
    return None


def _day_series(code: str) -> List[List]:
    """腾讯日K(fqkline, 320条qfq)全量行: [[date, open, close, high, low], ...]。
    文章因子(#6 站上MA60 / #7 MACD·KDJ / #8 低位启动)的日K数据源。"""
    sym = _qq_symbol(code)
    r = requests.get(f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,320,qfq",
                     timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json().get("data", {}).get(sym) or {}
    rows = data.get("qfqday") or data.get("day") or []
    out = []
    for row in rows:
        parts = row if isinstance(row, (list, tuple)) else row.split(",")
        if len(parts) >= 5:
            out.append([str(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])])
    return out


def _prev_close(code: str, date_str: str) -> Optional[float]:
    """候选日前一交易日收盘价(腾讯日K, 用于当天涨跌幅 pct_day)。无则返回 None。"""
    series = _day_series(code)
    prev = None
    for row in series:
        if row[0] < date_str:
            prev = row[2]  # close
        else:
            break
    return prev


def _ema(vals: List[float], n: int) -> List[float]:
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def _macd_ok(closes: List[float]) -> Optional[bool]:
    """MACD 向上: 红柱 或 (绿柱缩短 且 DIF>DEA); 样本<35 返回 None"""
    if len(closes) < 35:
        return None
    dif = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    dea = _ema(dif, 9)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return hist[-1] > 0 or (hist[-1] > hist[-2] and dif[-1] > dea[-1])


def _kdj_ok(highs: List[float], lows: List[float], closes: List[float]) -> Optional[bool]:
    """KDJ 金叉/向上发散(K>D 且 J>D); 样本不足返回 None"""
    n = 9
    if len(closes) < n + 5:
        return None
    K = D = 50.0
    for i in range(len(closes)):
        lo, hi = min(lows[max(0, i - n + 1):i + 1]), max(highs[max(0, i - n + 1):i + 1])
        rsv = 50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100
        K = K * 2 / 3 + rsv / 3
        D = D * 2 / 3 + K / 3
    J = 3 * K - 2 * D
    return K > D and J > D


def enrich_dayk_factors(item: Dict, date_str: str) -> None:
    """为单个候选采集文章日K因子(决策时点 = date_str 前一收盘, 排除当天形成中的K线):
    写回 item['factors']: ma60_above(1/0), ret20(近20日涨幅), macd_ok(1/0), kdj_ok(1/0)。
    日K失败置 None 容忍(不崩扫描), 竞价价用 factors['bid_price']。"""
    code = item["code"]
    f_ = item["factors"]
    px = f_.get("bid_price")
    try:
        kl = [r for r in _day_series(code) if r[0] < date_str]  # 决策时点=前一收盘
        if len(kl) < 21 or px is None:
            return
        closes = [r[2] for r in kl]
        highs = [r[3] for r in kl]
        lows = [r[4] for r in kl]
        if len(closes) >= 60:
            f_["ma60_above"] = 1 if px > sum(closes[-60:]) / 60 else 0
        f_["ret20"] = px / closes[-21] - 1
        f_["macd_ok"] = 1 if _macd_ok(closes) is True else 0
        f_["kdj_ok"] = 1 if _kdj_ok(highs, lows, closes) is True else 0
    except Exception as e:
        print(f"⚠️ 日K因子失败 {code}: {e}")


def backfill_factors(date_str: Optional[str] = None) -> int:
    """历史候选日K因子回填(--backfill-factors): 对 candidates 中 ma60_above IS NULL 的行,
    用腾讯日K(行 date < 候选日)回填 ma60_above/ret20/macd_ok/kdj_ok。
    ⚠️ bid_buy_ratio/bid_vol_total 无历史 GetStockBid 数据, 留 NULL(从今起采集)。
    返回回填行数。"""
    store = AuctionStore()
    import sqlite3 as _sq
    conn = _sq.connect(store.db_path)
    rows = conn.execute("SELECT date, code, bid_price FROM candidates "
                        "WHERE ma60_above IS NULL OR fused_score IS NULL").fetchall()
    done = 0
    for d, code, bid_px in rows:
        if bid_px is None:
            continue
        item = {"code": code, "factors": {"bid_price": bid_px}}
        enrich_dayk_factors(item, d)
        f = item["factors"]
        # 同步按融合规则算 S7/融合分/融合层, 保持与当日扫描口径一致(回测用)
        s7, _note = funnel.article_s7(f)
        s7v = s7 if f.get("ma60_above") is not None else None  # 日K因子成功才有效
        score_v = conn.execute("SELECT score, s1 FROM candidates WHERE date=? AND code=?",
                               (d, code)).fetchone()
        s1 = score_v[1] if score_v else 0
        fused = round(float(score_v[0] or 0) + s7, 2) if score_v else None
        tier = "core" if (fused is not None and fused >= funnel.FUSED_CORE and s1 > 0) else "watch"
        conn.execute("""UPDATE candidates SET ma60_above=?, ret20=?, macd_ok=?, kdj_ok=?,
                        s7=?, fused_score=?, tier=? WHERE date=? AND code=?""",
                     (f.get("ma60_above"), f.get("ret20"), f.get("macd_ok"), f.get("kdj_ok"),
                      s7v, fused, tier, d, code))
        done += 1
    conn.commit()
    conn.close()
    print(f"✅ 日K因子回填 {done} 行")
    return done


def _quote_now(code: str) -> Optional[float]:
    """腾讯即时行情现价(仅当日收盘后/同一天可用; 历史回补不适用)"""
    sym = _qq_symbol(code)
    r = requests.get(f"https://qt.gtimg.cn/q={sym}", timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    try:
        return float(r.text.split('"')[1].split("~")[3])
    except (IndexError, ValueError):
        return None


def label_results(date_str: str) -> int:
    """结果标签: 抓当日开/收/高/低 → 关联竞价价算 pct_open/pct_bid → 写 candidate_results。
    收盘后(15:05)定时跑或手动对历史日期回补, 是回测样本积累的基础。
    存量漏斗候选停跑(2026-09-06)后 candidates 不再新增 → 候选打标自然空跑;
    V5 打标(v5_results → T+0/T+1)不依赖 candidates, 必须继续执行。"""
    store = AuctionStore()
    cands = store.load_candidates(date_str)
    if cands:
        # 日期隔离: 先清当日"已不在候选名单"的旧结果行(候选集随 BOARD_LIMIT/融合规则变化)
        codes = tuple(c["code"] for c in cands)
        with store._conn() as c:
            # 只清"过时候选"行(role IS NULL); 对照组行(role 非空)保留, 避免重打标被删
            c.execute(f"DELETE FROM candidate_results WHERE date=? AND role IS NULL AND "
                      f"code NOT IN ({','.join('?'*len(codes))})", (date_str, *codes))
        print(f"[标签] {date_str} 共 {len(cands)} 只候选, 抓取当日行情...")
        n_ok = 0
        for cand in cands:
            if _label_one(store, date_str, cand["code"], cand["name"], cand["bid_price"]):
                n_ok += 1
        print(f"✅ 已打标签 {n_ok}/{len(cands)} 只 → candidate_results")
        # ---- 对照组打标(回测对比基准): 随机池基准 + 高开低走被拒负对照 ----
        n_ctl = _label_controls(store, date_str, cands)
        print(f"✅ 对照组打标 {n_ctl} 只 (control/FADE) → candidate_results")
    else:
        print(f"[标签] {date_str} 无漏斗候选(存量选股已停跑或非交易日), 跳过候选打标")
    # ---- V5 开盘首枪打标(T+0 当日 + T+1 次日回补) ----
    label_v5(date_str, store)
    return 0


def label_v5(date_str: str, store: AuctionStore) -> int:
    """V5 结果打标(v5_results):
    - T+0(当日 15:05 跑): 开盘价/收盘价 → pct_open(开盘买→当日收, V5 主口径)/pct_day
    - T+1(次日起任意日期跑): 次日开盘跳空/次日持有收益/是否触及-3%止损线
      → 「昨日强组 vs 低位组」的判决字段(next_*), 卖出纪律验证的数据基础。
    幂等: 已有当日行情的跳过重抓; next_* 缺失才补抓。"""
    rows = store.load_v5_results(date_str)
    if not rows:
        print(f"[V5标签] {date_str} 无 v5_results, 跳过")
        return 0
    today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    n_t0 = n_t1 = 0
    series_cache: Dict[str, List] = {}
    for r in rows:
        code = r["code"]
        try:
            if r.get("close_px") is None or r.get("pct_open") is None:
                ohlc = _day_ohlc(code, date_str)
                if not ohlc or not ohlc[3]:
                    continue
                open_px, close_px = ohlc[0], ohlc[3]
                prev_close = None
                try:
                    prev_close = _prev_close(code, date_str)
                except Exception:
                    pass
                pct_open = (close_px - open_px) / open_px if open_px else None
                pct_day = (close_px - prev_close) / prev_close if prev_close else None
                store.label_v5_result(date_str, code, open_px, close_px, pct_open, pct_day)
                r.update({"open_px": open_px, "close_px": close_px,
                          "pct_open": pct_open, "pct_day": pct_day})
                n_t0 += 1
                time.sleep(0.08)
            # T+1: 找 date_str 之后第一个交易日的次日表现(昨日强组的判决日)
            if r.get("next_close_pct") is None and r.get("open_px"):
                if code not in series_cache:
                    try:
                        series_cache[code] = _day_series(code)
                    except Exception:
                        series_cache[code] = []
                after = [k for k in series_cache[code] if k[0] > date_str]
                if not after:
                    continue  # 尚无次日K线(当天跑), 下次 --label 再补
                nd = after[0]  # [date, open, close, high, low]
                buy_px = r["open_px"]  # V5 买入口径=当日开盘价
                stop_line = buy_px * 0.97  # -3% 止损线
                store.label_v5_next(
                    date_str, code, nd[0],
                    next_open_pct=(nd[1] / r["close_px"] - 1) if r["close_px"] else None,
                    next_close_pct=(nd[2] / nd[1] - 1) if nd[1] else None,  # 次日开盘买→次日收
                    next_stop_hit=1 if nd[3] < stop_line else 0,  # 次日最低触及止损线
                )
                n_t1 += 1
                time.sleep(0.08)
        except Exception as e:
            print(f"⚠️ V5 打标失败 {code}: {e}")
    print(f"✅ V5 打标: T+0 {n_t0} 只, T+1 次日 {n_t1} 只 → v5_results")
    return 0


def v5_report(min_n: int = 3) -> int:
    """V5 回测报告: 从 v5_results 汇总分组表现, 是策略参数调整的唯一依据。
    分组维度(全部来自选股时点快照, 无后视):
      - group_tag: v5候选 / v5_rej_turn(换手<0.15被拒对照) / v5_rej_mv(市值≤50亿被拒对照)
      - half_pos:  全仓 / 半仓(3板以上·昨日大阳·竞价回落)
      - was_limit+prev_pct: 昨日涨停组 / 昨日大阳组 / 低位转强组
      - pos_tag:   主攻 / 次攻 / 普通
    口径: pct_open=开盘买→当日收(T+0); next_close_pct=次日开盘买→次日收(T+1, 卖出纪律口径);
         next_stop_hit=次日是否触及-3%止损线。样本 <min_n 标⚠️不解读。"""
    store = AuctionStore()
    with store._conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM v5_results ORDER BY date")]
    if not rows:
        print("v5_results 为空(尚无积累)")
        return 0

    def _grp(rs, key_fn):
        buckets: Dict[str, List] = {}
        for r in rs:
            buckets.setdefault(key_fn(r), []).append(r)
        return buckets

    def _stat(vals):
        if not vals:
            return None
        n = len(vals)
        win = sum(1 for v in vals if v > 0) / n
        avg = sum(vals) / n
        return f"n={n} 胜率{win:.0%} 均值{avg:+.2f}%" + ("" if n >= min_n else " ⚠️样本不足")

    def _prev_group(r):
        if r.get("was_limit"):
            return "昨日涨停"
        p = r.get("prev_pct")
        if p is None:
            return "昨涨未知"
        return "昨日大阳≥6%" if p >= 6.0 else "低位转强"

    cand_rows = [r for r in rows if r.get("group_tag") == "v5"]
    print("=" * 72)
    print(f"V5 回测报告 · 样本期 {rows[0]['date']} ~ {rows[-1]['date']} · 总行 {len(rows)}")
    print("=" * 72)

    for title, key_fn, field in [
        ("① 分组对照(候选 vs 换手被拒 vs 市值被拒)", lambda r: r.get("group_tag"), "pct_open"),
        ("② 仓位分层(全仓 vs 半仓)", lambda r: "半仓" if r.get("half_pos") else "全仓", "pct_open"),
        ("③ 昨日身位分组(判决「昨日强 vs 低位」)", _prev_group, "pct_open"),
        ("④ 主攻/次攻/普通", lambda r: {"main": "主攻", "sub": "次攻"}.get(r.get("pos_tag")) or "普通", "pct_open"),
    ]:
        print(f"\n── {title} ── [T+0 开盘买→当日收]")
        for g, rs in sorted(_grp(cand_rows, key_fn).items()):
            s = _stat([r[field] * 100 for r in rs if r.get(field) is not None])
            if s:
                print(f"  {g:<14} {s}")

    # T+1: 昨日强组 vs 低位组的次日判决(核心待验证假设)
    t1_rows = [r for r in cand_rows if r.get("next_close_pct") is not None]
    if t1_rows:
        print(f"\n── ⑤ 次日兑现(T+1, 已有次日数据 {len(t1_rows)} 只) ── [次日开盘买→次日收 | 隔夜跳空]")
        for g, rs in sorted(_grp(t1_rows, _prev_group).items()):
            vals_nc = [r["next_close_pct"] * 100 for r in rs if r.get("next_close_pct") is not None]
            vals_no = [r["next_open_pct"] * 100 for r in rs if r.get("next_open_pct") is not None]
            s_nc, s_no = _stat(vals_nc), _stat(vals_no)
            stops = sum(1 for r in rs if r.get("next_stop_hit"))
            stop_txt = f" 触止损{stops}/{len(rs)}" if rs else ""
            if s_nc:
                print(f"  {g:<14} 次日持有 {s_nc} | 隔夜跳空 {s_no or '—'}{stop_txt}")
    else:
        print("\n── ⑤ 次日兑现 ── (尚无 T+1 数据, 需次日起再跑 --label)")

    # 止损纪律统计
    stop_rows = [r for r in cand_rows if r.get("next_stop_hit") is not None]
    if stop_rows:
        hits = sum(r["next_stop_hit"] for r in stop_rows)
        print(f"\n── ⑥ 止损线(-3%)触及率 ── {hits}/{len(stop_rows)} "
              f"({hits/len(stop_rows):.0%}) ← 若>30% 说明竞价追高入场价普遍偏贵")

    # 对照组(被拒票)与候选的差值 = 门槛有效性
    for rej_tag, desc in [("v5_rej_turn", "换手<0.15被拒(门槛验证: 应显著差于候选)"),
                          ("v5_rej_mv", "市值≤50亿被拒")]:
        rej = [r for r in rows if r.get("group_tag") == rej_tag]
        vals = [r["pct_open"] * 100 for r in rej if r.get("pct_open") is not None]
        s = _stat(vals)
        if s:
            print(f"  对照[{rej_tag}] {desc}: {s}")
    print()
    return 0


def _label_one(store, date_str: str, code: str, name: str, bid_price, role=None) -> bool:
    """单只打标: 抓当日行情 → 算 pct_day/pct_bid/pct_open_day/pct_e31 → 落库。返回是否成功。"""
    ohlc = None
    try:
        ohlc = _day_ohlc(code, date_str)
    except Exception as e:
        print(f"⚠️ 日K失败 {code}: {e}")
    today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    if not ohlc and date_str == today:
        # 北交所/日K缺失 → 当日即时行情兜底(仅当天可用, 历史回补跳过)
        try:
            close = _quote_now(code)
            if close:
                ohlc = [None, None, None, close]
        except Exception as e:
            print(f"⚠️ 即时行情失败 {code}: {e}")
    if not ohlc or not ohlc[3]:
        print(f"⚠️ {code} {name} 无当日行情, 跳过")
        return False
    open_px, high_px, low_px, close_px = ohlc
    pct_open = (close_px - open_px) / open_px if (open_px and open_px > 0) else None
    pct_bid = (close_px - bid_price) / bid_price if (bid_price and bid_price > 0) else None
    pct_day = None
    try:
        prev_close = _prev_close(code, date_str)
        if prev_close and prev_close > 0:
            pct_day = (close_px - prev_close) / prev_close  # 当天涨跌幅(收盘相对昨收)
    except Exception as e:
        print(f"⚠️ 昨收失败 {code}: {e}")
    pct_open_day = (close_px - open_px) / open_px if (open_px and open_px > 0) else None
    pct_e31 = None
    try:
        px0931 = _mkline_0931(code, date_str)  # E层 09:31 入场价(仅最近日可回补)
        if px0931 and px0931 > 0:
            pct_e31 = (close_px - px0931) / px0931
    except Exception as e:
        print(f"⚠️ 09:31价失败 {code}: {e}")
    store.save_candidate_result(date_str, code, open_px, high_px, low_px, close_px,
                                pct_open, pct_bid, pct_day, pct_open_day, pct_e31, role=role)
    return True


def _label_controls(store, date_str: str, cands) -> int:
    """对照组打标:
    - control     = 过 B1 门槛(1≤bid_pct≤7)池随机抽样(确定性 seed=日期), 全池基准/随机对照
    - FADE        = 高开低走被拒组抽样, 负对照(验证硬门是否剔除 loser)
    与候选互斥, 每日各上限 CONTROL_SAMPLE/FADE_SAMPLE, 控制腾讯日K请求量。"""
    import random
    rng = random.Random(date_str)  # 同日重跑抽样一致
    picked = {c["code"] for c in cands}
    pool = store.load_bid_pool(date_str)
    bid_map = {p["code"]: p.get("price") or 0 for p in pool}
    in_gate = [p for p in pool
               if (p.get("bid_pct") is not None and 1 <= p["bid_pct"] <= 7
                   and p["code"] not in picked)]
    rejected = store.load_rejected(date_str)
    fade_codes = {r["code"] for r in rejected if r["reason"] == "竞价高开低走 出货形态"}
    # control: 随机池基准(排除高开低走被拒, 避免与负对照重复)
    avail = [p for p in in_gate if p["code"] not in fade_codes]
    sample = rng.sample(avail, min(CONTROL_SAMPLE, len(avail))) if avail else []
    rows = [(p["code"], p["name"], bid_map.get(p["code"]), "control") for p in sample]
    # FADE: 高开低走被拒负对照(抽样)
    faded = [r for r in rejected if r["code"] in fade_codes and r["code"] not in picked]
    rows += [(r["code"], r["name"], bid_map.get(r["code"]), "FADE")
             for r in faded[:FADE_SAMPLE]]
    n = 0
    for code, name, bid_price, role in rows:
        if _label_one(store, date_str, code, name, bid_price, role=role):
            n += 1
    return n


# =============================================================================
# 主流程
# =============================================================================

def _fallback_trading_day(spider: KPLSpider, date_str: str) -> str:
    """解析交易日: 周末/节假日无市场数据 → 回退最近交易日(最多回溯5天)。

    关键: His 接口(GetBKJJ_W36/GetBKJJBL/MarketCapacity 等)只服务**已完成**交易日,
    09:25 对"今天"一律 1020 —— 不能用它们判定当天(env_capacity 原探测因此每个工作日误回退, 历史 bug 根因)。
    - 当天: 用 MorningBiddingList(当日实时)判定; 工作日内即使竞价列表暂时无数据
      也按"今天"处理(cron 仅在交易日触发, 宁可当天空仓也不回退旧数据)。
    - 历史: 用 board_bid(GetBKJJ_W36, His, 已完成交易日有数据)判定。
    """
    from datetime import timedelta
    now = datetime.now(BJ_TZ)
    today = now.strftime("%Y-%m-%d")
    is_weekday = now.weekday() < 5
    cur = date_str
    for _ in range(6):
        try:
            if cur == today:
                info = spider.bid_list(pid_type=0, st=100).get("info") or []
                if info or is_weekday:
                    return cur
            else:
                spider.board_bid(cur)
                return cur
        except RuntimeError:
            pass
        print(f"⚠️ {cur} 无市场数据(周末/节假日), 回退上一天")
        cur = (datetime.strptime(cur, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    raise RuntimeError(f"最近5天均无市场数据({date_str} 起回溯)")


def scan(date_str: str, dry_run: bool = False) -> int:
    """主扫描: 采集 → 周期 → 出击选股 Top5 + 昨日连板·竞价换手 Top5 → 钉钉推送。
    存量漏斗选股(B1-S9 融合候选/备选)已停跑(2026-09-06): 不再采集涨停基因与全池竞价分时,
    不再算漏斗评分与 S7 融合, 不再写 candidates/rejected —— 省去每交易日数百请求。
    V5 首枪保留为**内部喂养**(不推送): stage_pool 发酵/高潮的容量方向从 auction.json 的
    v5 段读数; v5_results 继续落库供 --label/--v5-report 回测积累。"""
    t0 = time.time()
    spider = KPLSpider()
    date_str = _fallback_trading_day(spider, date_str)
    today_str = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    live = (date_str == today_str)  # 当天扫描 → 实时数据源(His 接口对今天无数据)
    store = AuctionStore()
    crawl_time = datetime.now(BJ_TZ).strftime("%H:%M")

    print(f"[1/5] 环境层采集 {date_str} ({'当天实时' if live else '历史回放'})")
    env = collect_env(spider, date_str, live=live)
    store.save_mood(date_str, env["mood"], env["capacity"], env["bid_total"],
                    env["bid_count"], env["zt_expr"])

    print(f"[2/5] 板块层 + 竞价池 ({'MorningBiddingList 实时' if live else 'His 板块成分'})")
    boards, pool, board_bid, zt_data = collect_boards(spider, date_str, live=live)
    store.save_board_bid(date_str, board_bid)
    store.save_limit_pool(date_str, zt_data)
    # 竞价池(板块成分 + 竞价列表合并)落库: stage_pool 的竞价数据/换手排名全靠它
    store.save_bid_pool(date_str, [_pool_row_layout(i) for i in pool.values()], "merged")

    # 市况(纯本地 limit_pool/board_bid 计算, 零请求) + 市场环境(软化后恒 pass, 信息性)
    regime = market_regime(date_str)
    env_res = funnel.env_check(env["mood"], env["capacity"], env["bid_total"], env["bid_count"])
    print(f"[3/5] 市况/环境: 📡 {regime['regime']}({' · '.join(regime['detail'])}) · "
          f"{'可出手' if env_res['pass'] else '空仓'} ({'; '.join(env_res['reasons'])})")

    # 昨日涨停基准日(live 路径不落当天涨停池, "昨日"语义本就是前一日; 回放时历史已补齐)
    _conn = __import__("sqlite3").connect(store.db_path)
    _prev_day_row = _conn.execute(
        "SELECT MAX(date) FROM limit_pool WHERE date < ?", (date_str,)).fetchone()
    _prev_limit_day = _prev_day_row[0] if _prev_day_row else None
    limit_codes = {str(r[0]) for r in _conn.execute(
        "SELECT code FROM limit_pool WHERE date=?", (_prev_limit_day,)).fetchall()} if _prev_limit_day else set()
    _conn.close()

    print(f"[4/5] 情绪周期")
    cycle_res = None
    try:
        cycle_res = compute_cycle(date_str, persist=False)
        print(f"      周期: {cycle_res['stage']} (置信度 {cycle_res['confidence']}/9), "
              f"主线 {[m['board'] for m in cycle_res['mainlines'][:3]]}")
    except Exception as e:
        print(f"      ⚠️ 周期引擎不可用({e}), 出击选股无法生成")

    # V5 竞价首枪(内部喂养, 不推送): 容量方向供 stage_pool 发酵/高潮段(经 auction.json v5)
    v5_list = []
    if cycle_res:
        try:
            v5_list = screen_v5(date_str, pool, limit_codes, stock_bids=None,
                                cycle_res=cycle_res)
            store.save_v5_results(date_str, v5_list)
            n_v5 = sum(1 for v in v5_list if v.get("group_tag") == "v5")
            print(f"      V5 内部喂养: 候选 {n_v5} 只(不推送), v5_results 落库 {len(v5_list)} 条")
        except Exception as e:
            print(f"      ⚠️ V5 筛选失败(今日容量方向缺席, 不影响主流程): {e}")

    print(f"[5/5] 出击选股 + 昨日连板换手")
    picks, watch_mode, bidrank = [], False, []
    if cycle_res:
        # 出击选股(9:26 口径): stage_pool(当日周期+当日竞价) → Top5。
        # strike_pool 原样存档(复核/审计读"当时说了什么"; 09:31 --confirm 也读本表)。
        try:
            from src.analysis.stage_candidates import stage_pool
            _pool25 = stage_pool(cycle_res, max_n=20, bid_date=date_str)
            _picks = [{k: p.get(k) for k in ("code", "name", "height", "status", "reason", "tag", "bid_pct")}
                      for p in _pool25]
            picks, watch_mode = pick_strike_top(_picks)
            print(f"      出击名单 {len(_picks)} 条 → Top{len(picks)}"
                  + ("(全观察, 纪律优先)" if watch_mode else ""))
            if not dry_run:
                _conn = __import__("sqlite3").connect(store.db_path)
                try:
                    _conn.execute("CREATE TABLE IF NOT EXISTS strike_pool "
                                  "(date TEXT PRIMARY KEY, stage TEXT, picks TEXT, created_at TEXT)")
                    _conn.execute("INSERT OR REPLACE INTO strike_pool VALUES (?,?,?,?)",
                                  (date_str, cycle_res["stage"], json.dumps(_picks, ensure_ascii=False),
                                   datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M")))
                    _conn.commit()
                finally:
                    _conn.close()
                print(f"      → strike_pool 存档 {len(_picks)} 条({cycle_res['stage']}期)")
        except Exception as e:
            print(f"      ⚠️ 出击选股失败(不影响落库): {e}")
        # 昨日连板 · 今日竞价换手 Top5(口径同 build_lianban_bid/盘面页 bidTop):
        # 连板名单 = limit_pool 前一交易日 pid_type>=2; 换手 KPL 优先, 0值腾讯 0930 补算
        try:
            _conn = __import__("sqlite3").connect(store.db_path)
            try:
                _lb_rows = _conn.execute(
                    "SELECT code, name, pid_type, circ_mv FROM limit_pool "
                    "WHERE date=? AND pid_type>=2", (_prev_limit_day,)).fetchall() \
                    if _prev_limit_day else []
            finally:
                _conn.close()
            _np = os.environ.get("NO_PROXY", "")
            if _lb_rows and "gtimg.cn" not in _np:
                os.environ["NO_PROXY"] = _np + ("," if _np else "") + \
                    "gtimg.cn,.gtimg.cn,ifzq.gtimg.cn,web.ifzq.gtimg.cn"
            lianban_rows = []
            for code, name, pid, mv in _lb_rows:
                item = pool.get(str(code))
                t_name = (item.get("name") if item else None) or name or ""
                if "ST" in t_name.upper():
                    continue
                turn = (item.get("turnover_ratio") or 0) if item else 0
                bid_pct = item.get("bid_pct") if item else None
                f_mv = (item.get("circ_mv") if item else None) or mv
                if not turn and f_mv:
                    amt = _tencent_auction_amt(str(code))
                    if amt:
                        turn = amt / f_mv * 100  # 竞价成交额/流通市值(补算口径)
                lianban_rows.append({"code": str(code), "name": t_name,
                                     "height": pid or 0, "bid_pct": bid_pct,
                                     "turnover": turn or 0})
            bidrank = rank_lianban_bid(lianban_rows)
            print(f"      昨日({_prev_limit_day})连板 {len(lianban_rows)} 只 → 竞价换手 Top{len(bidrank)}")
        except Exception as e:
            print(f"      ⚠️ 昨日连板换手排名失败(不影响主流程): {e}")

    # 人气榜 am 快照(东财单源, 前100, 保留排名): 独立 hot_rank.db;dry-run 不写
    if not dry_run:
        print(f"      东财人气榜快照(am)")
        try:
            hot = collect_em_hot(top=100)
            hr = HotRankStore()
            snap = "am" if not hr.has_snapshot(date_str, "am") else None
            if snap:
                saved = hr.save_hot_rank(date_str, snap, hot)
                print(f"      → {len(hot)} 条已存({snap})" if saved else "      → 已存在,跳过")
            else:
                print("      → 今日已有快照,跳过")
        except Exception as e:
            print(f"      ⚠️ 人气榜采集失败(不影响主流程): {e}")

    out = {
        "date": date_str, "generated_at": crawl_time,
        "env": env_res, "boards": boards,
        "strike": picks, "strike_watch": watch_mode, "bidrank": bidrank,
        "v5": v5_list, "regime": regime,
        "cycle": ({
            "stage": cycle_res["stage"], "confidence": cycle_res["confidence"],
            "gate": {"退潮": "closed", "冰点": "closed", "分歧": "half",
                     "发酵": "open", "高潮": "open"}.get(cycle_res["stage"], "off"),
            "mainlines": [m["board"] for m in cycle_res["mainlines"]],
            "leaders": [{"code": l["code"], "name": l["name"],
                         "pid": l["pid"], "role": l["role"]} for l in cycle_res["leaders"]],
        } if cycle_res else None),
        "empty_reason": "" if env_res["pass"] else "; ".join(env_res["reasons"]),
        "stats": {"pool": len(pool), "boards": len(boards)},
    }
    # dry-run 不写生产快照(演练/回放不覆盖前端auction.json; 演练正文直接打印供人工核验)
    if not dry_run:
        AUCTION_OUT.parent.mkdir(parents=True, exist_ok=True)
        # 前端生产文件写入前同样校验数据日期(与 AuctionStore._validate_date 同规则):
        # 防止非交易日手跑把"今天"假快照覆盖到 auction.json(2026-08-23 审计实测发生过)
        AuctionStore._validate_date(date_str)
        AUCTION_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
        print(f"✅ auction.json 已写入 {AUCTION_OUT} "
              f"(出击 {len(picks)} + 连板换手 {len(bidrank)} + V5喂养 {len(v5_list)})")

    if cycle_res:
        text = build_strike_message(date_str, crawl_time, cycle_res, regime, env_res,
                                    picks, watch_mode, bidrank)
    else:
        text = (f"## 🎯 今日出击 {crawl_time}\n> {date_str}\n"
                "**⚠️ 周期引擎不可用, 出击选股未生成**(下一交易日自动重试)\n\n"
                "📈 [复盘页面](https://WXinYi.github.io/stockboard/#/auction)")
    if dry_run:
        print("──── dry-run 推送正文(未发送) ────")
        print(text)
        print("────────────────────────────────")
        return 0
    try:
        resp = DingTalk().send_markdown(f"🎯 今日出击 {date_str} {crawl_time}", text)
        print(f"📣 钉钉推送: {resp}")
    except Exception as e:
        # 推送失败不能阻断 workflow 的 commit 步骤(否则当天选股丢库), 只告警
        print(f"❌ 钉钉推送失败(不影响落库): {e}")
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
    run("竞价列表 MorningBiddingList", lambda: spider.bid_list(pid_type=0))
    run("涨停池 DailyLimitPerformance", lambda: spider.zt_pool(date_str))
    print("\n".join(checks))
    print(f"== 探测完成: {sum('✅' in c for c in checks)}/{len(checks)} 可用 ==")
    return 0 if all("✅" in c for c in checks) else 1


def main():
    ap = argparse.ArgumentParser(description="竞价抢筹扫描")
    ap.add_argument("--date", help="扫描日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--probe", action="store_true", help="接口探测模式(T1)")
    ap.add_argument("--confirm", action="store_true", help="出击选股开盘确认(09:31, 读 strike_pool)")
    ap.add_argument("--label", nargs="?", const="today", metavar="YYYY-MM-DD",
                    help="结果标签模式: 抓当日收盘写 candidate_results(默认今天; 可传历史日期回补)")
    ap.add_argument("--v5-report", action="store_true",
                    help="V5 回测报告: 按分组(候选/换手被拒对照/市值被拒对照·主攻/次攻/半仓·昨日强/低位)"
                         "统计 T+0 与 T+1 收益, 策略调整依据")
    ap.add_argument("--hot-rank", action="store_true",
                    help="东财人气榜快照(TOP100)落 hot_rank.db; snap 自动判定 am/pm(crawl.yml 午后调用)")
    ap.add_argument("--backfill-factors", action="store_true",
                    help="历史候选日K因子回填(ma60_above/ret20/macd_ok/kdj_ok; 委比无历史数据留 NULL)")
    ap.add_argument("--dry-run", action="store_true", help="演练: 不推钉钉不写生产快照, 正文打印供核验")
    args = ap.parse_args()

    date_str = args.date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    if args.probe:
        return probe(date_str)
    if args.backfill_factors:
        return backfill_factors()
    if args.label:
        d = date_str if args.label == "today" else args.label
        return label_results(d)
    if args.v5_report:
        return v5_report()
    if args.hot_rank:
        return hot_rank_job()
    if args.confirm:
        return e_confirm(date_str)
    return scan(date_str, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
