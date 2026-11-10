"""
通过平台 API 获取选手详情、持仓和调仓

2026-11-10 协议迁移: EM 关闭旧 rtV2(emdcspzhapi.eastmoney.com, "APP版本较低"门禁)。
新协议 = spzhapi.dfcfs.cn/rtV2 + 登录态 token(args 内 ctToken/utToken, 无需 sign)
+ 新字段三件套(randomCode/deviceId/新版 clientVersion)。
token 来源: env EM_CT_TOKEN/EM_UT_TOKEN > data/em_tokens.json(gitignored, 抓包获取)。
token 失效表现: code 9081/200 → 采集诚实失败, 重新抓包更新 token 即恢复。
"""
import re
import json
import time
import uuid
import random
import string
import os
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import date
from pathlib import Path

import requests

from src.config import DATA_DIR
from src.utils.logger import setup_logger

logger = setup_logger()

# ── 新协议配置(2026-11-10, spzhapi 主机 + 登录态 token) ──
API_V2_URL = "https://spzhapi.dfcfs.cn/rtV2"          # 新主机(旧主机 emdcspzhapi 已废弃)
TOKEN_FILE = Path(DATA_DIR) / "em_tokens.json"
CLIENT_VERSION = "11.5.5_3.0.0.261105130728749"        # 新版 app 版本串(门禁校验点)
API_V2_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "%E4%B8%9C%E6%96%B9%E8%B4%A2%E5%AF%8C/20261021100.1055 CFNetwork/3896.200.61 Darwin",
}

# 关注/已知选手 zh_id → userId(公开标识, rtV1 榜单/搜索接口均可见)。
# relocate/持仓/收益接口必须带 userId; 缺失时回退采集库 players.user_id / 搜索接口反查。
NEW_USER_IDS = {
    "900456476": "2992386206580634",   # 狼之行一
    "900450475": "1407047413230182",   # 敢作敢当的武研琳(11-03 起隐藏)
    "900351276": "3208446625163740",   # 新生代柚子04
    "900240956": "9887395831331906",   # 股得猫咛
    "900438148": "4208037210932556",   # 我嘚财富(08-30 起隐藏)
    "900376763": "4784346310692888",   # 东之福气娟子
    "900439290": "6487375946169702",   # 我要再来一次(10-15 起隐藏)
    "900461598": "3019386220935090",   # 大道可寻
    "900293400": "6838395742604432",   # 月儿兰
    "900458047": "3561335584325600",   # 点火的佩斯
    "900455448": "4992097323709932",   # 捏盘冲升
}

MAX_RETRIES = 3
RETRY_DELAY = 1  # 秒


def _load_tokens() -> Dict[str, str]:
    """登录态 token: env EM_CT_TOKEN/EM_UT_TOKEN 优先, 其次 data/em_tokens.json。
    返回 {ctToken, utToken, deviceId}; 缺失/不全 → {}(调用方诚实失败)。"""
    ct = os.environ.get("EM_CT_TOKEN", "")
    ut = os.environ.get("EM_UT_TOKEN", "")
    dev = os.environ.get("EM_DEVICE_ID", "")
    if ct and ut:
        return {"ctToken": ct, "utToken": ut, "deviceId": dev or str(uuid.uuid4())}
    try:
        d = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if d.get("ctToken") and d.get("utToken"):
            d.setdefault("deviceId", str(uuid.uuid4()))
            return d
    except Exception:
        pass
    return {}


def _call_api_new(method: str, args: Dict[str, Any], tokens: Dict[str, str]) -> Optional[Dict]:
    """新协议调用: spzhapi/rtV2 + token(args 内) + 新字段三件套。"""
    payload = {
        "args": args,
        "method": method,
        "client": "ios",
        "randomCode": "".join(random.choices(string.ascii_letters + string.digits, k=32)),
        "timestamp": int(time.time() * 1000),
        "deviceId": tokens.get("deviceId", ""),
        "clientType": "cfw",
        "clientVersion": CLIENT_VERSION,
    }
    headers = dict(API_V2_HEADERS)
    headers["Accept-Language"] = "zh-CN,zh-Hans;q=0.9"
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(API_V2_URL, json=payload, headers=headers, timeout=20)
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {})
            msg = str(data.get("message", ""))
            if "版本较低" in msg:
                # 门禁类: 重试无意义, 直接失败并给出可行动提示
                logger.error(f"EM 门禁拒绝({method}): {msg} — token 可能过期, 请重新抓包更新"
                             f" EM_CT_TOKEN/EM_UT_TOKEN 或 data/em_tokens.json")
                return None
            logger.warning(f"API 返回错误: code={data.get('code')}, {msg}, method={method}")
            return None
        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"API 超时 (尝试 {attempt+1}/{MAX_RETRIES})")
                continue
            logger.error(f"API 超时，method={method}")
            return None
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"API 调用失败 (尝试 {attempt+1}/{MAX_RETRIES}): {e}")
                time.sleep(RETRY_DELAY)
                continue
            logger.error(f"API 调用失败: {e}")
            return None
    return None


def _call_api(method: str, args: Dict[str, str]) -> Optional[Dict]:
    """兼容入口: 旧签名保留(export_json/watchdog 调 relocate 用), 内部走新协议。
    relocate/持仓/收益等需要登录态的方法由此通道提供。"""
    tokens = _load_tokens()
    if not tokens:
        logger.error("EM token 缺失(EM_CT_TOKEN/EM_UT_TOKEN 或 em_tokens.json) — 无法调用 EM 接口")
        return None
    return _call_api_new(method, dict(args), tokens)


def _safe_int(value, default=0) -> int:
    """安全转换整数, 处理 '44'/'--'/None 等特殊值"""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _opt_rate(v) -> Optional[float]:
    """源可能不提供的比值(如胜率): 缺/非数/0 → None(存 NULL), 不用 0 冒充真值。

    依据(2026-09-15 实测): 东财 rtV2 对全部选手返回 dealRate=0, 库内 25556 行无一非零,
    10 倍收益选手亦然; 旧 H5 页已失效无从补 → 该字段判定为"源不可得"。
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _safe_float(value, default=0.0) -> float:
    """安全转换浮点数，处理 '+∞'、'--' 等特殊值"""
    if value is None:
        return default
    s = str(value).strip()
    if not s or s in ("--", "+∞", "-∞", "∞", "NaN", "nan", "inf", "-inf"):
        return default
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _resolve_user_id(zh_id: str) -> Optional[str]:
    """userId 解析链: 代码映射 → 采集库 players.user_id → None。
    (rtV1 榜单与搜索接口也可见 userId, 但 watchdog/CI 无库, 以代码映射为主)"""
    if zh_id in NEW_USER_IDS:
        return NEW_USER_IDS[zh_id]
    try:
        import sqlite3
        db = Path(DATA_DIR) / "crawl_data.db"
        if db.exists():
            row = sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
                "SELECT user_id FROM players WHERE zh_id=?", (zh_id,)).fetchone()
            if row and row[0]:
                return row[0]
    except Exception as e:
        logger.warning(f"userId 库内解析失败({e})")
    return None


def _fetch_trades_pages(tokens: Dict[str, str], uid: str, zh_id: str,
                        max_pages: int = 3) -> List[Dict[str, Any]]:
    """调仓流水分页(新→旧), 聚合为旧 tradeSummary 形状的行:
    每 (date, stock, 方向) 一行, trades_count=当日该方向笔数, 价格/仓位档取最新一笔。"""
    agg = {}
    pages = 0
    for page in range(1, max_pages + 1):
        d = _call_api_new("CombinationRelocatePositionHandler", {
            "ctToken": tokens["ctToken"], "utToken": tokens["utToken"],
            "userId": uid, "combinationId": int(zh_id),
            "pageNum": page, "pageSize": 20, "beginDate": "", "endDate": "",
        }, tokens)
        rows = (d or {}).get("pages") or []
        pages += 1
        for r in rows:
            code = str(r.get("stkCode", ""))
            dr = "买入" if r.get("bsMark") == "B" else "卖出"
            key = (str(r.get("bizDate", "")), code, dr)
            a = agg.setdefault(key, {
                "biz_date": key[0], "stock_code": code,
                "stock_name": r.get("stkName", ""),
                "direction": dr, "trades_count": 0,
                "position_ratio": "", "price": 0.0,
            })
            a["trades_count"] += 1
            if not a["position_ratio"]:
                a["position_ratio"] = r.get("positionRatio") or ""
            px = r.get("relocatePrice")
            if px and not a["price"]:
                a["price"] = _safe_float(px)   # 首见=最新(流水新→旧)
        if len(rows) < 20:
            break
        time.sleep(0.2)
    logger.debug(f"调仓分页: {pages} 页, 聚合 {len(agg)} 行")
    out = []
    for (bd, code, dr), a in sorted(agg.items(), key=lambda x: x[0][0], reverse=True):
        out.append({
            "zh_id": zh_id,
            "stock_name": a["stock_name"],
            "stock_code": code,
            "trade_date": f"{bd[:4]}-{bd[4:6]}-{bd[6:8]}" if len(bd) == 8 else bd,
            "direction": dr,
            "trades_count": a["trades_count"],
            "position_ratio": a["position_ratio"],
            "price": a["price"],
            "position_change": 0,
        })
    return out


def crawl_player_all_data(zh_id: str, user_id: Optional[str] = None,
                          full: bool = False) -> Tuple[Optional[Dict[str, Any]], List[Dict], List[Dict]]:
    """
    爬取单个选手的全部数据（同步版本, 2026-11-10 新协议）

    Args:
        zh_id: 选手组合ID
        user_id: 选手 userId(缺省走解析链: NEW_USER_IDS → 采集库)
        full: 全量模式(收盘专班)——含 dimensions(回撤)+调仓2页;
              瘦身模式(默认, 盘中班)省略 dimensions、调仓1页 → 每选手 4 调用 vs 6,
              新协议每选手接口数是旧版 4-6 倍, 不瘦身班次会被限速拖到被下一班取消

    Returns:
        (detail_dict, positions_list, trades_list) — 形状与旧协议一致
    """
    tokens = _load_tokens()
    if not tokens:
        logger.error("EM token 缺失 — 请配置 EM_CT_TOKEN/EM_UT_TOKEN 或 em_tokens.json")
        return (None, [], [])
    uid = user_id or _resolve_user_id(zh_id)
    if not uid:
        logger.error(f"{zh_id}: 无法解析 userId, 跳过(rtV1 榜单或搜索接口可反查)")
        return (None, [], [])
    zh_int = int(zh_id)
    base = {"ctToken": tokens["ctToken"], "utToken": tokens["utToken"],
            "userId": uid, "combinationId": zh_int}

    info = _call_api_new("CombinationInfoHandler", dict(base), tokens) or {}
    yld = _call_api_new("combination_yield_detail_handler", dict(base), tokens) or {}
    pos_raw = _call_api_new("CombinationHoldPositionPermitHandler", dict(base), tokens)
    dims = (_call_api_new("combination_dimensions", {"zh": zh_int, "unit": "20d"}, tokens) or {}) if full else {}
    if not info and not yld and not pos_raw:
        logger.error(f"{zh_id}: 全部接口无数据(组合可能已删除)")
        return (None, [], [])

    pct = lambda v: _safe_float(v) * 100 if v not in (None, "") else 0.0   # 0.4570 → 45.70
    positions = []
    if isinstance(pos_raw, list):
        for blk in pos_raw:
            for p in blk.get("data") or []:
                positions.append({
                    "zh_id": zh_id,
                    "stock_name": p.get("__name", ""),
                    "stock_code": p.get("__code", ""),
                    "stk_mkt_code": p.get("stkMktCode", ""),
                    "cost_price": _safe_float(p.get("cbj")),
                    "current_price": _safe_float(p.get("__zxjg")),
                    "profit_ratio": _safe_float(p.get("webYkRate")),
                    "position_ratio": _safe_float(p.get("holdPos")),
                    "position_rate_detail": p.get("positionRateDetail", ""),
                    "update_time": "",
                })
    portf_rat = sum(x["position_rate_detail"] and _safe_float(x["position_rate_detail"]) or 0
                    for x in positions) if positions else _safe_float(info.get("portfRat"))

    detail = {
        "zh_id": zh_id,
        "name": info.get("zuheName", ""),
        "user_id": uid,
        "followers": _safe_int(info.get("concernCnt")),
        "total_return": pct(yld.get("totalYieldRate")),
        "daily_return": pct(yld.get("dayYieldRate")),
        "weekly_return": 0.0,   # 新协议无周收益字段(诚实置 0; 原口径 5 日收益待发现数据源)
        "monthly_return": pct(yld.get("monthYieldRate")),
        "yearly_return": pct(yld.get("yearYieldRate")),
        "net_value": _safe_float(yld.get("netWorth")),
        "max_drawdown": _safe_float(dims.get("maxDrawdown")),
        "win_rate": _opt_rate(dims.get("dayWinRate")),   # 口径变化: 原成交胜率已不可得, 此为当日胜率
        "days": _safe_int(info.get("yxts")),
        "concept": (info.get("comment") or "")[:100],
        "intro": info.get("comment") or "",
        "manager_name": info.get("uidNick", ""),
        "start_date": info.get("startDate", ""),
        "portf_rat": round(portf_rat, 2),
        "rate_max_stk": "",
        "rate_max_stk_code": "",
    }
    trades = _fetch_trades_pages(tokens, uid, zh_id, max_pages=2 if full else 1)
    return (detail, positions, trades)


async def crawl_player_all_data_async(
    zh_id: str, full: bool = False
) -> Tuple[Optional[Dict[str, Any]], List[Dict], List[Dict]]:
    """
    爬取单个选手的全部数据（异步版本，在线程池中执行同步请求）

    Args:
        zh_id: 选手组合ID
        full: 全量模式(收盘专班)

    Returns:
        (detail_dict, positions_list, trades_list)
    """
    return await asyncio.to_thread(crawl_player_all_data, zh_id, None, full)


# =============================================================================
# 测试
# =============================================================================
if __name__ == "__main__":
    # 测试同步版本
    print("=== 测试 API 爬虫 ===")
    for test_id in ["900240956", "900056513"]:
        detail, positions, trades = crawl_player_all_data(test_id)
        if detail:
            print(f"\n选手: {detail['name']} ({test_id})")
            print(f"  总收益: {detail['total_return']}%")
            print(f"  日收益: {detail['daily_return']}%")
            print(f"  净值: {detail['net_value']}, 回撤: {detail['max_drawdown']}%")
            print(f"  关注: {detail['followers']}人, 运行: {detail['days']}天")
            print(f"  管理人: {detail['manager_name']}")
            print(f"  持仓 {len(positions)} 只:")
            for p in positions:
                print(f"    {p['stock_name']}({p['stock_code']}) "
                      f"成本{p['cost_price']} 现价{p['current_price']} "
                      f"盈亏{p['profit_ratio']}% 仓位{p['position_ratio']}%")
            print(f"  最近调仓 {len(trades)} 笔:")
            for t in trades:
                print(f"    {t['trade_date']} {t['direction']} "
                      f"{t['stock_name']}({t['stock_code']}) "
                      f"{t['trades_count']}笔 仓位{t['position_ratio']} 价格{t['price']}")
        else:
            print(f"\n✗ {test_id}: 获取失败")
