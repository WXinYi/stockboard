# -*- coding: utf-8 -*-
"""持仓推算: 调仓流水 → 持仓变化(2026-11-10 分层采集设计)。

⚠️ 2026-11-10 起盘中持仓改为「复制最近 EOD 校准快照」(0 次 API、口径更诚实),
本模块暂停接线, 保留纯函数与单测供未来需要时复用 —— main.py 已不再 import。

盘中班只拉调仓(1 调用/人), 全量持仓降频到 新选手初始化 + 每小时班 + EOD 校准;
两次校准之间, 用当日调仓流水推算持仓(哪些票在仓/粗略仓位)。

语义依据(2026-11-10 抓包+实测修正):
- 卖出行档位语义不明(卖前/卖后不可辨) → 保守规则: 档位≤2成视为清仓, 其余不动(等校准)
- ⚠️ 买入行档位实测同样不可靠: 样本 900469743 11-10 买入通力科技档位"1成以下",
  盘后真实持仓 99% — positionRatio 更像"本笔动作用量"而非"买入后持仓档"。
  故推算值只作盘中参考(现价/盈亏诚实置 None), 一律以 EOD full 档校准为准。

纯函数, 不做 IO; 单测见 test_position_delta.py。
"""
from typing import Dict, List, Optional

# 文字档位 → 数值仓位%("1成以下"买方≈5; 卖方见模块注释的保守规则)
RATIO_MAP: Dict[str, float] = {
    "1成以下": 5, "1成": 10, "2成": 20, "3成": 30, "4成": 40, "5成": 50,
    "6成": 60, "7成": 70, "8成": 80, "9成": 90, "9成以上": 95, "10成": 100,
    "满仓": 100,
}
_CLEAR_THRESHOLD = 20   # 卖出行档位 ≤2成 → 视为清仓


def apply_trades_to_positions(prev: List[Dict], trades: List[Dict]) -> List[Dict]:
    """上次已知持仓 + 当日调仓(旧→新) → 推算持仓。不改入参, 返回新列表。

    prev 行: {stock_code, stock_name, position_ratio(数值), cost_price, ...}
    trades 行: {stock_code, stock_name, direction(买入/卖出), position_ratio(文字档), price}
    """
    pos: Dict[str, Dict] = {p.get("stock_code", ""): dict(p) for p in prev if p.get("stock_code")}
    for t in trades:
        code = str(t.get("stock_code", ""))
        if not code:
            continue
        ratio = RATIO_MAP.get(str(t.get("position_ratio", "")), None)
        if t.get("direction") == "买入":
            old = pos.get(code, {})
            pos[code] = {
                "stock_code": code,
                "stock_name": t.get("stock_name") or old.get("stock_name", ""),
                "position_ratio": ratio if ratio is not None else old.get("position_ratio"),
                "cost_price": t.get("price") or old.get("cost_price"),
                "current_price": None,      # 推算行无现价(诚实置 None, 校准后回填)
                "profit_ratio": None,
            }
        else:  # 卖出
            if code in pos:
                if ratio is None or ratio <= _CLEAR_THRESHOLD:
                    pos.pop(code)           # 清仓 → 移出持仓
                # 部分卖出剩余未知 → 保守不动(等校准)
    return list(pos.values())
