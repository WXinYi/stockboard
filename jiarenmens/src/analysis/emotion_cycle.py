#!/usr/bin/env python3
"""
超短情绪周期引擎 v1 (规则可回测)

数据源(auction.db, 只读):
  - market_breadth: 涨停/跌停/炸板数/破板率 250 天 (backfill_emotion.py 回补)
  - limit_pool:     每日涨停池(板位/涨停时间/封单/主力净额/成交额/板块)

关键处理: DailyLimitPerformance 的 PidType=5 是"≥5板"封顶桶, 真实连板高度按
个股逐日连续在池反推(昨天在池 → 今天 = 昨天高度+1)。

输出 compute_cycle(date): stage 六段阶段 / reasons / metrics(高度·涨停·破板率·晋级率)
  / matrix 3×3 / mainlines 主线 / leaders 龙头谱系(空间锚·总龙头·板块龙头·中军·补涨)
  / playbook 阶段操作提示; 落库 data/analysis.db (本地独享)。

规则来源: 情绪周期文章(金字塔/矩阵/口诀) + 六段模型; 阈值集中 CYCLE_CFG, 待回测校准。
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "auction.db"
ADB = ROOT / "data" / "analysis.db"

CYCLE_CFG = {
    "ice_height": 3,           # 真实高度≤3 → 冰点候选(文章二: 降至2B-3B 直接确认)
    "ice_zt_ratio": 0.70,      # 涨停数 < ma5×0.7
    "ice_broke": 30.0,         # 破板率 >30%
    "gaochao_height": 6,       # 真实高度≥6 → 高潮
    "gaochao_zt_ratio": 1.40,  # 涨停数 ≥ ma5×1.4
    "tui_height_drop": 2,      # 高度单日降≥2级 → 退潮候选
    "tui_zt_ratio": 0.80,      # 涨停数 < ma5×0.8
    "fajiao_height": 5,        # 五板封住定龙头 → 发酵确认
    "promote_strong": 0.5,     # 层晋级率 ≥50% → 强
    "promote_weak": 0.25,      # <25% → 弱
    "mainline_min": 3,         # 主线板块最少涨停数
}

STAGE_PLAYBOOK = {
    "冰点":  "空仓应对为主；留意逆市连板(新周期火种)；首板套利只在最强板块",
    "启动":  "打低位首板/1进2 为主；情绪低点做龙头，穿越反包空间龙",
    "发酵":  "五板封住定龙头：上龙头/同梯队强者，次日高开抢；板块弱转强打板龙头、低吸中后排",
    "高潮":  "接力只做龙头(秒板/放量分歧板)；板块爆炸买跟风但去弱留强；高峰跟风不碰",
    "分歧":  "抱团龙头与妖股、低吸龙头；尽量避开中位股(核按钮高发)",
    "退潮":  "空仓纪律优先；只观察最高标(尾盘炸板最强还可捡)；从此高位不接力",
}


def _rows(sql, args=()):
    with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, args)]


def _ma(seq, n):
    seq = [x for x in seq if x is not None][-n:]
    return sum(seq) / len(seq) if seq else None


def load_pool(days=40, extra_rows=None, exclude_date=None):
    """近 N 个池日全量行(+可选当日实时行, exclude_date=实时行覆盖的库内日期) + 真实连板高度反推"""
    rows = _rows("""SELECT date, code, name, pid_type, zt_time, seal_amount, main_net,
                           amount, plates FROM limit_pool
                   WHERE date >= (SELECT MIN(date) FROM (SELECT DISTINCT date FROM limit_pool
                                    ORDER BY date DESC LIMIT ?))
                   ORDER BY date""", (days,))
    if exclude_date:
        rows = [r for r in rows if r["date"] != exclude_date]
    if extra_rows:
        rows = rows + extra_rows
        rows.sort(key=lambda r: r["date"])
    streaks: dict[tuple, int] = {}
    prev_date, prev_codes = None, {}
    for r in rows:
        d = r["date"]
        if d != prev_date:
            prev_codes = {c: s for (dd, c), s in streaks.items() if dd == prev_date}
            prev_date = d
        pid = r["pid_type"] or 0
        if r["code"] in prev_codes:
            h = prev_codes[r["code"]] + 1          # 昨天在池 → 连板+1 (解开 pid=5 封顶)
        else:
            h = pid if pid < 5 else 5              # 新面孔: pid<5 精确, ≥5 视为 5 板起步
        r["height"] = h
        streaks[(d, r["code"])] = h
    return rows


def ladder_split(rows):
    lad = {"low": [], "mid": [], "high": []}
    for r in rows:
        h = r["height"]
        (lad["low"] if h <= 2 else lad["mid"] if h <= 5 else lad["high"]).append(r)
    return lad


def promotion_rate(prev_rows, cur_rows, lo, hi):
    """昨日该层(height∈[lo,hi]) → 今日 height+1 的比例"""
    prev = {r["code"]: r["height"] for r in prev_rows if lo <= r["height"] <= hi}
    if not prev:
        return None
    cur_h = {r["code"]: r["height"] for r in cur_rows}
    hit = sum(1 for c, h in prev.items() if cur_h.get(c, 0) == h + 1)
    return hit / len(prev)


def board_mainlines(cur_rows):
    """主线板块: 涨停数为主排序(生命力强), 高度/成交额加权"""
    agg = {}
    for r in cur_rows:
        for b in str(r["plates"] or "").split("、"):
            b = b.strip()
            if not b:
                continue
            a = agg.setdefault(b, {"board": b, "count": 0, "max_pid": 0,
                                   "amount": 0.0, "names": []})
            a["count"] += 1
            a["max_pid"] = max(a["max_pid"], r["height"])
            a["amount"] += r["amount"] or 0
            if len(a["names"]) < 8:
                a["names"].append(f'{r["name"]}({r["height"]}板)')
    out = [a for a in agg.values() if a["count"] >= CYCLE_CFG["mainline_min"]]
    return sorted(out, key=lambda a: (a["count"], a["max_pid"]), reverse=True)[:6]


def classify_leaders(cur_rows, mainlines):
    """龙头谱系: 空间锚(全场最高连板) / 总龙头(主线1最高) / 板块龙头 / 中军(成交额) / 补涨"""
    if not cur_rows:
        return []
    out, used = [], set()
    top = max(cur_rows, key=lambda r: r["height"])
    used.add(top["code"])

    def seal_note(r):
        return f"封单 {r['seal_amount']/1e8:.2f}亿" if r["seal_amount"] else ""

    anchor_in_main = None
    for m in mainlines:
        if m["board"] in str(top["plates"] or ""):
            anchor_in_main = m["board"]
    out.append({"code": top["code"], "name": top["name"], "pid": top["height"],
                "role": "空间锚(最高连板)",
                "note": (f"{top['height']}连板 | {anchor_in_main + '主线空间龙, ' if anchor_in_main else ''}"
                         f"{seal_note(top)}" + (" | ⚠️封单衰减看分歧" if top["seal_amount"] and
                                               top["seal_amount"] < 5e7 and top["height"] >= 5 else ""))})
    for i, m in enumerate(mainlines[:2]):
        members = [r for r in cur_rows
                   if m["board"] in str(r["plates"] or "") and r["code"] not in used]
        if not members:
            continue
        lead = max(members, key=lambda r: r["height"])
        role = "总龙头" if i == 0 else f"板块龙头({m['board']})"
        if anchor_in_main == m["board"]:
            continue  # 主线由空间锚带队, 已在首行
        out.append({"code": lead["code"], "name": lead["name"], "pid": lead["height"],
                    "role": role, "note": f"主线[{m['board']}] {m['count']}只涨停, {seal_note(lead)}"})
        used.add(lead["code"])
        rest = [r for r in members if r["code"] != lead["code"]]
        if rest:
            mid = max(rest, key=lambda r: r["amount"] or 0)
            if (mid["amount"] or 0) > 5e8:
                out.append({"code": mid["code"], "name": mid["name"], "pid": mid["height"],
                            "role": f"中军({m['board']})",
                            "note": f"成交 {mid['amount']/1e8:.1f}亿 容量核心"})
                used.add(mid["code"])
            bu = max((r for r in rest if r["code"] not in used and 2 <= r["height"] < lead["height"]),
                     key=lambda r: r["height"], default=None)
            if bu:
                out.append({"code": bu["code"], "name": bu["name"], "pid": bu["height"],
                            "role": f"补涨/卡位({m['board']})", "note": "龙头被关时的板块内补涨位"})
                used.add(bu["code"])
    return out


def compute_cycle(date_str: str | None = None, persist: bool = True,
                  rt_today: dict | None = None) -> dict:
    """rt_today(盘中实时注入): {"rows": 当日涨停池行, "breadth": [涨停,跌停,自然涨停,
    曾跌停,破板率,炸板数,日期]} — 提供时以实时数据为当日口径(午盘/尾盘推送用)"""
    if rt_today:
        rt_date = rt_today["rows"][0]["date"] if rt_today["rows"] else None
        pool_rows = load_pool(extra_rows=rt_today["rows"], exclude_date=rt_date)
    else:
        pool_rows = load_pool()
    dates = sorted({r["date"] for r in pool_rows})
    date_str = date_str or dates[-1]
    cur_rows = [r for r in pool_rows if r["date"] == date_str]
    idx = dates.index(date_str)
    prev_date = dates[idx - 1] if idx > 0 else None
    prev_rows = [r for r in pool_rows if r["date"] == prev_date]

    breadth_db = _rows("""SELECT date, zt, broke_rate FROM market_breadth WHERE date<=?
                          ORDER BY date DESC LIMIT 10""", (date_str,))
    if rt_today and rt_today.get("breadth"):
        b = rt_today["breadth"]
        # 实时行作为"今日"，库内 ma5 窗口排除今日(防重复计入)
        breadth = [{"date": str(b[6]), "zt": b[0], "broke_rate": b[4]}] + \
                  [x for x in breadth_db if x["date"] < date_str][:9]
    else:
        breadth = breadth_db
    cfg = CYCLE_CFG
    zt = breadth[0]["zt"] if breadth else None
    zt_ma5 = _ma([b["zt"] for b in breadth[1:6]], 5)   # 前 5 日均值(不含当日)
    broke = breadth[0]["broke_rate"] if breadth else None
    broke_ma5 = _ma([b["broke_rate"] for b in breadth[1:6]], 5)

    heights = {d: max(r["height"] for r in pool_rows if r["date"] == d) for d in dates}
    height = heights.get(date_str, 0)
    height_prev = heights.get(prev_date)
    h_trend = [heights.get(d) for d in dates[-4:]]

    lad_c, lad_p = ladder_split(cur_rows), ladder_split(prev_rows)
    promo = {
        "low": promotion_rate(lad_p["low"], cur_rows, 1, 2),
        "mid": promotion_rate(lad_p["mid"], cur_rows, 3, 5),
        "high": promotion_rate(lad_p["high"], cur_rows, 6, 99),
    }
    ladder_counts = {k: len(v) for k, v in lad_c.items()}

    def tier_state(rate, cnt_now, cnt_prev):
        if rate is not None:
            return "强" if rate >= cfg["promote_strong"] else \
                   ("弱" if rate < cfg["promote_weak"] else "平衡")
        return "强" if cnt_now > cnt_prev else ("平衡" if cnt_now == cnt_prev else "弱")

    matrix = {"high": tier_state(promo["high"], ladder_counts["high"], len(lad_p["high"])),
              "mid": tier_state(promo["mid"], ladder_counts["mid"], len(lad_p["mid"]))}

    zr = (zt / zt_ma5) if (zt and zt_ma5) else None
    zr_txt = f"{zr:.0%} of ma5" if zr is not None else "无ma5"
    mid_txt = f"{promo['mid']:.0%}" if promo["mid"] is not None else "无数据"
    hdrop = (height_prev - height) if (height and height_prev) else None
    reasons = []
    # 1) 退潮
    if (hdrop is not None and hdrop >= cfg["tui_height_drop"]) or \
            (zr is not None and zr < cfg["tui_zt_ratio"] and (broke or 0) > (broke_ma5 or 0)):
        stage = "退潮"
        reasons.append(f"高度 {height_prev}→{height}" + (f"(降{hdrop}级)" if hdrop else "") +
                       f"，涨停 {zt} 只({zr_txt})，破板率 {broke}%")
    # 2) 冰点
    elif height <= cfg["ice_height"] and (
            (zr is not None and zr < cfg["ice_zt_ratio"]) or (broke or 0) > cfg["ice_broke"]):
        stage = "冰点"
        reasons.append(f"高度仅 {height}B(≤{cfg['ice_height']}B 直接确认)，涨停 {zt} 只，破板率 {broke}%")
    # 3) 高潮
    elif height >= cfg["gaochao_height"] or (zr is not None and zr >= cfg["gaochao_zt_ratio"]):
        stage = "高潮"
        reasons.append(f"高度 {height}B(近4日 {'→'.join(map(str, h_trend))})，涨停 {zt} 只({zr_txt})，情绪峰值区")
    # 4) 发酵
    elif height_prev and height >= height_prev and height >= cfg["fajiao_height"] \
            and (promo["mid"] or 0) >= 0.4:
        stage = "发酵"
        reasons.append(f"高度 {height_prev}→{height}B(五板封住定龙头)，中位晋级率 {mid_txt}")
    # 5) 启动
    elif height_prev and height > height_prev:
        stage = "启动"
        reasons.append(f"高度回升 {height_prev}→{height}B，涨停 {zt} 只，梯队重建初期")
    # 6) 分歧
    else:
        stage = "分歧"
        reasons.append(f"高度 {height}B 持平/中断，中位晋级率 {mid_txt}，破板率 {broke}%")

    mainlines = board_mainlines(cur_rows)
    leaders = classify_leaders(cur_rows, mainlines)
    confidence = min(9, 4 + len(reasons) + (2 if promo["mid"] is not None else 0))

    res = {"date": date_str, "stage": stage, "confidence": confidence, "reasons": reasons,
           "playbook": STAGE_PLAYBOOK[stage],
           "metrics": {"height": height, "height_prev": height_prev, "zt": zt, "zt_ma5": zt_ma5,
                       "broke_rate": broke, "broke_ma5": broke_ma5, "promo": promo,
                       "ladder": ladder_counts, "h_trend": h_trend},
           "matrix": matrix, "mainlines": mainlines, "leaders": leaders}
    if persist:
        save(res)
    return res


def save(res: dict):
    ADB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ADB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS cycle_daily(
            date TEXT PRIMARY KEY, stage TEXT, confidence INTEGER,
            detail TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS leaders_daily(
            date TEXT, code TEXT, name TEXT, pid INTEGER, role TEXT, note TEXT,
            PRIMARY KEY(date, code, role))""")
        conn.execute("INSERT OR REPLACE INTO cycle_daily(date, stage, confidence, detail)"
                     " VALUES(?,?,?,?)",
                     (res["date"], res["stage"], res["confidence"],
                      json.dumps(res, ensure_ascii=False)))
        for l in res["leaders"]:
            conn.execute("INSERT OR REPLACE INTO leaders_daily(date, code, name, pid, role, note)"
                         " VALUES(?,?,?,?,?,?)",
                         (res["date"], l["code"], l["name"], l["pid"], l["role"], l["note"]))
