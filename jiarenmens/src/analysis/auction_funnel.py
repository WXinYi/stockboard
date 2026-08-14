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
    (2026-08-13 软化) 竞价时情绪/量能不作为阻塞条件, 全部报告不阻塞:
    - 情绪 strong: 超区间[25,75] 仅备注(冰点/过热参考)
    - 连板高度 lbgd < 2: 仅备注
    - 竞价量能比 last/s_zrcs: 仅备注(09:25 盘前量能数据未生成属正常, 缺失时 ratio=None)
    - 红盘占比 tSZ/(tSZ+tXD): 仅备注
    pass 恒为 True, reasons 为信息性备注; 选股收敛交由候选评分, 不因环境整体空仓。
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

def _score_b1(bid_net: Optional[float], circ_mv: Optional[float]) -> int:
    """S1 竞价资金强度(预测上涨第一因子): 净买/流通市值 归一化。
    竞价净额普遍很小(08-07 过门槛53只中 36只仅 0-0.1%), 阈值取平滑分档:
    >0.1% → 3(真金白银抢筹), 0.05-0.1% → 2, 0-0.05% → 1, 净卖 → 0(假高开)。"""
    if bid_net is None or circ_mv is None or circ_mv <= 0:
        return 0
    ratio = bid_net / circ_mv * 100
    if ratio > 0.1:
        return 3
    if ratio > 0.05:
        return 2
    if ratio > 0:
        return 1
    return 0


def _score_b2(turnover: Optional[float], circ_mv: Optional[float]) -> int:
    """竞价换手按流通市值分档(市值越小要求越低):
    达标 → 2 分, 达 0.5× 阈值 → 1 分(旧版一刀切 0 分导致大量票失分)"""
    if turnover is None or circ_mv is None:
        return 0
    if circ_mv < 3e9:
        threshold = 0.3
    elif circ_mv < 1e10:
        threshold = 0.15
    else:
        threshold = 0.08
    if turnover >= threshold:
        return 2
    if turnover >= threshold * 0.5:
        return 1
    return 0


def _score_b3(vol_ratio: Optional[float]) -> int:
    """S5 竞价量比健康: 3-8 倍=真实抢筹→2, 2-3 轻微放量或 8-15 过热→1,
    >15 巨量分歧警惕→0(可能出货对倒), <2 无量→0"""
    if vol_ratio is None:
        return 0
    if vol_ratio > 15:
        return 0
    if vol_ratio > 8:
        return 1
    if vol_ratio > 3:
        return 2
    if vol_ratio > 2:
        return 1
    return 0


def _score_b4(bid_rows: Optional[List[List]]) -> int:
    """竞价形态(力1, 9:15-9:25 逐分钟): 推土机(单调升)→3, 诱空(V型收回)→2, 末抢(最后30秒拉升)→1
    高开低走(出货形态)→0(由 _is_high_open_fade 硬门剔除); 无数据 → 0(标注缺数据)"""
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
        return 1
    # 推土机: 价格序列后半程单调上升(9:20 后 8 个点)
    tail = [px for _, px in pts if str(_) >= "09:20"]
    if len(tail) >= 6 and all(tail[i] <= tail[i + 1] * 1.0005 for i in range(len(tail) - 1)) and tail[-1] > tail[0]:
        return 3
    # 诱空: 前期下跌(9:20 前低点) + 9:20 后 V 型收回至高于前期
    pre = [px for _, px in pts if str(_) < "09:20"]
    post = [px for _, px in pts if str(_) >= "09:20"]
    if pre and post and min(pre) < pre[0] * 0.99 and post[-1] > pre[0]:
        return 2
    return 0


def _is_high_open_fade(bid_rows: Optional[List[List]]) -> bool:
    """高开低走(出货形态): 9:20 前拉高见顶, 9:20 后不可撤单段回落 >1% 且自身下行未收回。
    竞价前半程(可撤单段)虚涨拉高诱多, 后半程真金不再跟进 → 开盘大概率回落, 硬门剔除。"""
    if not bid_rows or len(bid_rows) < 8:
        return False
    pts = []
    for r in bid_rows:
        if len(r) >= 2:
            pts.append((str(r[0]), float(r[1])))
    if len(pts) < 8:
        return False
    pre = [px for ts, px in pts if str(ts) < "09:20"]
    post = [px for ts, px in pts if str(ts) >= "09:20"]
    if not pre or len(post) < 3:
        return False
    pre_peak = max(pre)
    return post[-1] < pre_peak * 0.99 and post[-1] < post[0]


def _score_force_match(bid_rows: Optional[List[List]]) -> int:
    """力2 撮合(量加权红量占比, 9:20 后不可撤单窗口): 红量=买方向累计量增量, 绿量=卖方向。
    ≥70%→3, ≥60%→2, ≥50%→1, <50%→0。修正 bid_buy_ratio 按笔数占比的失真(对倒一笔大单只算 1 笔,
    量加权才能反映真实买盘投入)。"""
    if not bid_rows:
        return 0
    red = green = 0.0
    prev = None
    for r in bid_rows:
        if len(r) < 4:
            continue
        ts, cum = str(r[0]), float(r[3])
        if ts < "09:20":
            prev = cum  # 只用 9:20 后段(不可撤单), 前段可撤单易做假
            continue
        if prev is None:
            prev = cum
            continue
        delta = max(0.0, cum - prev)  # 累计量快照非单调, 截断负增量
        if int(r[2]) == 1:
            red += delta
        else:
            green += delta
        prev = cum
    total = red + green
    if total <= 0:
        return 0
    ratio = red / total
    if ratio >= 0.70:
        return 3
    if ratio >= 0.60:
        return 2
    if ratio >= 0.50:
        return 1
    return 0


def _score_force_unfilled(unfilled_buy, circ_mv) -> int:
    """力3 委买(20分后不可撤单委买 / 流通市值): 排队待成交的潜在买压, 9:25 后追买动能。
    ≥1%→3, ≥0.5%→2, ≥0.2%→1, <0.2%→0。仅 MorningBiddingList 榜单股有 r[9];
    板块成分股缺失 → 0(中性, 不惩罚)。"""
    if unfilled_buy is None or not circ_mv or circ_mv <= 0:
        return 0
    ratio = float(unfilled_buy) / float(circ_mv)
    if ratio >= 0.01:
        return 3
    if ratio >= 0.005:
        return 2
    if ratio >= 0.002:
        return 1
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


def _score_b4_resonance(boards: List[str], gate_counts: Dict[str, int]) -> int:
    """S3 板块共振(预测上涨第二因子): 竞价抢筹的胜负手是板块。
    所属板块内过 B1 门槛的票 ≥3 只 → 3(资金共识/板块行情), 2 只 → 2, 否则 0。
    孤立抢筹多数是一日游, 板块联动才有持续性。"""
    if not boards:
        return 0
    best = 0
    for b in boards:
        best = max(best, gate_counts.get(b, 0))
    if best >= 3:
        return 3
    if best == 2:
        return 2
    return 0


def _score_gene(gene: Optional[Dict]) -> int:
    """S6 涨停基因延续性: 首板封板率 ≥70% → 2, ≥50% → 1, 无数据 → 0。
    历史封板率高的票, 竞价抢筹延续上涨概率更高。破板率 >40% 已在 L4 淘汰。"""
    if not gene or gene.get("data") is None:
        return 0
    seal = gene["data"].get("seal_pct") or 0
    if seal >= 70:
        return 2
    if seal >= 50:
        return 1
    return 0


def prelim_score(item: Dict) -> int:
    """初步评分(S1资金+S2换手+S5量比, 不需竞价分时) — 用于决定基因查询顺序"""
    return (_score_b1(item.get("bid_net"), item.get("circ_mv"))
            + _score_b2(item.get("turnover_ratio"), item.get("circ_mv"))
            + _score_b3(item.get("vol_ratio")))


def gate_b1(bid_pct: Optional[float]) -> Optional[str]:
    """B1 硬门槛: 竞价涨幅必须在 [1%,7%] 抢筹区间。
    <1% 不是抢筹(平开无动作); >7% 距涨停空间 ≤3%, 盈亏比天然差, 且一字板买不进。
    返回 None=通过, 否则返回淘汰原因。"""
    if bid_pct is None:
        return "无竞价涨幅数据"
    if not (1 <= bid_pct <= 7):
        return f"竞价涨幅{bid_pct:.2f}% 不在抢筹区间[1%,7%]"
    return None


def position_bonus(tag: Optional[str]) -> int:
    """身位加分(预测上涨因子): 连板 → +2, 首板 → +1。
    兼容标记格式: "连板"/"首板" 关键词 + "4天2板"/"6天5板"(按板数≥2=连板)。
    板块权重股无连板标记, 不加分。"""
    if not tag:
        return 0
    if "连板" in tag:
        return 2
    if "首板" in tag:
        return 1
    import re
    m = re.search(r"(\d+)板", tag)
    if m:
        return 2 if int(m.group(1)) >= 2 else 1
    return 0


def score_stock(row: List, bid_rows: Optional[List[List]] = None,
                boards: Optional[List[str]] = None,
                gate_counts: Optional[Dict[str, int]] = None,
                tag: str = "", gene: Optional[Dict] = None) -> Dict:
    """单只股票竞价评分 v3(上涨概率模型, 满分 15):
    [代码,名称,现价,实时涨幅,竞价量比,竞价额,竞价涨幅,竞价净额,竞价换手,流通市值,板块标签,...]
    S1 资金3 + S2 形态3 + S3 共振3 + S4 身位2 + S5 量比2 + S6 基因2。
    目标: 预测"竞价后买入能否挣钱" — 资金真实流入>涨幅数字, 9:20后不可撤单段行为>竞价整体,
    板块共振>个股孤立, 身位(昨涨停/连板)>无背景。B6 大单因子已删除(竞价期无逐笔成交)。"""
    factors = {
        "bid_price": float(row[2]) if len(row) > 2 and row[2] not in (None, "") else None,
        "bid_pct": float(row[6]) if len(row) > 6 and row[6] not in (None, "") else None,
        "bid_net": float(row[7]) if len(row) > 7 and row[7] not in (None, "") else None,
        "turnover": float(row[8]) if len(row) > 8 and row[8] not in (None, "") else None,
        "vol_ratio": float(row[4]) if len(row) > 4 and row[4] not in (None, "") else None,
        "circ_mv": float(row[9]) if len(row) > 9 and row[9] not in (None, "") else None,
        "unfilled_buy": float(row[11]) if len(row) > 11 and row[11] not in (None, "") else None,
    }
    # 竞价末分钟增量成交量(09:24-09:25), E 层 E1 与首分钟量对比用。
    # ⚠️ 不能用最后一行 r[3]=竞价全程累计量(10分钟总和), 否则"放量倍数"被严重稀释(实测 000859 累计15165 vs 末分钟仅~1900手)。
    if bid_rows and len(bid_rows[-1]) >= 4:
        try:
            last_min = max(str(r[0])[:5] for r in bid_rows if len(r) >= 2)  # 末分钟标签如 "09:24"
            last_cum = float(bid_rows[-1][3])
            prev_rows = [r for r in bid_rows if len(r) >= 4 and str(r[0])[:5] < last_min]
            base_cum = max(float(r[3]) for r in prev_rows) if prev_rows else 0.0
            factors["bid_vol_last"] = last_cum - base_cum  # 竞价末分钟增量成交量(手)
        except (TypeError, ValueError):
            factors["bid_vol_last"] = None
    else:
        factors["bid_vol_last"] = None
    # 竞价方向占比(委比代理, 9:20 后不可撤单窗口买方向占比, GetStockBid r[2]==1=买) + 竞价总成交量(手)。
    # 仅作为回测样本采集, 不参与评分(08-13 单日实证委比代理与结果反向: 亚泰买占4%赢, 有研买占100%跌)。
    if bid_rows:
        try:
            _tail = [rr for rr in bid_rows if len(rr) >= 3 and str(rr[0]) >= "09:20"]
            factors["bid_buy_ratio"] = (sum(1 for rr in _tail if int(rr[2]) == 1) / len(_tail)) if len(_tail) >= 3 else None
            factors["bid_vol_total"] = float(bid_rows[-1][3]) if len(bid_rows[-1]) >= 4 else None
        except (TypeError, ValueError):
            factors["bid_buy_ratio"] = None
            factors["bid_vol_total"] = None
    else:
        factors["bid_buy_ratio"] = None
        factors["bid_vol_total"] = None
    s_fund = _score_b1(factors["bid_net"], factors["circ_mv"])
    s_form = _score_b4(bid_rows)
    s_dir = _score_b5(bid_rows)
    s_res = _score_b4_resonance(boards or [], gate_counts or {})
    s_pos = position_bonus(tag)
    s_vol = _score_b3(factors["vol_ratio"])
    s_gene = _score_gene(gene)
    # 三力: 力2 撮合(量加权红量占比) + 力3 委买(20分后不可撤单委买/流通市值)
    s_force_match = _score_force_match(bid_rows)
    s_force_unfilled = _score_force_unfilled(factors["unfilled_buy"], factors["circ_mv"])
    total = s_fund + s_form + s_res + s_pos + s_vol + s_gene + s_force_match + s_force_unfilled
    # 方向修正: 9:23 后持续卖(买占比≤35%) → 形态分减 1(真实流出信号)
    if s_dir < 0 and s_form > 0:
        s_form -= 1
        total -= 1
    # 高开低走(出货形态)标记, run_funnel 硬门剔除(不进评分, 直接出局)
    shape_fade = _is_high_open_fade(bid_rows)
    factors["shape_fade"] = shape_fade
    return {"score": total, "max": 21, "factors": factors,
            "sub": {"S1资金": s_fund, "S2形态": s_form, "S3共振": s_res,
                    "S4身位": s_pos, "S5量比": s_vol, "S6基因": s_gene,
                    "S8撮合": s_force_match, "S9委买": s_force_unfilled},
            "missing": [k for k in ("bid_price", "bid_pct", "bid_net", "turnover",
                                    "vol_ratio", "circ_mv") if factors.get(k) is None]}


# =============================================================================
# 文章九大标准 × v3 融合(2026-08-13 起, 参数单日校准, 待回测调优)
# =============================================================================
# 来源: 知乎《集合竞价选股九大标准》单日(08-13)实证:
# - 量比>15 = 对倒(海泰新光 量比16 收-12.11%) → 硬门剔除
# - S7 技术分: MA60/低位启动/MACD·KDJ 共振加分, 高位(近20日>30%)减分;
#   委比代理今天与结果反向(亚泰买占4%赢/有研买占100%跌) → 不参与评分, 仅展示参考。
# 所有权重为"待回测校准"临时值, backtest_factors.py 用积累样本调优后可改。
S7_BONUS_MA60 = 1.0       # 站上60日线
S7_BONUS_MK = 0.5         # MACD 与 KDJ 同时向上(共振)
S7_BONUS_LOW = 0.5        # 近20日涨幅 ∈ (0, 20%): 低位启动
S7_PENALTY_HIGH = -1.0    # 近20日涨幅 > 30%: 高位
VOL_RATIO_LIMIT = 15.0    # 量比超过=对倒嫌疑, 硬门
FUSED_CORE = 13.0         # 融合分核心线(21分制: 原10/15≈67% 上限, 21×0.67≈14, 取13略松作起点, 待回测校准)
FUSED_WATCH = 11.0        # 融合分备选线


def article_s7(factors: Dict) -> tuple:
    """文章九大标准 → 技术分 S7(浮动). 纯加减分不否决; 日K因子缺失时该分量缺失部分为 0。
    返回 (s7, 说明)"""
    s7, parts = 0.0, []
    if factors.get("ma60_above") is True:
        s7 += S7_BONUS_MA60
        parts.append("站上MA60")
    if factors.get("macd_ok") is True and factors.get("kdj_ok") is True:
        s7 += S7_BONUS_MK
        parts.append("MACD·KDJ共振")
    ret20 = factors.get("ret20")
    if ret20 is not None:
        if 0 < ret20 < 0.20:
            s7 += S7_BONUS_LOW
            parts.append(f"低位{ret20:.0%}")
        elif ret20 > 0.30:
            s7 += S7_PENALTY_HIGH
            parts.append(f"高位{ret20:.0%}")
    return round(s7, 2), ("；".join(parts) or "技术中性")


def fuse_score(item: Dict, s7: float) -> float:
    """融合分 = v3 总分(S1-S6, 满分15) + S7 技术分(满分~2)"""
    return round(float(item.get("score") or 0) + s7, 2)


def fuse_tier(item: Dict, s7: float) -> str:
    """基于融合分定层(在 run_funnel 已入围的 core/watch 内重排, 不新增):
    core 需融合分≥FUSED_CORE 且 S1资金>0; 否则 watch。"""
    s1 = (item.get("sub") or {}).get("S1资金") or 0
    if fuse_score(item, s7) >= FUSED_CORE and s1 > 0:
        return "core"
    return "watch"


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
               score_threshold: int = 10) -> Dict:
    """执行漏斗: 候选池 = 强势板块成分 + 竞价列表, 逐层过滤
    B1 硬门槛(1-7%) → 高开低走硬门(出货形态) → 评分v3(满分21: 资金/形态/共振/身位/量比/基因 + 三力撮合/委买)
    → L4 基因 → 分层
    分层: core(核心, ≥阈值 且 资金净买) + watch(备选, ≥阈值-3)
    """
    env_result = env_check(env["mood"], env["capacity"], env["bid_total"], env["bid_count"])
    if not env_result["pass"]:
        return {"env": env_result, "boards": [], "candidates": [], "watch": [],
                "empty_reason": "市场环境不满足, 空仓"}

    # 板块共振: 每板块过 B1 门槛的票数(资金共识度量)
    gate_counts: Dict[str, int] = {}
    for r in pool_rows:
        if not gate_b1(r.get("bid_pct")) and r.get("boards"):
            for b in r["boards"]:
                gate_counts[b] = gate_counts.get(b, 0) + 1

    candidates = []
    watch = []
    rejected = []
    for r in pool_rows:
        code = str(r.get("code") or "")
        if not code:
            continue
        name = r.get("name") or ""
        # B1 硬门槛: 抢筹区间之外直接出局
        gate = gate_b1(r.get("bid_pct"))
        if gate:
            rejected.append({"code": code, "name": name, "reason": gate})
            continue
        gene = gene_check(genes.get(code))
        if not gene["pass"]:
            rejected.append({"code": code, "name": name, "reason": gene["reason"]})
            continue
        # 对倒硬门(文章 #3 延伸): 量比>15 大概率对倒, 直接剔除(S5 归零不够, 08-13 海泰量比16 -12.11%)
        _vr = r.get("vol_ratio")
        if _vr is not None and float(_vr) > VOL_RATIO_LIMIT:
            rejected.append({"code": code, "name": name,
                             "reason": f"量比{float(_vr):.0f} > {VOL_RATIO_LIMIT:.0f} 对倒嫌疑"})
            continue
        sc = score_stock([code, name, r.get("price"), r.get("change_pct"), r.get("vol_ratio"),
                          r.get("limit_up_buy"), r.get("bid_pct"), r.get("bid_net"),
                          r.get("turnover_ratio"), r.get("circ_mv"), r.get("plates"),
                          r.get("unfilled_buy"), 0],
                         stock_bids.get(code) if stock_bids else None,
                         r.get("boards") or [], gate_counts, r.get("tag") or "", gene)
        # 高开低走硬门(力1 出货形态): 前半程拉高见顶 + 9:20 后回落未收回 → 直接出局
        if sc["factors"].get("shape_fade"):
            rejected.append({"code": code, "name": name, "reason": "竞价高开低走 出货形态"})
            continue
        total = sc["score"]
        # 板块共振票数(该股所属板块中过 B1 门槛的票数, 供推送亮点行展示)
        resonance = max((gate_counts.get(b, 0) for b in (r.get("boards") or [])), default=0)
        item = {
            "code": code, "name": name, "score": total, "tier": None, "max": sc["max"],
            "factors": sc["factors"], "sub": sc["sub"], "gene": gene,
            "boards": r.get("boards") or [], "tag": r.get("tag") or "",
            "missing": sc["missing"], "resonance": resonance,
        }
        # 资金必须净买(S1>0), 否则即使总分高也是假高开; 备选同样要求资金非负
        if total >= score_threshold and sc["sub"]["S1资金"] > 0:
            item["tier"] = "core"
            candidates.append(item)
        elif total >= score_threshold - 2 and sc["sub"]["S1资金"] >= 1:
            item["tier"] = "watch"
            watch.append(item)
        else:
            rejected.append({"code": code, "name": name,
                             "reason": f"评分{total}/{sc['max']} 低于备选线{score_threshold - 3}"})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    watch.sort(key=lambda c: c["score"], reverse=True)
    return {"env": env_result, "boards": boards, "candidates": candidates, "watch": watch,
            "rejected": rejected, "empty_reason": None}
