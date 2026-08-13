"""
通过平台 API (rtV2) 获取选手详情、持仓和调仓

替代 Playwright 方案，不再需要浏览器渲染。
一次 API 调用即可获取详情 + 持仓 + 最近调仓。
"""
import re
import asyncio
from typing import Dict, Any, Optional, List, Tuple
from datetime import date

import requests

from src.config import DATA_DIR
from src.utils.logger import setup_logger

logger = setup_logger()

# V2 API 配置
API_V2_URL = "https://emdcspzhapi.eastmoney.com/rtV2"
API_V2_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "app-iphone-client-iPhone15,2-FAC3049C-3BE9-4D4C-88E3-3B4FD518050A",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
}

MAX_RETRIES = 3
RETRY_DELAY = 1  # 秒


def _call_api(method: str, args: Dict[str, str]) -> Optional[Dict]:
    """调用 rtV2 API（同步）"""
    payload = {
        "appKey": "eastmoney",
        "method": method,
        "client": "ios",
        "clientVersion": "10.6",
        "clientType": "cfw",
        "args": args,
        "timestamp": "1784714414710",  # 固定时间戳，APP 不校验
    }

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                API_V2_URL, json=payload, headers=API_V2_HEADERS, timeout=15
            )
            data = resp.json()
            if data.get("code") == 0:
                return data.get("data", {})
            else:
                logger.warning(
                    f"API 返回错误: code={data.get('code')}, "
                    f"message={data.get('message')}, method={method}"
                )
                return None
        except requests.Timeout:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"API 超时 (尝试 {attempt+1}/{MAX_RETRIES})")
                continue
            logger.error(f"API 超时，method={method} args={args}")
            return None
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                logger.warning(f"API 调用失败 (尝试 {attempt+1}/{MAX_RETRIES}): {e}")
                asyncio.sleep(RETRY_DELAY) if False else None  # 同步版本
                continue
            logger.error(f"API 调用失败: {e}")
            return None

    return None


def _parse_player_detail(api_data: Dict, zh_id: str) -> Dict[str, Any]:
    """从 API 响应解析选手详情"""
    d = api_data.get("detail", {})
    if not d:
        return {"zh_id": zh_id}

    player = {
        "zh_id": zh_id,
        "name": d.get("zuheName", ""),
        "user_id": d.get("userid", ""),
        "followers": int(d.get("concernCnt", 0)),
        "total_return": float(d.get("rate", 0)),
        "daily_return": float(d.get("rateDay", 0)),
        "weekly_return": float(d.get("rate5Day", 0)),
        "monthly_return": float(d.get("rate20Day", 0)),
        "yearly_return": float(d.get("rate250Day", 0)),
        "net_value": float(d.get("JZ", 0)),
        "max_drawdown": float(d.get("maxDrawDown", 0)),
        "win_rate": float(d.get("dealRate", 0)),
        "days": int(d.get("yxts", 0)),
        "concept": d.get("comment", "")[:100] if d.get("comment") else "",
        "intro": d.get("comment", ""),
        # 附加字段
        "manager_name": d.get("uidNick", ""),
        "start_date": d.get("startDate", ""),
        "portf_rat": float(d.get("portfRat", 0)),
        "rate_max_stk": d.get("rateMaxStkName", ""),
        "rate_max_stk_code": d.get("rateMaxStkCode", ""),
    }
    return player


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


def _parse_positions(api_data: Dict, zh_id: str) -> List[Dict[str, Any]]:
    """从 API 响应解析持仓数据"""
    positions = []
    for p in api_data.get("position", []):
        try:
            position = {
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
            }
            positions.append(position)
        except (ValueError, TypeError) as e:
            logger.warning(f"解析持仓项失败: {e}, data={p}")
            continue
    return positions


def _parse_trades(api_data: Dict, zh_id: str) -> List[Dict[str, Any]]:
    """从 API 响应解析调仓记录"""
    trades = []
    for t in api_data.get("tradeSummary", []):
        try:
            # 买入方向
            buy_trades = int(t.get("lshj_mr", 0) or 0)
            buy_position = t.get("cwhj_mr", "")
            _buy_price = t.get("cjjg_mr", "0")
            buy_price = float(_buy_price) if _buy_price and _buy_price != "--" else 0.0

            # 卖出方向
            sell_trades = int(t.get("lshj_mc", 0) or 0)
            sell_position = t.get("cwhj_mc", "")
            _sell_price = t.get("cjjg_mc", "0")
            sell_price = float(_sell_price) if _sell_price and _sell_price != "--" else 0.0

            trade_date_raw = t.get("tzrq", "")
            # 格式化日期: 20260717 → 2026-07-17
            if len(trade_date_raw) == 8:
                trade_date = f"{trade_date_raw[:4]}-{trade_date_raw[4:6]}-{trade_date_raw[6:8]}"
            else:
                trade_date = trade_date_raw

            # 买入记录
            if buy_trades > 0:
                trades.append({
                    "zh_id": zh_id,
                    "stock_name": t.get("stkName", ""),
                    "stock_code": t.get("stkMktCode", "").replace("SZ", "").replace("SH", ""),
                    "trade_date": trade_date,
                    "direction": "买入",
                    "trades_count": buy_trades,
                    "position_ratio": buy_position,
                    "price": buy_price,
                    "position_change": 0,
                })
            # 卖出记录
            if sell_trades > 0:
                trades.append({
                    "zh_id": zh_id,
                    "stock_name": t.get("stkName", ""),
                    "stock_code": t.get("stkMktCode", "").replace("SZ", "").replace("SH", ""),
                    "trade_date": trade_date,
                    "direction": "卖出",
                    "trades_count": sell_trades,
                    "position_ratio": sell_position,
                    "price": sell_price,
                    "position_change": 0,
                })
        except (ValueError, TypeError) as e:
            logger.warning(f"解析调仓记录失败: {e}, data={t}")
            continue
    return trades


def crawl_player_all_data(zh_id: str) -> Tuple[Optional[Dict[str, Any]], List[Dict], List[Dict]]:
    """
    爬取单个选手的全部数据（同步版本）

    Args:
        zh_id: 选手组合ID

    Returns:
        (detail_dict, positions_list, trades_list)
    """
    api_data = _call_api("combination_detail_97", {"zh": zh_id})
    if not api_data:
        return (None, [], [])

    detail = _parse_player_detail(api_data, zh_id)
    positions = _parse_positions(api_data, zh_id)
    trades = _parse_trades(api_data, zh_id)

    return (detail, positions, trades)


async def crawl_player_all_data_async(
    zh_id: str
) -> Tuple[Optional[Dict[str, Any]], List[Dict], List[Dict]]:
    """
    爬取单个选手的全部数据（异步版本，在线程池中执行同步请求）

    Args:
        zh_id: 选手组合ID

    Returns:
        (detail_dict, positions_list, trades_list)
    """
    return await asyncio.to_thread(crawl_player_all_data, zh_id)


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
