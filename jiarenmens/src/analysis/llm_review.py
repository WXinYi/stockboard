#!/usr/bin/env python3
"""大模型竞价独立选股(影子) —— 每日 09:25 由 auction_scan 调用, 双写 auction.db.llm_review + auction.json.llm。

与规则引擎完全隔离: 只喂"当日竞价 + 前一日数据"(涨停池/炸板池/宽度/情绪/竞价额/红盘),
不喂引擎的阶段标签/闸门/出击名单/状态词/六情绪结论词。输出允许空仓(picks=[]), 观察/空仓日强制空。

健壮性四层: ①输出压缩(reason≤60字/picks≤3) ②括号配对提取JSON(容忍围栏/尾串/数字后杂引号)
③解析失败自动重试1次 ④两次都失败则 degraded 落档(原始文本保存), 不阻塞竞价链路。

用法:
  python -m src.analysis.llm_review                 # 今天(需 DEEPSEEK_API_KEY)
  python -m src.analysis.llm_review --date 2026-09-11
  python -m src.analysis.llm_review --dry-run       # 只打印, 不写库不写json
环境变量: DEEPSEEK_API_KEY(必) / LLM_MODEL(默认deepseek-flash) / LLM_BASE_URL(默认api.deepseek.com)
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROMPT_VER = "v2"
MODEL_DEFAULT = "deepseek-flash"
API_BASE_DEFAULT = "https://api.deepseek.com"
DB = Path(__file__).resolve().parents[2] / "data" / "auction.db"
FOCUS = "本周只修一个毛病：当日收跌股禁买"

SYSTEM = """你是一位精通A股超短线情绪周期的游资操盘手，正在为一个管理模拟组合的职业超短选手做 09:25 竞价决策。
你每天看到的只有原始数据。你的第一职责不是选股，而是判断"今天该不该出手"——在情绪下行段，空仓就是最好的交易，选股不是每天的必修课。

## 决策流程（强制按顺序执行）
第一步【定环境】用四个信号判定今日 regime：
  a. 竞价情绪值环比（升/平/降及幅度） b. 红盘占比（≥45% 健康 / 25~45% 弱 / <25% 极弱）
  c. 竞价总额方向（注意：放量但红盘<25% = 抽血不是增量） d. 高标承接（最高板竞价是否还有大额主净）
  → 攻击日=情绪回升+红盘≥45%+额不缩+高标有承接；试错日=情绪止跌企稳或修复初期；
    观察日=情绪续降或红盘<40%或额缩；空仓日=情绪续降+红盘<25%+高标竞价无承接。
第二步【闸门】观察日原则上不出手（除非出现平开/低开+大额主净的明确修复形态）；空仓日无论个股信号多好，picks 必须为空。
第三步【选股铁律】（仅攻击/试错日适用）：
  1. 高开>5% 的票一律不做——高开越多兑现盘越重；主净为正只是必要条件，绝不构成安全证明（竞价大单可反手砸出）
  2. 只做两类形态：a)平开/低开(<3%)且主净大额为正的修复低吸 b)温和高开(+1.5~4%)且主线梯队内、封单厚、非最高板
  3. 弱转强（昨炸板/断板+今竞价+1.5~7%+主净为正）只在攻击/试错日有效；观察/空仓日的弱转强=诱多概率大，一律不做
  4. 一字/涨停开盘=买不到，只作风向标
  5. 缩量板（≥2板且昨日成交额/流通<3%）不接
  6. 反核（总龙头深水≤-5%）≤0.3成且必须开盘承接确认
  7. 出手仓位匹配环境：攻击5-6成/试错2成/观察0-1成/空仓0

## 输出规则
只输出一个JSON对象（不要markdown、不要解释）：
{"environment":{"regime":"攻击|试错|观察|空仓","why":"引用四信号具体数字"},
 "picks":[{"code":"","name":"","action":"","rank":1,"reason":"≤60字引用具体数字","entry":"","stop":"","pos":""}],
 "avoid":"今日不碰什么、为什么","position_today":""}
- picks 仅当 regime=攻击/试错 才允许非空（最多3只）；观察/空仓日必须是空数组 []
- code 必须来自候选宇宙；reason 必须引用输入数据的具体数字，禁止"感觉/预计"
示例（某退潮日的满分输出）：
{"environment":{"regime":"空仓","why":"情绪值25→20续降,红盘19%,竞价额缩15%,5板高标+8.8%主净0"},"picks":[],"avoid":"所有高开票无论主净多少均属兑现盘中;一字板买不到;弱转强在此环境为诱多","position_today":"0成,等情绪止跌+高标承接恢复再评估"}"""

RETRY_HINT = "你的上一次输出无法解析为完整JSON。只重新输出JSON本体：不要markdown围栏、不要解释文字、每条reason不超过60字。"


# ────────────────────────── 数据拼装(只读前一日+当日竞价) ──────────────────────────

def _prev_day(cur, d):
    r = cur.execute("SELECT MAX(date) FROM limit_pool WHERE date<?", (d,)).fetchone()
    return r[0] if r and r[0] else None


def _yi(v):
    return (v or 0) / 1e8


def build_universe(cur, d):
    """09:25 可见的原始数据文本(不含任何引擎结论)。"""
    p = _prev_day(cur, d)
    rows = cur.execute(
        """SELECT p.code,p.name,p.pid_type,p.seal_amount,p.plates,p.circ_mv,p.amount,
                  b.change_pct,b.main_net
           FROM limit_pool p LEFT JOIN bid_pool b ON b.code=p.code AND b.date=?
           WHERE p.date=? ORDER BY p.pid_type DESC, p.seal_amount DESC""", (d, p)).fetchall()
    codes = {r["code"] for r in rows}

    def line(r, tag=""):
        chg = f"竞价{r['change_pct']:+.1f}%" if r["change_pct"] is not None else "竞价-"
        # 主净缺失(None=无数据)必须显示 '-', 不得伪装成 0 —— 09-22 对账实锤模型会把
        # "无数据"当"净流入为零"参与推理(与 09-15 静默治理"缺失≠零"同族)
        net = f"主净{(r['main_net'] or 0)/1e8:+.2f}亿" if r["main_net"] else "主净-"
        mv = f"流通{_yi(r['circ_mv']):.0f}亿" if r["circ_mv"] else ""
        amt = f"昨额{_yi(r['amount']):.1f}亿" if r["amount"] else ""
        return (f"{r['pid_type']}板 {r['name']}({r['code']})[{r['plates']}] "
                f"封单{_yi(r['seal_amount']):.2f}亿 {mv} {amt} {chg} {net} {tag}").replace("  ", " ").strip()

    st = [f"  昨日({p})涨停 {len(rows)} 家:", *[("  " + line(r)) for r in rows]]

    brk = cur.execute("SELECT code,name FROM broken_pool WHERE date=?", (p,)).fetchall()
    if brk:
        bset = {r["code"]: r["name"] for r in brk}
        brk_rows = cur.execute(
            f"SELECT code,change_pct,main_net FROM bid_pool WHERE date=? AND code IN ({','.join('?'*len(bset))})",
            (d, *bset)).fetchall()
        st.append(f"  昨日炸板 {len(brk)} 家(今日若回封=弱转强候选, 但注意纪律3的环境限制):")
        st += [f"  {bset[r['code']]}({r['code']}) 竞价{r['change_pct']:+.1f}% 主净{_yi(r['main_net']):+.2f}亿 〔昨炸板〕"
               if r["change_pct"] is not None else f"  {bset[r['code']]}({r['code']}) 竞价- 〔昨炸板〕"
               for r in brk_rows]

    movers = cur.execute("""SELECT code,name,change_pct,main_net FROM bid_pool WHERE date=?
                            ORDER BY change_pct DESC LIMIT 10""", (d,)).fetchall()
    extra = [m for m in movers if m["code"] not in codes]
    if extra:
        st.append("  今日竞价全场涨幅榜(非昨日涨停新面孔):")
        st += [f"  {m['name']}({m['code']}) 竞价{m['change_pct']:+.1f}% 主净{_yi(m['main_net']):+.2f}亿" for m in extra]

    b = cur.execute("SELECT zt,dt,broke_rate FROM market_breadth WHERE date=?", (p,)).fetchone()
    m = cur.execute("SELECT ztjs,strong,lbgd FROM mood_daily WHERE date=?", (d,)).fetchone()
    mp = cur.execute("SELECT ztjs,strong FROM mood_daily WHERE date=?", (p,)).fetchone()
    chgs = [r["change_pct"] for r in rows if r["change_pct"] is not None]
    avg = f"{sum(chgs)/len(chgs):+.2f}%" if chgs else "-"
    red = sum(1 for x in chgs if x > 0)
    # 09-22 对账实锤: 旧写法 "(60/103只)" 是"有竞价数据数/总数", 模型误读成"红盘60/103"
    # 而推出 58%≥45% 的虚假确认 —— 红绿计数显式给出, 不留可误读的分子分母
    st.append(f"  昨日({p})环境: 涨停{b['zt']} 跌停{b['dt']} 炸板率{b['broke_rate']:.0f}% 竞价情绪值{mp['strong']}")
    st.append(f"  今日({d})竞价快照: 情绪值{m['strong']}(昨{mp['strong']}) 竞价涨停{m['ztjs']}只 "
              f"连板高度{m['lbgd']}")
    st.append(f"  昨日涨停股今日竞价均值: {avg}"
              f"（有竞价数据 {len(chgs)}/{len(rows)} 只：红盘 {red}、绿盘 {len(chgs)-red}；"
              f"无竞价数据(一字/停牌等) {len(rows)-len(chgs)} 只未计入均值）")
    return "\n".join(st)


def build_messages(cur, d):
    user = f"""【今日时点】{d} 09:25 集合竞价 | 选手纪律: {FOCUS}
【候选宇宙 · 昨日涨停 × 今日竞价】
{build_universe(cur, d)}

【任务】按系统指令的两级决策流程:
1. environment: 用四信号判定今日 regime(攻击/试错/观察/空仓), why 引用具体数字
2. picks: 仅 regime=攻击/试错 时输出 0-3 只(可以为空); 观察/空仓日必须为空数组
3. avoid: 今日不碰什么、为什么
4. position_today: 总仓位建议

【输出JSON格式】
{{"environment":{{"regime":"","why":""}},
 "picks":[{{"code":"","name":"","action":"","rank":1,"reason":"","entry":"","stop":"","pos":""}}],
 "avoid":"","position_today":""}}"""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ────────────────────────── 10:00 盘中实况喂入(v3, 2026-09-22) ──────────────────────────
# 动机(09-22 用户拍板): 09:25 竞价时点存在结构性盲区 —— 当日涨跌停/晋级情况/涨停溢价
# 均未产生, LLM 只能拿昨日存量+竞价代理值拼定性, 实测出现"涨停105→103持平"式硬凑
# (两个不同口径的昨日数字被当成今昨对比; 当日真实收盘 64 家, 大退潮)。
# 移到 10:00 后改喂实时因子: 实时涨跌停/炸板、晋级率、昨日涨停股实时溢价(分首板/连板)、
# 梯队结构、指数量能。引擎 09:25 出击链路不受影响(本模块只在 llm-1000 班被调用)。

LIVE_PROMPT_VER = "v3"
KPL_POST_URL = "https://apphwhq.longhuvip.com/w1/api/index.php?"
KPL_DEVICE = "d66474b3-fd78-3a95-a56d-76e29e765ea3"


def _kpl_get(params: dict):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(KPL_POST_URL + q, headers={"User-Agent": "Dalvik/2.1.0"})
    for _ in range(2):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=20).read())
        except Exception:
            time.sleep(1)
    return None


def _qt_snap(syms):
    """腾讯实时快照: {'sh000001': {price, pct, amount_yi}}; 指数与个股同一接口。"""
    out = {}
    for i in range(0, len(syms), 60):
        req = urllib.request.Request(
            f"https://qt.gtimg.cn/q={','.join(syms[i:i+60])}",
            headers={"User-Agent": "Mozilla/5.0"})
        try:
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", "ignore")
        except Exception:
            continue
        for m in re.finditer(r'v_(\w+)="([^"]*)"', raw):
            f = m.group(2).split("~")
            try:
                if len(f) > 37 and f[3]:
                    price, prev = float(f[3]), float(f[4] or 0)
                    out[m.group(1)] = {"price": price,
                                       "pct": (price - prev) / prev * 100 if prev > 0 else None,
                                       "amount_yi": float(f[37] or 0) / 1e4}
            except ValueError:
                continue
    return out


def _sym_of(code):
    return ("sh" if code[0] in "65" else "bj" if code[0] in "48" else "sz") + code


def _flat_rows(info):
    out = []
    for x in (info or []):
        if isinstance(x, list):
            if x and isinstance(x[0], list):
                out.extend(y for y in x if isinstance(y, list))
            else:
                out.append(x)
    return out


def build_universe_live(cur, d):
    """10:00 实时快照喂入(v3): 返回 (universe_text, meta)。
    meta = {codes: 候选宇宙(幻觉校验), promoted, prev_count, rt_count}。"""
    p = _prev_day(cur, d)
    prev = cur.execute(
        """SELECT code,name,pid_type,seal_amount,plates,circ_mv,amount
           FROM limit_pool WHERE date=? ORDER BY pid_type DESC, seal_amount DESC""", (p,)).fetchall()
    prev_codes = {r["code"] for r in prev}

    rt_codes, tier = set(), {}
    for pid in (1, 2, 3, 4, 5):
        j = _kpl_get({"a": "DailyLimitPerformance", "c": "HomeDingPan", "PhoneOSNew": 1,
                      "DeviceID": KPL_DEVICE, "PidType": pid, "Type": 4, "Index": 0,
                      "Order": 0, "st": 500, "apiv": "w39"})
        n = 0
        for x in _flat_rows(j.get("info") if isinstance(j, dict) else None):
            if len(x) >= 2:
                rt_codes.add(str(x[0]))
                n += 1
        if n:
            tier[pid] = n

    rise = _kpl_get({"a": "RiseFallAnalysis", "apiv": "w43", "c": "HomeDingPan", "PhoneOSNew": 1})
    rt_row = next((x for x in _flat_rows(rise.get("info") if isinstance(rise, dict) else None)
                   if len(x) >= 7 and str(x[6]) == d), None) if isinstance(rise, dict) else None
    yb = cur.execute("SELECT zt,dt,broke_rate,zhaban FROM market_breadth WHERE date=?", (p,)).fetchone()

    st = [f"【数据边界】以下为 {d} 10:00 实时快照: 当日全天涨跌停/晋级率/涨停溢价收盘值尚未产生, "
          f"禁止推测全天数据; 判断只基于已发生的盘中事实。"]
    if rt_row:
        st.append(f"一、今日实时({d} 10:00): 涨停{rt_row[0]}(昨{yb['zt']}) 跌停{rt_row[1]}(昨{yb['dt']}) "
                  f"炸板{rt_row[5]}只(昨{yb['zhaban']}只) 破板率{rt_row[4]:.0f}%(昨{yb['broke_rate']:.0f}%)")
    promoted = prev_codes & rt_codes
    if prev:
        st.append(f"二、晋级: 昨日涨停 {len(prev)} 只 → 今日再涨停 {len(promoted)} 只"
                  f"(晋级率 {len(promoted)/len(prev)*100:.0f}%)")
    if tier:
        st.append("三、梯队: " + " ".join(f"{k}板{v}只" for k, v in sorted(tier.items())))

    snaps = _qt_snap([_sym_of(c) for c in sorted(prev_codes)])
    pres = [(_sym_of(r["code"]), r) for r in prev]
    prem_all, prem_first, prem_lian = [], [], []
    red = green = noq = 0
    for sym, r in pres:
        q = snaps.get(sym)
        if not q or q["pct"] is None:
            noq += 1
            continue
        prem_all.append(q["pct"])
        (prem_first if r["pid_type"] == 1 else prem_lian).append(q["pct"])
        if q["pct"] > 0:
            red += 1
        else:
            green += 1
    def _avg(xs):
        return f"{sum(xs)/len(xs):+.2f}%" if xs else "-"
    st.append(f"四、昨日涨停股实时表现(赚钱效应核心): 红{red} 绿{green} 无实时行情{noq}(共{len(prev)})"
              f" | 实时溢价均值 {_avg(prem_all)}"
              f"（首板 {_avg(prem_first)} / 连板 {_avg(prem_lian)}）")
    for sym, r in pres:
        q = snaps.get(_sym_of(r["code"]))
        if q and q.get("pct") is not None:
            st.append(f"  {r['pid_type']}板 {r['name']}({r['code']})[{r['plates']}] "
                      f"昨封单{_yi(r['seal_amount']):.2f}亿 昨额{_yi(r['amount']):.1f}亿 "
                      f"现价{q['price']:.2f} 实时{q['pct']:+.1f}%")
    ids = _qt_snap(["sh000001", "sz399001"])
    if ids:
        sh, szv = ids.get("sh000001", {}), ids.get("sz399001", {})
        amt = sh.get("amount_yi", 0) + szv.get("amount_yi", 0)
        st.append(f"五、指数量能: 上证{sh.get('pct', 0):+.2f}% 深成指{szv.get('pct', 0):+.2f}% "
                  f"两市成交额(截至10:00) {amt:.0f}亿（量能对比因子待 v3.1）")
    st.append("六、候选宇宙 = 今日实时涨停 ∪ 昨日涨停(明细见上); picks 的 code 必须出自其中。")
    meta = {"codes": rt_codes | prev_codes, "promoted": len(promoted),
            "prev_count": len(prev), "rt_count": len(rt_codes)}
    return "\n".join(st), meta


def build_messages_live(cur, d, universe_text):
    user = f"""【今日时点】{d} 10:00 盘中(开盘后30分钟) | 选手纪律: {FOCUS}
【候选宇宙 · 实时盘面 + 昨日涨停池(附实时溢价)】
{universe_text}

【任务】按系统指令的两级决策流程:
1. environment: 用盘中实况判定今日 regime(攻击/试错/观察/空仓), why 引用具体数字
2. picks: 仅 regime=攻击/试错 时输出 0-3 只(可以为空); 观察/空仓日必须为空数组
3. avoid: 今日不碰什么、为什么
4. position_today: 总仓位建议

【输出JSON格式】
{{"environment":{{"regime":"","why":""}},
 "picks":[{{"code":"","name":"","action":"","rank":1,"reason":"","entry":"","stop":"","pos":""}}],
 "avoid":"","position_today":""}}"""
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ────────────────────────── 健壮解析 ──────────────────────────

def _repair_quotes(t):
    t = re.sub(r'(:\s*\d+(?:\.\d+)?)"(?=\s*[,}\]])', r"\1", t)   # 数字后杂引号
    t = re.sub(r",\s*([}\]])", r"\1", t)                          # 尾逗号
    return t


def extract_json(txt):
    """提取首个可解析的 JSON 对象; 失败返回 None。
    双模式: ①字符串感知配对(正确处理串内花括号) ②纯深度配对(容忍杂引号破坏字符串态)。
    截断输出若已含可解析的前半对象, 返回该部分对象(由调用方补默认键)。"""
    t = (txt or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-z]*\s*", "", t)
        t = t.rsplit("```", 1)[0]
    first = t.find("{")
    if first < 0:
        return None
    for mode in ("string", "plain"):
        depth = 0
        in_str = esc = False
        for i in range(first, len(t)):
            ch = t[i]
            if mode == "string":
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    cand = t[first:i + 1]
                    try:
                        return json.loads(cand)
                    except Exception:
                        try:
                            return json.loads(_repair_quotes(cand))
                        except Exception:
                            break  # 该模式首个候选失败 → 换下一模式
    return None


# ────────────────────────── 调用与落档 ──────────────────────────

def call_llm(messages, model, base, key, timeout=90):
    body = json.dumps({"model": model, "messages": messages, "temperature": 0,
                       "response_format": {"type": "json_object"}, "max_tokens": 16000}).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    msg = resp["choices"][0]["message"]
    return (msg.get("content") or "", msg.get("reasoning_content") or "",
            round(time.time() - t0, 1), resp.get("usage", {}))


def _save(cur, d, payload, raw=None, prompt_ver=None):
    cur.execute("""CREATE TABLE IF NOT EXISTS llm_review (
        date TEXT NOT NULL, prompt_ver TEXT NOT NULL, regime TEXT, why TEXT,
        position TEXT, picks TEXT, avoid TEXT, model TEXT,
        degraded INTEGER DEFAULT 0, latency_s REAL, raw TEXT, created_at TEXT,
        PRIMARY KEY(date, prompt_ver))""")
    cur.execute("""INSERT OR REPLACE INTO llm_review
                   (date,prompt_ver,regime,why,position,picks,avoid,model,degraded,latency_s,raw,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d, prompt_ver or payload.get("prompt_ver") or PROMPT_VER, payload.get("regime"),
                 payload.get("why"),
                 payload.get("position_today"), json.dumps(payload.get("picks") or [], ensure_ascii=False),
                 payload.get("avoid"), payload.get("model"), 1 if payload.get("degraded") else 0,
                 payload.get("latency_s"), (raw or "")[:4000],
                 datetime.now().isoformat(timespec="seconds")))


def run(date=None, dry_run=False, live=False):
    """主入口: 返回给 auction.json 'llm' 字段的 dict; 无 key/失败返回 None(链路不受影响)。
    live=True(2026-09-22): 10:00 盘中班 —— 拉实时因子拼 v3 喂入, picks 记录信号时点价
    (entry_px), 幻觉校验宇宙 = 今日实时涨停 ∪ 昨日涨停。"""
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY")
    if not key:
        print("[llm] 未配置 DEEPSEEK_API_KEY, 跳过大模型选股(影子)")
        return None
    model = os.environ.get("LLM_MODEL", MODEL_DEFAULT)
    base = os.environ.get("LLM_BASE_URL", API_BASE_DEFAULT).rstrip("/")
    d = date or datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        if live:
            universe_text, meta = build_universe_live(conn.cursor(), d)
            messages = build_messages_live(conn.cursor(), d, universe_text)
            prompt_ver = LIVE_PROMPT_VER
            print(f"[llm] 实时喂入(v3): 候选宇宙 {len(meta['codes'])} 只 | "
                  f"昨日涨停 {meta['prev_count']} → 今日晋级 {meta['promoted']} | "
                  f"今日实时涨停 {meta['rt_count']}")
        else:
            messages = build_messages(conn.cursor(), d)
            prompt_ver = PROMPT_VER
        txt, reasoning, latency, usage = call_llm(messages, model, base, key)
        payload = extract_json(txt)
        if payload is None or not isinstance(payload.get("environment"), dict):
            print("[llm] 第一次输出无法解析, 带提示重试…")
            messages.append({"role": "assistant", "content": (txt or "")[:500]})
            messages.append({"role": "user", "content": RETRY_HINT})
            txt2, _, latency2, _ = call_llm(messages, model, base, key)
            payload = extract_json(txt2)
            txt, latency = txt2 or txt, latency2
        now = datetime.now().isoformat(timespec="seconds")
        env = (payload or {}).get("environment") if isinstance(payload, dict) else None
        if isinstance(env, dict) and env.get("regime"):
            picks = payload.get("picks") or []
            if live:
                # 幻觉校验(实时宇宙) + 信号时点入场价记录(回测金标准, 10:00 实时价)
                codes = meta["codes"]
                snaps = _qt_snap([_sym_of(k.get("code")) for k in picks if isinstance(k, dict) and k.get("code")])
                kept = []
                for k in picks:
                    if not (isinstance(k, dict) and k.get("code") in codes):
                        continue
                    q = snaps.get(_sym_of(k["code"]))
                    if q:
                        k["entry_px"] = q["price"]
                        k["entry_time"] = "10:00"
                    kept.append(k)
                picks = kept
            else:
                # 候选宇宙校验: 幻觉票剔除——code 必须出现在当日 bid_pool
                codes = {r["code"] for r in conn.execute("SELECT code FROM bid_pool WHERE date=?", (d,))}
                picks = [k for k in picks if isinstance(k, dict) and k.get("code") in codes]
            out = {"date": d, "regime": env.get("regime"), "why": env.get("why"),
                   "position_today": payload.get("position_today") or "",
                   "picks": picks, "avoid": payload.get("avoid") or "",
                   "model": model, "prompt_ver": prompt_ver,
                   "latency_s": latency, "degraded": False, "generated_at": now}
            if live:
                out["slot"] = "1000"
        else:
            # 兜底降级: 原文存档, 页面显示"今日无有效输出"
            out = {"date": d, "regime": None, "why": None, "position_today": None, "picks": [],
                   "avoid": None, "model": model, "prompt_ver": prompt_ver, "latency_s": latency,
                   "degraded": True, "raw_head": (txt or "")[:200], "generated_at": now}
            if live:
                out["slot"] = "1000"
            print("[llm] ⚠️ 两次输出均无法解析, 已降级存档(degraded)")
        if not dry_run:
            _save(conn.cursor(), d, out, raw=txt, prompt_ver=prompt_ver)
            conn.commit()
        print(f"[llm] ✅ {d} regime={out.get('regime')} picks={len(out.get('picks') or [])} "
              f"degraded={out.get('degraded')} {out.get('latency_s')}s")
        return out
    except Exception as e:
        print(f"[llm] ❌ 失败(不影响竞价链路): {e}")
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="大模型独立选股(09:25 竞价版 v2 回放 / 10:00 盘中版 v3)")
    ap.add_argument("--date", help="日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写库")
    ap.add_argument("--live", action="store_true", help="10:00 盘中实况模式(v3: 拉实时因子)")
    ap.add_argument("--out", help="payload JSON 写入路径(供 llm-1000 workflow 更新 auction.json)")
    a = ap.parse_args()
    result = run(a.date, dry_run=a.dry_run, live=a.live)
    if a.out and result:
        Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[llm] payload 已写入 {a.out}")
    print(json.dumps(result, ensure_ascii=False, indent=1) if result else "(无输出)")
