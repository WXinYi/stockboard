#!/usr/bin/env python3
"""
阶段候选池: 由超短情绪周期决定"当下该看哪些票" (周期 → 战法模式 → 候选)

每个阶段对应文章战法里的选股模式:
  冰点  → 1进2 候选(昨日首板+今日竞价强势) + 逆市连板        (首板套利/新周期火种)
  启动  → 昨日低位晋级候选 + 主线首板                        (打首板/低点做龙头)
  发酵  → 主线梯队(2-5板)强者 + 龙头谱系 + V5容量方向(主线内) (上龙头/同梯队)
  高潮  → 龙头谱系(接力名单) + V5容量方向(主线内)            (只做龙头接力)
  分歧  → 龙头谱系(低吸观察)                                 (抱团龙头, 避中位)
  退潮  → 仅空间锚(观察)                                     (空仓纪律)

V5 竞价首枪作为"容量方向"只在 发酵/高潮 两个阶段开启(周期闸门), 且只保留主线板块内
的标的 —— 即 V5 的选股池由情绪周期产生与过滤。

高中位矩阵分层闸门(与 stockboard-app/src/utils/leaderBattle.js 的 MATRIX_GATE 镜像同步):
同一阶段下按 cycle_res["matrix"](高位|中位) 再分 low=1-2板 / mid=3-5板 / high=≥6板 三层
收紧状态: go=可做 care=可做(矩阵谨慎) watch=观察(矩阵) ban=观察(矩阵禁买); 只收紧不放松。
(JS 版另有评分上限 cap, Python 无评分, 仅状态语义对齐。)
"""
from src.analysis.emotion_cycle import load_pool, ladder_split

V5_STAGES = {"发酵", "高潮"}

MATRIX_GATE = {
    "强|强":     {"tier": {"high": "go",    "mid": "go",    "low": "go"}},
    "强|平衡":   {"tier": {"high": "go",    "mid": "care",  "low": "go"}},
    "强|弱":     {"tier": {"high": "care",  "mid": "ban",   "low": "go"}},
    "平衡|强":   {"tier": {"high": "care",  "mid": "go",    "low": "go"}},
    "平衡|平衡": {"tier": {"high": "watch", "mid": "care",  "low": "go"}},
    "平衡|弱":   {"tier": {"high": "ban",   "mid": "ban",   "low": "care"}},
    "弱|强":     {"tier": {"high": "watch", "mid": "go",    "low": "go"}},
    "弱|平衡":   {"tier": {"high": "ban",   "mid": "care",  "low": "care"}},
    "弱|弱":     {"tier": {"high": "ban",   "mid": "ban",   "low": "watch"}},
}

MATRIX_ACT = {
    "care": "可做(矩阵谨慎)",
    "watch": "观察(矩阵)",
    "ban": "观察(矩阵禁买)",
}


def _tier_of(height: int) -> str:
    return "low" if height <= 2 else "mid" if height <= 5 else "high"


def _apply_matrix(pool: list, cycle_res: dict) -> list:
    """矩阵分层闸门: 按 高位×中位 状态收紧各层候选状态(只收紧不放松)"""
    m = cycle_res.get("matrix") or {}
    gate = MATRIX_GATE.get(f'{m.get("high")}|{m.get("mid")}')
    if not gate:
        return pool
    for p in pool:
        act = gate["tier"][_tier_of(p["height"])]
        if act != "go" and p["status"].startswith("可做"):
            p["status"] = MATRIX_ACT[act]
    return pool


def _bid_pool(date_str):
    from pathlib import Path
    import sqlite3
    db = Path(__file__).resolve().parents[2] / "data" / "auction.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return {r["code"]: dict(r) for r in conn.execute(
            "SELECT code, name, change_pct, main_net, tag, plates FROM bid_pool WHERE date=?",
            (date_str,))}


def stage_pool(cycle_res: dict, max_n: int = 20) -> list:
    """返回按阶段产生的候选池: [{code,name,height,reason,status}]"""
    stage = cycle_res["stage"]
    date_str = cycle_res["date"]
    pool: list[dict] = []
    seen: set[str] = set()

    def add(code, name, height, reason, status):
        if code in seen:
            for p in pool:
                if p["code"] == code and reason not in p["reason"]:
                    p["reason"] += f"；{reason}"
            return
        seen.add(code)
        pool.append({"code": code, "name": name, "height": height,
                     "reason": reason, "status": status})

    # 1) 龙头谱系: 任何阶段都盯(状态由阶段×角色定)
    lead_status = {"高潮": "可做(接力)", "发酵": "可做", "分歧": "可做(低吸)",
                   "退潮": "观察(只看最强)", "冰点": "观察", "启动": "观察"}
    for l in cycle_res["leaders"]:
        role = l["role"]
        note = l["note"] or ""
        if "中军" in role:
            st = "观察(容量)"
        elif "补涨" in role:
            st = "可做(补涨)" if stage in ("分歧", "退潮", "发酵") else "观察"
        else:
            st = lead_status.get(stage, "观察")
        if "封单衰减" in note and st.startswith("可做"):
            st = "观察(封单衰减)"
        add(l["code"], l["name"], l["pid"],
            f"龙头谱系[{role}] {note}".strip(), st)

    if stage == "退潮":
        return _apply_matrix(pool, cycle_res)[:max_n]

    # 2) 阶段扩展候选
    pool_rows = load_pool(days=10)
    dates = sorted({r["date"] for r in pool_rows})
    cur_rows = [r for r in pool_rows if r["date"] == date_str]
    prev_rows = [r for r in pool_rows if r["date"] == dates[dates.index(date_str) - 1]] \
        if date_str in dates and dates.index(date_str) > 0 else []
    prev_first = {r["code"]: r for r in prev_rows if r["height"] == 1}
    bids = _bid_pool(date_str)
    mainlines = {m["board"] for m in cycle_res["mainlines"]}

    def bid_strong(code):
        b = bids.get(code)
        return b and b["change_pct"] is not None and 1.5 <= b["change_pct"] <= 7

    if stage in ("冰点", "启动"):
        # 1进2 候选: 昨日首板 + 今日竞价强势
        for code, r in list(prev_first.items())[:40]:
            if bid_strong(code):
                b = bids[code]
                add(code, r["name"], 2, f"1进2: 昨日首板, 今竞价 {b['change_pct']:+.1f}%",
                    "可做(首板套利)")
        if stage == "冰点":
            # 逆市连板: 今日池内高度≥2 (冰点日还能连板的是新周期火种)
            for r in cur_rows:
                if r["height"] >= 2:
                    add(r["code"], r["name"], r["height"],
                        f"逆市连板(冰点火种) {r['height']}板", "观察(新周期留意)")
    elif stage == "发酵":
        # 主线梯队强者: 今日池内 主线板块 2-5板
        for r in cur_rows:
            in_main = any(m["board"] in str(r["plates"] or "") for m in cycle_res["mainlines"])
            if in_main and 2 <= r["height"] <= 5:
                add(r["code"], r["name"], r["height"],
                    f"主线梯队 {r['height']}板", "可做")
    elif stage == "高潮":
        # 接力对象: 主线内最高梯队(龙头谱系之外的次高)
        for r in sorted(cur_rows, key=lambda x: -x["height"])[:6]:
            in_main = any(m["board"] in str(r["plates"] or "") for m in cycle_res["mainlines"])
            if in_main and r["height"] >= 3:
                add(r["code"], r["name"], r["height"], "主线高位(秒板接力对象)", "观察(谨慎接力)")

    # 3) V5 容量方向(周期闸门): 仅 发酵/高潮 开, 且只留主线板块内
    if stage in V5_STAGES:
        v5 = _load_v5(date_str)
        for c in v5:
            if any(b in mainlines for b in c.get("boards", [])) and len(pool) < max_n + 8:
                add(c["code"], c["name"], 0,
                    f"V5容量方向({c.get('pos_tag') or '普通'}) [{'/'.join(c.get('boards', [])[:2])}]",
                    "观察(容量)")

    return _apply_matrix(pool, cycle_res)[:max_n]


def _load_v5(date_str):
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parents[3] / "stockboard-app" / "public" / "data" / "latest" / "auction.json"
    if not f.exists():
        return []
    j = json.loads(f.read_text())
    if j.get("date") != date_str:
        return []
    return [v for v in (j.get("v5") or []) if v.get("group_tag") == "v5"]
