"""
竞价抢筹漏斗计算器

七层漏斗收敛为四段(09:30 前出结论, E 层开盘确认后置):
  L1 环境(任一不过 → 空仓): 情绪/连板高度/竞价量能比/红盘占比
  L0 板块: 竞价爆量板块 + 竞价时段板块强度 → 强势板块集合
  L3 竞价评分(15分制, ≥8 过): 涨幅/换手/量比/形态/方向/大单
  L4 基因(淘汰制): 首板封板率/破板率(开盘啦 GetZhangTingGene 直接数据)

阈值目前为文档经验值 + 开盘啦自带提示, 待迭代2 用积累数据回测校准。
"""
from typing import Any, Dict, List, Optional


# =============================================================================
# L1 环境
# =============================================================================

def env_check(mood: Dict, capacity: Dict, bid_total: Dict, bid_count: List) -> Dict:
    """市场环境检查 → {pass, reasons, data}
    - 情绪 strong: 开盘啦提示 <25 冰点 / >75 过热 → 空仓
    - 连板高度 lbgd < 2 → 无赚钱效应
    - 竞价量能比 last/s_zrcs < 0.8 → 大幅缩量
    - 红盘占比 tSZ/(tSZ+tXD) < 0.4 → 情绪冰点
    """
    info = (mood.get("info") or [{}])[0]
    strong = int(info.get("strong") or 0)
    lbgd = int(info.get("lbgd") or 0)
    cap = capacity.get("info") or {}
    bt = bid_total.get("info") or {}

    reasons = []
    ok = True
    if not (25 <= strong <= 75):
        ok = False
        reasons.append(f"情绪值{strong} 超区间[25,75](开盘啦提示: 过低冰点/过高释放亏钱效应)")
    if lbgd < 2:
        ok = False
        reasons.append(f"连板高度{lbgd} < 2, 无赚钱效应")
    try:
        ratio = float(cap.get("last") or 0) / float(cap.get("s_zrcs") or 1)
        if ratio < 0.8:
            ok = False
            reasons.append(f"竞价量能比 {ratio:.2f} < 0.8, 缩量")
    except (ValueError, TypeError):
        ratio = None
        reasons.append("量能数据缺失")
    try:
        red = int(bt.get("tSZ") or 0)
        green = int(bt.get("tXD") or 0)
        red_ratio = red / (red + green) if (red + green) else 0
        if red_ratio < 0.4:
            ok = False
            reasons.append(f"红盘占比 {red_ratio:.0%} < 40%")
    except (ValueError, TypeError):
        red_ratio = None

    if not reasons:
        reasons.append("环境正常")
    return {"pass": ok, "reasons": reasons,
            "data": {"strong": strong, "lbgd": lbgd, "capacity_ratio": ratio,
                     "red_ratio": red_ratio, "bid_count": bid_count}}


# =============================================================================
# L0 板块
# =============================================================================

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


# =============================================================================
# L3 竞价评分(15 分制)
# =============================================================================

def _score_b1(bid_pct: Optional[float]) -> int:
    """竞价涨幅: 1-3%→1, 3-5%→2, 5-7%→1, 其他→0"""
    if bid_pct is None:
        return 0
    if 1 <= bid_pct < 3:
        return 1
    if 3 <= bid_pct < 5:
        return 2
    if 5 <= bid_pct < 7:
        return 1
    return 0


def _score_b2(turnover: Optional[float], circ_mv: Optional[float]) -> int:
    """竞价换手按流通市值分档: <30亿 >0.3% / 30-100亿 >0.15% / >100亿 >0.08% → 2"""
    if turnover is None or circ_mv is None:
        return 0
    if circ_mv < 3e9:
        return 2 if turnover > 0.3 else 0
    if circ_mv < 1e10:
        return 2 if turnover > 0.15 else 0
    return 2 if turnover > 0.08 else 0


def _score_b3(vol_ratio: Optional[float]) -> int:
    """竞价量比: >3→3, >2→2"""
    if vol_ratio is None:
        return 0
    if vol_ratio > 3:
        return 3
    if vol_ratio > 2:
        return 2
    return 0


def _score_b4(bid_rows: Optional[List[List]]) -> int:
    """竞价形态(9:15-9:25 逐分钟): 推土机(单调升)→3, 诱空(V型收回)→3, 末抢(最后30秒拉升)→2
    无数据 → 0(标注缺数据)"""
    if not bid_rows or len(bid_rows) < 5:
        return 0
    pts = []
    for r in bid_rows:
        if len(r) >= 2:
            pts.append((str(r[0]), float(r[1])))
    if len(pts) < 5:
        return 0
    # 末抢: 最后 30 秒(09:24:30 后)价格拉升 ≥1%
    last_ts, last_px = pts[-1]
    pre_ts, pre_px = pts[-4] if len(pts) >= 4 else pts[0]
    if "09:24" in last_ts and pre_px > 0 and (last_px / pre_px - 1) >= 0.01:
        return 2
    # 推土机: 价格序列后半程单调上升(9:20 后 8 个点)
    tail = [px for _, px in pts if str(_) >= "09:20"]
    if len(tail) >= 6 and all(tail[i] <= tail[i + 1] * 1.0005 for i in range(len(tail) - 1)) and tail[-1] > tail[0]:
        return 3
    # 诱空: 前期下跌(9:20 前低点) + 9:20 后 V 型收回至高于前期
    pre = [px for _, px in pts if str(_) < "09:20"]
    post = [px for _, px in pts if str(_) >= "09:20"]
    if pre and post and min(pre) < pre[0] * 0.99 and post[-1] > pre[0]:
        return 3
    return 0


def _score_b5(bid_rows: Optional[List[List]]) -> int:
    """未匹配量方向近似(GetStockBid 买卖方向标记): 9:23 后买方向占比>60% → 2, 持续卖 → -2"""
    if not bid_rows:
        return 0
    tail = [r for r in bid_rows if len(r) >= 3 and str(r[0]) >= "09:23"]
    if len(tail) < 3:
        return 0
    buys = sum(1 for r in tail if int(r[2]) == 1)
    buy_ratio = buys / len(tail)
    if buy_ratio >= 0.6:
        return 2
    if buy_ratio <= 0.35:
        return -2
    return 0


def _score_b6(monitor: Dict) -> int:
    """大单方向(GetMainMonitor 分档): 主动买(方向2)金额 > 主动卖(方向4) → 2, 反偏 → -2
    无大单数据 → 0(标注缺数据)"""
    rows = monitor.get("List") or []
    if not rows:
        return 0
    buy = sum(float(r[3] or 0) for r in rows if len(r) >= 4 and int(r[0]) == 2)
    sell = sum(float(r[3] or 0) for r in rows if len(r) >= 4 and int(r[0]) == 4)
    if buy + sell == 0:
        return 0
    return 2 if buy > sell else -2


def prelim_score(item: Dict) -> int:
    """初步评分(B1+B2+B3, 不需竞价分时/大单) — 用于决定给哪些股票拉 B4/B5/B6 数据"""
    return (_score_b1(item.get("bid_pct")) + _score_b2(item.get("turnover_ratio"), item.get("circ_mv"))
            + _score_b3(item.get("vol_ratio")))


def score_stock(row: List, bid_rows: Optional[List[List]] = None, monitor: Optional[Dict] = None) -> Dict:
    """单只股票竞价评分(GetBKJJBL 行格式):
    [代码,名称,现价,实时涨幅,竞价量比,竞价额,竞价涨幅,竞价净额,竞价换手,流通市值,板块标签,...]"""
    factors = {
        "bid_price": float(row[2]) if len(row) > 2 and row[2] not in (None, "") else None,
        "bid_pct": float(row[6]) if len(row) > 6 and row[6] not in (None, "") else None,
        "turnover": float(row[8]) if len(row) > 8 and row[8] not in (None, "") else None,
        "vol_ratio": float(row[4]) if len(row) > 4 and row[4] not in (None, "") else None,
        "circ_mv": float(row[9]) if len(row) > 9 and row[9] not in (None, "") else None,
    }
    # 竞价末分钟累计量(GetStockBid 最后一行 r[3]), E 层 E1 对比用
    if bid_rows and len(bid_rows[-1]) >= 4:
        try:
            factors["bid_vol_last"] = float(bid_rows[-1][3])
        except (TypeError, ValueError):
            factors["bid_vol_last"] = None
    else:
        factors["bid_vol_last"] = None
    s_b1 = _score_b1(factors["bid_pct"])
    s_b2 = _score_b2(factors["turnover"], factors["circ_mv"])
    s_b3 = _score_b3(factors["vol_ratio"])
    s_b4 = _score_b4(bid_rows)
    s_b5 = _score_b5(bid_rows)
    s_b6 = _score_b6(monitor or {})
    total = s_b1 + s_b2 + s_b3 + s_b4 + s_b5 + s_b6
    return {"score": total, "max": 15, "factors": factors,
            "sub": {"B1涨幅": s_b1, "B2换手": s_b2, "B3量比": s_b3,
                    "B4形态": s_b4, "B5方向": s_b5, "B6大单": s_b6},
            "missing": [k for k, v in factors.items() if v is None]}


# =============================================================================
# L4 基因(淘汰制)
# =============================================================================

def gene_check(gene: Optional[List]) -> Dict:
    """涨停基因(GetZhangTingGene 免Token): [涨停次数, 5%溢价次, 次日红盘%, 首板封板率%, 破板率%, 连板率%]
    封板率 < 30% 或 破板率 > 40% → 淘汰(渣男股)"""
    if not gene or len(gene) < 6:
        return {"pass": True, "reason": "无基因数据", "data": None}
    seal, brk = float(gene[3]), float(gene[4])
    reasons = []
    ok = True
    if seal < 30:
        ok = False
        reasons.append(f"首板封板率{seal:.0f}% < 30%")
    if brk > 40:
        ok = False
        reasons.append(f"首板破板率{brk:.0f}% > 40%")
    return {"pass": ok, "reason": "；".join(reasons) or f"基因正常(封板{seal:.0f}%/破板{brk:.0f}%)",
            "data": {"limit_count": int(gene[0]), "premium_5pct": int(gene[1]),
                     "next_red_pct": float(gene[2]), "seal_pct": seal, "break_pct": brk,
                     "consecutive_pct": float(gene[5])}}


# =============================================================================
# 主流程
# =============================================================================

def run_funnel(env: Dict, boards: List[Dict], pool_rows: List[Dict], genes: Dict[str, List],
               stock_bids: Optional[Dict[str, List]] = None,
               monitors: Optional[Dict[str, Dict]] = None,
               score_threshold: int = 8) -> Dict:
    """执行漏斗: 候选池 = 强势板块成分 + 竞价列表, 逐层过滤"""
    env_result = env_check(env["mood"], env["capacity"], env["bid_total"], env["bid_count"])
    if not env_result["pass"]:
        return {"env": env_result, "boards": [], "candidates": [], "empty_reason": "市场环境不满足, 空仓"}

    candidates = []
    for r in pool_rows:
        code = str(r.get("code") or "")
        if not code:
            continue
        name = r.get("name") or ""
        sc = score_stock([code, name, r.get("price"), r.get("change_pct"), r.get("vol_ratio"),
                          r.get("limit_up_buy"), r.get("bid_pct"), r.get("bid_net"),
                          r.get("turnover_ratio"), r.get("circ_mv"), r.get("plates"), 0, 0],
                         stock_bids.get(code) if stock_bids else None,
                         (monitors or {}).get(code))
        if sc["score"] < score_threshold:
            continue
        gene = gene_check(genes.get(code))
        if not gene["pass"]:
            continue
        candidates.append({
            "code": code, "name": name, "score": sc["score"], "max": sc["max"],
            "factors": sc["factors"], "sub": sc["sub"], "gene": gene,
            "boards": r.get("boards") or [], "tag": r.get("tag") or "",
            "missing": sc["missing"],
        })
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"env": env_result, "boards": boards, "candidates": candidates, "empty_reason": None}
