# -*- coding: utf-8 -*-
"""调仓增量键(2026-11-11): 稳定键 + 旧键迁移兼容。

背景: 旧 _k = md5(交易日|代码|方向|价格|仓位档|笔数)[:12], 其中 price 取
"最新一笔"、trades_count 随分页窗口累加、仓位档取首见 —— 任一变化都会翻键,
导致 notify_daily 把同一笔判成"新增"(重复推送 + 误标 🆕)。

stable_trade_key 只依赖交易身份(zh_id|交易日|代码|方向), 不依赖会变的聚合值。
去重键 export_json 已是 (zh_id, code, trade_date, direction), 同日同向多笔已被
合并成一行(trades_count>1), 因此该四元组唯一, 无需 seq。

迁移: 历史 last_notify_state.json 里存的是旧键。直接换键会让上线第一班所有
历史操作的新键都不在 seen 中 → 全量重推。故导出时同时输出 _k_legacy,
notify_daily 判定"新键或旧键任一命中即已见", 写入只写新键, 平滑过渡。
"""
import hashlib
from typing import Iterable, Optional, Set


def _safe_float(v, default=0.0):
    """与 export_json.safe_float 逐字一致(None→default, 否则 float(v))。"""
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0):
    """与 export_json.safe_int 逐字一致(None→default, 否则 int(v))。"""
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def stable_trade_key(trade: dict, zh_id: str = "") -> str:
    """稳定增量键: 只由交易身份决定, 重采/换 id/价格修正都不变。16 位 hex。"""
    raw = "|".join([
        str(zh_id or trade.get("zh_id") or ""),
        str(trade.get("trade_date") or trade.get("td") or ""),
        str(trade.get("stock_code") or trade.get("sc") or ""),
        str(trade.get("direction") or trade.get("dr") or ""),
    ])
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def legacy_trade_key(trade: dict) -> str:
    """旧键, 仅用于迁移期命中历史 seen。必须与 export_json 旧算法完全一致。"""
    raw = "|".join([
        str(trade.get("trade_date") or ""),
        str(trade.get("stock_code") or ""),
        str(trade.get("direction") or ""),
        str(_safe_float(trade.get("price"))),
        str(trade.get("position_ratio") or ""),
        str(_safe_int(trade.get("trades_count"), 1)),
    ])
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def trade_keys(trade: dict, zh_id: str = "") -> tuple:
    """导出用: 返回 (新键, 旧键)。"""
    return stable_trade_key(trade, zh_id), legacy_trade_key(trade)


def is_trade_seen(trade: dict, seen_set: Set[str]) -> bool:
    """迁移期判定: 新键或旧键任一命中历史 seen 即视为已推过。"""
    for k in (trade.get("_k"), trade.get("_k_legacy")):
        if k and str(k) in seen_set:
            return True
    return False


def seen_write_key(trade: dict) -> Optional[str]:
    """写入 seen 只用新键(逐步淘汰旧键); 缺 _k 时退回 _id 保持旧行为。"""
    k = trade.get("_k") or trade.get("_id")
    return str(k) if k is not None else None
