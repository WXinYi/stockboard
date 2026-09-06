"""
竞价环境与强势板块选择

存量评分漏斗(auction_funnel.py)2026-09-06 删除, 这两个被新"出击选股"流程
每日调用的纯函数原样迁出至此:
  - env_check:    推送/auction.json 的环境行(可出手/空仓 + 红盘/委买/量能)
  - board_select: 强势板块两路信号合并(竞价爆量 + 板块强度)
"""
from typing import Dict, List


def env_check(mood: Dict, capacity: Dict, bid_total: Dict, bid_count: List) -> Dict:
    """市场环境检查 → {pass, reasons, data}
    (2026-08-13 软化) 竞价时情绪/量能不作为阻塞条件, 全部报告不阻塞:
    - 情绪 strong: 超区间[25,75] 仅备注(冰点/过热参考)
    - 连板高度 lbgd < 2: 仅备注
    - 竞价量能比 last/s_zrcs: 仅备注(09:25 盘前量能数据未生成属正常, 缺失时 ratio=None)
    - 红盘占比 tSZ/(tSZ+tXD): 仅备注
    pass 恒为 True, reasons 为信息性备注; 出击收敛交由周期闸门, 不因环境整体空仓。
    """
    info = (mood.get("info") or [{}])[0]
    strong = int(info.get("strong") or 0)
    lbgd = int(info.get("lbgd") or 0)
    cap = capacity.get("info") or {}
    bt = bid_total.get("info") or {}

    notes = []
    if not (25 <= strong <= 75):
        notes.append(f"情绪值{strong} 超区间[25,75](参考: 过低冰点/过高释放亏钱效应)")
    if lbgd < 2:
        notes.append(f"连板高度{lbgd} < 2 (参考: 赚钱效应弱)")
    # 量能: 09:25 盘前当天数据未生成属正常(capacity 为空), 此时 ratio=None 而非 0(避免误报缩量)
    last, s_zrcs = cap.get("last"), cap.get("s_zrcs")
    if last is None or s_zrcs is None:
        ratio = None
    else:
        try:
            ratio = float(last) / float(s_zrcs)
        except (ValueError, TypeError, ZeroDivisionError):
            ratio = None
    if ratio is not None and ratio < 0.8:
        notes.append(f"竞价量能比 {ratio:.2f} < 0.8 (参考: 缩量)")
    try:
        red = int(bt.get("tSZ") or 0)
        green = int(bt.get("tXD") or 0)
        red_ratio = red / (red + green) if (red + green) else 0
    except (ValueError, TypeError):
        red_ratio = None
    if red_ratio is not None and red_ratio < 0.4:
        notes.append(f"红盘占比 {red_ratio:.0%} < 40% (参考)")

    if not notes:
        notes.append("环境正常")
    return {"pass": True, "reasons": notes,
            "data": {"strong": strong, "lbgd": lbgd, "capacity_ratio": ratio,
                     "red_ratio": red_ratio, "bid_count": bid_count,
                     "bid_total": bt.get("tJJJE"), "bid_total_prev": bt.get("lJJJE")}}


def board_select(board_bid: Dict, ranking: Dict, max_boards: int = 8) -> List[Dict]:
    """强势板块选择: 竞价爆量板块(List1新增+List2延续) + 竞价时段强度榜(涨幅>0 且 主力净额>0)。
    两路信号量纲不同(burst 2-6 倍 vs strength 10-30), 各自取半再合并去重, 避免互相挤掉。"""
    burst_boards: Dict[str, Dict] = {}
    for lt, key in (("L1", "List1"), ("L2", "List2")):
        for row in board_bid.get(key, []):
            if len(row) < 6:
                continue
            burst_boards[row[0]] = {
                "code": row[0], "name": row[1], "burst": float(row[2]),
                "amount": float(row[3]), "main_net": float(row[5]),
                "src": f"爆量{lt}", "strength": 0.0,
            }
    strength_boards: Dict[str, Dict] = {}
    for row in ranking.get("list", []):
        if len(row) < 19:
            continue
        code, name, strength, chg = row[0], row[1], float(row[2]), float(row[3])
        main_net = float(row[6])
        if chg <= 0 or main_net <= 0:
            continue
        if code in burst_boards:  # 双信号同板块 → 升级标记
            burst_boards[code]["strength"] = strength
            burst_boards[code]["src"] = "爆量+强度"
            continue
        strength_boards[code] = {"code": code, "name": name, "burst": 0.0,
                                 "amount": float(row[5]), "main_net": main_net,
                                 "src": "强度", "strength": strength}
    half = max(1, max_boards // 2)
    ranked = (sorted(burst_boards.values(), key=lambda b: b["burst"], reverse=True)[:half]
              + sorted(strength_boards.values(), key=lambda b: b["strength"], reverse=True)[:half])
    return ranked[:max_boards]
