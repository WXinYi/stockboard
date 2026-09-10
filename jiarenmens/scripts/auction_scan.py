#!/usr/bin/env python3
"""
竞价扫描入口: 采集 → 周期 → 出击选股 → 钉钉推送

09:29 推送 = 出击选股 Top5(盘面页出击Tab 的 9:26 口径存档, stage_candidates.stage_pool)
         + 昨日连板 · 今日竞价换手 Top5。存量评分漏斗(B1-S9 候选)与 V5 首枪已整链
         删除(2026-09-06): 漏斗评分/涨停基因/全池竞价分时采集/v5_results 表及回测
         打标一并移除, 省去每交易日数百请求。

用法(在 jiarenmens/ 目录下执行):
  python scripts/auction_scan.py --probe            # T1 探测: 验证竞价接口可用性
  python scripts/auction_scan.py --dry-run          # 完整扫描, 不推钉钉不写生产快照(本地验证用)
  python scripts/auction_scan.py --date 2026-08-07  # 回放指定日期(历史数据)

时序: 09:25 cron 触发 → 扫描 → 09:29 钉钉出击推送 → 09:2x build+部署上线(2026-09-10 起直接发版,
开盘确认机制已整链移除——不再等 09:31, auction.json 提交后立即部署)。

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

from src.analysis.auction_env import board_select, env_check  # noqa: E402
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
# 数据归一化 → 统一 dict(竞价池行布局, 17 位与 MorningBiddingList 对齐)
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
    boards = board_select(board_bid, ranking, max_boards=BOARD_LIMIT)

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
    boards = board_select(board_bid, ranking, max_boards=BOARD_LIMIT)

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
# 钉钉消息
# =============================================================================


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


PULSE_STAGE = {
    # (昨收阶段组, 竞价实测) → (当下周期阶段预判, 一句话依据)
    # stage=None 表示"延续昨收阶段"→ 由调用方回填昨收 stage
    ("低谷", "情绪修复"): ("启动", "退潮反转, 关注新周期方向"),
    ("低谷", "退潮延续"): ("退潮", "低谷延续, 空仓纪律有效"),
    ("低谷", "分化(观望)"): ("退潮/冰点", "震荡, 纪律优先"),
    ("上行", "情绪修复"): (None, "延续昨收阶段"),
    ("上行", "退潮延续"): ("分歧", "见顶预警, 上行期竞价转弱"),
    ("上行", "分化(观望)"): ("分歧", "高位分化, 去弱留强"),
    ("高位", "情绪修复"): ("发酵", "分歧转一致, 龙头接力窗口"),
    ("高位", "退潮延续"): ("分歧", "分歧加剧, 避中位防核按钮"),
    ("高位", "分化(观望)"): ("分歧", "分歧延续, 只看龙头"),
}
STAGE_GROUP = {"退潮": "低谷", "冰点": "低谷", "启动": "上行",
               "发酵": "上行", "高潮": "上行", "分歧": "高位"}


def auction_pulse(prev_limit: List[Dict], pool: Dict[str, Dict],
                  leaders: Optional[List[Dict]] = None, top_leaders: int = 2,
                  stage: Optional[str] = None) -> Optional[Dict]:
    """竞价情绪主判据(纯函数, 单测覆盖): 9:26 推送的第一判据。
    物理约束: 竞价时点当日涨停池不存在 → 情绪周期引擎无法运行; 竞价数据是唯一"今日"信号。
    输出: 三档竞价实测(情绪修复/退潮延续/分化) + 当下周期阶段预判(竞价实测×昨收阶段, 见 PULSE_STAGE)。
    规则(确定性): 均幅≥+2% 且 红盘率≥70% → 情绪修复; 均幅<0 或 大面率(竞价≤-3%)≥30% → 退潮延续; 其余 → 分化(观望)。
    阈值待竞价历史样本校准(bid_pool×limit_pool 可回测)。
    prev_limit: 昨日涨停池行 [{code,name}]; pool: 今日竞价池(code→dict, bid_pct);
    leaders: 周期龙头谱系; stage: 昨收周期阶段(用于周期指向)。"""
    matched = []
    for r in prev_limit:
        item = pool.get(str(r["code"]))
        if item and item.get("bid_pct") is not None:
            matched.append(float(item["bid_pct"]))
    if len(matched) < 5:
        return None  # 样本不足, 不预判
    n = len(matched)
    red_rate = sum(1 for p in matched if p > 0) / n
    avg_bid = sum(matched) / n
    face_n = sum(1 for p in matched if p <= -3)
    if avg_bid >= 2 and red_rate >= 0.7:
        verdict = "情绪修复"
    elif avg_bid < 0 or face_n / n >= 0.3:
        verdict = "退潮延续"
    else:
        verdict = "分化(观望)"
    lead_txt = ""
    if leaders:
        q = []
        for l in leaders:
            item = pool.get(str(l["code"]))
            if item and item.get("bid_pct") is not None:
                q.append(f"{l['name']}{item['bid_pct']:+.1f}%")
            if len(q) >= top_leaders:
                break
        if q:
            lead_txt = " · 龙头: " + " ".join(q)
    stage_now, note = "", ""
    if stage:
        _s, note = PULSE_STAGE.get((STAGE_GROUP.get(stage, ""), verdict), ("", ""))
        stage_now = _s or stage  # None=延续昨收阶段
    return {"verdict": verdict, "red_rate": red_rate, "avg_bid": avg_bid,
            "face_n": face_n, "n": n, "lead_txt": lead_txt,
            "stage_now": stage_now, "note": note}


def build_strike_message(date_str: str, crawl_time: str, cycle_res: Optional[Dict],
                         regime: Optional[Dict], env: Dict, picks: List[Dict],
                         watch_mode: bool, bidrank: List[Dict],
                         pulse: Optional[Dict] = None) -> str:
    """09:29 推送正文: 周期(昨收口径)+竞价预判+环境 → 出击选股 Top5 → 昨日连板·竞价换手 Top5"""
    e = env["data"]
    lines = [f"## 🎯 今日出击 {crawl_time}", f"> {date_str}"]
    # 第一判据: 竞价实测情绪(9:26 唯一的"今日"信号) → 当下周期阶段预判
    if pulse:
        lines.append(f"**⚡ 竞价情绪(9:26 实测): {pulse['verdict']}**(昨日涨停股竞价 红盘率{pulse['red_rate']:.0%}"
                     f" · 均幅{pulse['avg_bid']:+.1f}% · 竞价大面{pulse['face_n']}/{pulse['n']})"
                     + pulse.get("lead_txt", ""))
        if pulse.get("stage_now"):
            lines.append(f"**📌 当下周期(竞价预判): {pulse['stage_now']}** — {pulse['note']}")
    # 参考行: 昨收口径周期(竞价阶段当日池不存在, 引擎只能吃到昨收数据); 出击名单按此闸门生成(保守)
    if cycle_res:
        cap, banner = STAGE_GATE_CN.get(cycle_res["stage"], (100, ""))
        cap_txt = "禁买" if cap == 0 else f"{cap}成"
        reg_txt = f" · 📡 市况{regime['regime']}" if regime else ""
        lines.append(f"周期参考(昨收): {cycle_res['stage']}({cycle_res['confidence']}/9)"
                     f" · 出击名单按此闸门, 仓位上限 {cap_txt}{reg_txt}")
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
    存量评分漏斗候选与 V5 首枪已整链删除(2026-09-06): 漏斗评分/S7 融合/涨停基因/全池
    竞价分时采集、v5_results 表及回测打标一并移除 —— 每交易日省去数百请求。"""
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
    env_res = env_check(env["mood"], env["capacity"], env["bid_total"], env["bid_count"])
    print(f"[3/5] 市况/环境: 📡 {regime['regime']}({' · '.join(regime['detail'])}) · "
          f"{'可出手' if env_res['pass'] else '空仓'} ({'; '.join(env_res['reasons'])})")

    # 昨日涨停基准日(live 路径不落当天涨停池, "昨日"语义本就是前一日; 回放时历史已补齐)
    _conn = __import__("sqlite3").connect(store.db_path)
    _prev_day_row = _conn.execute(
        "SELECT MAX(date) FROM limit_pool WHERE date < ?", (date_str,)).fetchone()
    _prev_limit_day = _prev_day_row[0] if _prev_day_row else None
    _conn.close()

    print(f"[4/5] 情绪周期")
    cycle_res = None
    try:
        cycle_res = compute_cycle(date_str, persist=False)
        print(f"      周期: {cycle_res['stage']} (置信度 {cycle_res['confidence']}/9), "
              f"主线 {[m['board'] for m in cycle_res['mainlines'][:3]]}")
    except Exception as e:
        if date_str == today_str and live:
            # 9:26 实盘口径(stage_pool.bid_date 设计注释: "盘前存档场景传当日, 9:26 竞价+昨日池"):
            # 竞价班 live 路径不落当日涨停池 → compute_cycle(今天)必抛"not in list" →
            # 回退池内最近一天(昨日收盘周期判定) + 今日竞价(下方 stage_pool bid_date=今日)
            try:
                cycle_res = compute_cycle(persist=False)
                print(f"      周期: {cycle_res['stage']} (置信度 {cycle_res['confidence']}/9), "
                      f"主线 {[m['board'] for m in cycle_res['mainlines'][:3]]}")
                print(f"      (9:26 口径: 当日池未落 → 用池内最近日 {cycle_res['date']} 的周期判定 + 今日竞价)")
            except Exception as e2:
                print(f"      ⚠️ 周期引擎不可用({e2}), 出击选股无法生成")
        else:
            print(f"      ⚠️ 周期引擎不可用({e}), 出击选股无法生成")

    print(f"[5/5] 出击选股 + 昨日连板换手")
    picks, watch_mode, bidrank = [], False, []
    if cycle_res:
        # 出击选股(9:26 口径): stage_pool(当日周期+当日竞价) → Top5。strike_pool 原样存档供复核。
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
        "regime": regime,
        "cycle": ({
            "stage": cycle_res["stage"], "confidence": cycle_res["confidence"],
            "gate": {"退潮": "closed", "冰点": "closed", "分歧": "half",
                     "发酵": "open", "高潮": "open"}.get(cycle_res["stage"], "off"),
            "mainlines": [m["board"] for m in cycle_res["mainlines"]],
            "leaders": [{"code": l["code"], "name": l["name"],
                         "pid": l["pid"], "role": l["role"]} for l in cycle_res["leaders"]],
        } if cycle_res else None),
        "empty_reason": "" if env_res["pass"] else "; ".join(env_res["reasons"]),
        "stats": {"pool": len(pool), "boards": len(boards), "genes": 0},
        # ── 过渡兼容键(老 Pages 构建的 AuctionTab 读 candidates.length/watch 会崩,
        #    新 UI 要等 crawl 班 npm run build 部署; 新 UI 上线后本组可删) ──
        "candidates": [], "watch": [],
    }
    # dry-run 不写生产快照(演练/回放不覆盖前端auction.json; 演练正文直接打印供人工核验)
    if not dry_run:
        AUCTION_OUT.parent.mkdir(parents=True, exist_ok=True)
        # 前端生产文件写入前同样校验数据日期(与 AuctionStore._validate_date 同规则):
        # 防止非交易日手跑把"今天"假快照覆盖到 auction.json(2026-08-23 审计实测发生过)
        AuctionStore._validate_date(date_str)
        AUCTION_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
        print(f"✅ auction.json 已写入 {AUCTION_OUT} "
              f"(出击 {len(picks)} + 连板换手 {len(bidrank)})")

    # 竞价预判(方案A, 纯展示): 昨日涨停池 × 今日竞价 → 修复/延续/分化
    pulse = None
    try:
        _conn = __import__("sqlite3").connect(store.db_path)
        try:
            _prev_limit_rows = [{"code": r[0], "name": r[1]} for r in _conn.execute(
                "SELECT code, name FROM limit_pool WHERE date=?", (_prev_limit_day,))] \
                if _prev_limit_day else []
        finally:
            _conn.close()
        pulse = auction_pulse(_prev_limit_rows, pool, leaders=(cycle_res or {}).get("leaders"),
                              stage=(cycle_res or {}).get("stage"))
        if pulse:
            print(f"      ⚡ 竞价预判: {pulse['verdict']} (红盘率{pulse['red_rate']:.0%} "
                  f"均幅{pulse['avg_bid']:+.1f}% 大面{pulse['face_n']}/{pulse['n']})")
    except Exception as e:
        print(f"      ⚠️ 竞价预判计算失败(不影响主流程): {e}")

    if cycle_res:
        text = build_strike_message(date_str, crawl_time, cycle_res, regime, env_res,
                                    picks, watch_mode, bidrank, pulse=pulse)
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
    ap.add_argument("--hot-rank", action="store_true",
                    help="东财人气榜快照(TOP100)落 hot_rank.db; snap 自动判定 am/pm(crawl.yml 午后调用)")
    ap.add_argument("--dry-run", action="store_true", help="演练: 不推钉钉不写生产快照, 正文打印供核验")
    args = ap.parse_args()

    date_str = args.date or datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    if args.probe:
        return probe(date_str)
    if args.hot_rank:
        return hot_rank_job()
    return scan(date_str, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
