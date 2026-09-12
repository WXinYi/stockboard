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
        net = f"主净{_yi(r['main_net']):+.2f}亿" if r["main_net"] else "主净0"
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
    avg = f"{sum(chgs)/len(chgs):+.2f}% ({len(chgs)}/{len(rows)}只)" if chgs else "-"
    st.append(f"  昨日({p})环境: 涨停{b['zt']} 跌停{b['dt']} 炸板率{b['broke_rate']:.0f}% 竞价情绪值{mp['strong']}")
    st.append(f"  今日({d})竞价快照: 情绪值{m['strong']}(昨{mp['strong']}) 竞价涨停{m['ztjs']}只 "
              f"连板高度{m['lbgd']}")
    st.append(f"  昨日涨停股今日竞价均值: {avg}")
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


def _save(cur, d, payload, raw=None):
    cur.execute("""CREATE TABLE IF NOT EXISTS llm_review (
        date TEXT NOT NULL, prompt_ver TEXT NOT NULL, regime TEXT, why TEXT,
        position TEXT, picks TEXT, avoid TEXT, model TEXT,
        degraded INTEGER DEFAULT 0, latency_s REAL, raw TEXT, created_at TEXT,
        PRIMARY KEY(date, prompt_ver))""")
    cur.execute("""INSERT OR REPLACE INTO llm_review
                   (date,prompt_ver,regime,why,position,picks,avoid,model,degraded,latency_s,raw,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (d, PROMPT_VER, payload.get("regime"), payload.get("why"),
                 payload.get("position_today"), json.dumps(payload.get("picks") or [], ensure_ascii=False),
                 payload.get("avoid"), payload.get("model"), 1 if payload.get("degraded") else 0,
                 payload.get("latency_s"), (raw or "")[:4000],
                 datetime.now().isoformat(timespec="seconds")))


def run(date=None, dry_run=False):
    """主入口: 返回给 auction.json 'llm' 字段的 dict; 无 key/失败返回 None(链路不受影响)。"""
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
        messages = build_messages(conn.cursor(), d)
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
            # 候选宇宙校验: 幻觉票剔除——code 必须出现在当日 bid_pool
            codes = {r["code"] for r in conn.execute("SELECT code FROM bid_pool WHERE date=?", (d,))}
            picks = [k for k in picks if isinstance(k, dict) and k.get("code") in codes]
            out = {"date": d, "regime": env.get("regime"), "why": env.get("why"),
                   "position_today": payload.get("position_today") or "",
                   "picks": picks, "avoid": payload.get("avoid") or "",
                   "model": model, "prompt_ver": PROMPT_VER,
                   "latency_s": latency, "degraded": False, "generated_at": now}
        else:
            # 兜底降级: 原文存档, 页面显示"今日无有效输出"
            out = {"date": d, "regime": None, "why": None, "position_today": None, "picks": [],
                   "avoid": None, "model": model, "prompt_ver": PROMPT_VER, "latency_s": latency,
                   "degraded": True, "raw_head": (txt or "")[:200], "generated_at": now}
            print("[llm] ⚠️ 两次输出均无法解析, 已降级存档(degraded)")
        if not dry_run:
            _save(conn.cursor(), d, out, raw=txt)
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
    ap = argparse.ArgumentParser(description="大模型竞价独立选股(影子)")
    ap.add_argument("--date", help="日期 YYYY-MM-DD(默认今天)")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写库")
    a = ap.parse_args()
    out = run(a.date, dry_run=a.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=1) if out else "(无输出)")
