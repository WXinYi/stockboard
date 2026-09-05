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


def stage_pool(cycle_res: dict, max_n: int = 20, bid_date: str | None = None) -> list:
    """返回按阶段产生的候选池: [{code,name,height,reason,status}]
    bid_date: 竞价数据日期, 默认=周期日(历史回放); 盘前存档场景传当日(9:26 竞价+昨日池)。"""
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
            # 退潮期 cap=0 禁买(与 JS 端 STAGE_GATE 对齐), 补涨只在 分歧/发酵 可做
            st = "可做(补涨)" if stage in ("分歧", "发酵") else "观察"
        else:
            st = lead_status.get(stage, "观察")
        if "封单衰减" in note and st.startswith("可做"):
            st = "观察(封单衰减)"
        add(l["code"], l["name"], l["pid"],
            f"龙头谱系[{role}] {note}".strip(), st)

    if stage == "退潮":
        # 火种观察: 今日逆市连板≥2板(禁买期只看, 新周期候选载体; 与 JS 端退潮火种对齐)
        eod_rows = [r for r in load_pool(days=2) if r["date"] == date_str]
        for r in sorted(eod_rows, key=lambda x: -x["height"])[:4]:
            if r["height"] >= 2:
                add(r["code"], r["name"], r["height"],
                    f"逆市连板(火种观察) {r['height']}板", "观察(火种)")
        return _apply_matrix(pool, cycle_res)[:max_n]

    # 2) 阶段扩展候选
    pool_rows = load_pool(days=10)
    dates = sorted({r["date"] for r in pool_rows})
    cur_rows = [r for r in pool_rows if r["date"] == date_str]
    prev_rows = [r for r in pool_rows if r["date"] == dates[dates.index(date_str) - 1]] \
        if date_str in dates and dates.index(date_str) > 0 else []
    prev_first = {r["code"]: r for r in prev_rows if r["height"] == 1}
    bids = _bid_pool(bid_date or date_str)
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
        if stage == "启动":
            # 首板试错: 今日主线首板 + 竞价强(JS 端 leaderBattle.computeStrike 用 早封+主力净买 过滤, 口径互补)
            for r in cur_rows:
                in_main = any(m["board"] in str(r["plates"] or "") for m in cycle_res["mainlines"])
                if in_main and r["height"] == 1 and bid_strong(r["code"]):
                    b = bids[r["code"]]
                    add(r["code"], r["name"], 1,
                        f"首板试错: 主线首板, 今竞价 {b['change_pct']:+.1f}%", "可做(首板试错)")
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

    # 3) 弱转强(陈小群): 昨日分歧(断板∪尾盘烂板) + 今日竞价超预期(+1.5~7%) → 必要条件,
    #    分时确认才上, 失败止损。烂板=收盘封单/盘中最高封单<0.15(当日最弱档, 绝对阈值不可用——
    #    封单全天被消化是常态, 09-01 实测分布校准)。
    if stage in ("启动", "发酵", "分歧") and date_str in dates and dates.index(date_str) >= 1:
        _i = dates.index(date_str)
        prev_date2 = dates[_i - 1]
        prev2_codes = {r["code"] for r in pool_rows if r["date"] == dates[_i - 2]} if _i >= 2 else set()
        sealed_prev = {r["code"] for r in prev_rows}
        broken_prev = _broken_map(prev_date2)  # 昨日触板未封(精确炸板池, 选股宝归档)
        duanban = prev2_codes - sealed_prev  # 前日涨停、昨日未封 = 昨日断板
        rotten = {r["code"] for r in pool_rows if r["date"] == prev_date2
                  and r.get("max_seal") and r.get("seal_amount")
                  and r["seal_amount"] / r["max_seal"] < 0.15}
        names_d = {r["code"]: r["name"] for r in pool_rows}
        for code in list(duanban | rotten | set(broken_prev))[:80]:
            if code in seen or not bid_strong(code):
                continue
            tag = "断板" if code in duanban else ("炸板" if code in broken_prev else "烂板")
            add(code, names_d.get(code, code) or broken_prev.get(code, code), 0,
                f"弱转强: 昨日{tag}分歧, 今竞价 {bids[code]['change_pct']:+.1f}%, 分时确认才上",
                "可做(弱转强)")

    # 3) V5 容量方向(周期闸门): 仅 发酵/高潮 开, 且只留主线板块内
    if stage in V5_STAGES:
        v5 = _load_v5(date_str)
        for c in v5:
            if any(b in mainlines for b in c.get("boards", [])) and len(pool) < max_n + 8:
                add(c["code"], c["name"], 0,
                    f"V5容量方向({c.get('pos_tag') or '普通'}) [{'/'.join(c.get('boards', [])[:2])}]",
                    "观察(容量)")

    return _apply_matrix(_apply_shrink_filter(pool, cycle_res, cur_rows), cycle_res)[:max_n]


def _circ_mv_map(date_str):
    from pathlib import Path
    import sqlite3
    db = Path(__file__).resolve().parents[2] / "data" / "auction.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT code, circ_mv FROM limit_pool WHERE date=?", (date_str,))}


def _apply_shrink_filter(pool: list, cycle_res: dict, cur_rows: list) -> list:
    """缩量板降级(著名刺客: 换手连板优于缩量板——换手才检验真实承接):
    ≥2板 且 估算换手(amount/circ_mv)<3% → 观察(缩量板)。JS 端 leaderBattle 同规则(-12分)。"""
    mvs = _circ_mv_map(cycle_res["date"])
    amt = {r["code"]: (r["amount"] or 0) for r in cur_rows}
    for p in pool:
        if p["height"] >= 2 and p["status"].startswith("可做"):
            mv, a = mvs.get(p["code"]), amt.get(p["code"]) or 0
            if mv and a and 0 < a / mv * 100 < 3:
                p["status"] = "观察(缩量板)"
    return pool


def _broken_map(date_str):
    """昨日炸板 {code: name}(broken_pool 表, 选股宝 limit_up_broken 归档); 表不存在返回空 dict"""
    from pathlib import Path
    import sqlite3
    db = Path(__file__).resolve().parents[2] / "data" / "auction.db"
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT code, name FROM broken_pool WHERE date=?", (date_str,))}
    except sqlite3.Error:
        return {}


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
