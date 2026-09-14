#!/usr/bin/env python3
"""对账巡检: 生产选股页(#/market) 6 模块 vs 原始接口现场重算 (2026-09-14 事故后巡检)

铁律:
  1. 不读本项目产出的任何 JSON/数据库(auction.json/strike_review.json/lianban_bid.json/
     core.json/auction.db/crawl_data.db) —— 对账基准 100% 来自现场请求的原始接口;
     生产实际展示值来自无头浏览器读取的 DOM 渲染文本。
  2. 规则同源, 不重新发明:
     - ①②③(live 模块): Node 直接跑仓库里生产前端真引擎(emotionCycle.js/leaderBattle.js/
       stockPicks.js), 输入为现场抓的 KPL 原始数据(行映射抄 useKplApi.js) —— 与页面同代码同口径;
       live 模块盘中分钟级漂移 → 失配时重抓重算一次再判(时点差标 ⚠️ 不算 ❌)。
     - ④(09:25盘前候选=bidrank): 口径= auction_scan.rank_lianban_bid + lianban_bid_hs.py
       (昨日涨停池 pid>=2 ∩ 今日竞价实际换手; 换手 KPL 竞价池优先/0值腾讯 0930 补算)。
     - ⑤(弱转强): 口径= stage_candidates 弱转强块 / _synthesize_wzq(断板∪烂板∪炸板 ∩ 竞价+1.5~7%)。
       两条路径 2026-09-14 修复后同锚"竞价日前一交易日"; 修复前 stage_pool 曾锚"周期日前一日"
       (9:26 场景=前两交易日, off-by-one, 每晨名单恒旧一天) —— 页面名单若仍命中旧锚 = 生产端未部署修复。
     - ⑥(大模型选股): 只核管线健康(DOM 卡片状态 + GitHub Actions auction.yml 当日 run), 不复现内容。
  3. 接口挂了不算对账失败: 单模块数据源请求失败标 ⏭ 跳过(源不可用), 不伪装成 ❌。

判定: ✅一致(含容差) / ⚠️可解释差异(注明原因, 时点差不是错误) / ❌真出入(同时点口径下对不上)。
有 ❌ → 钉钉 webhook(DINGTALK_URL/DINGTALK_SECRET)告警; 无 ❌ 静默。报告另存 /tmp/reconcile_report.txt。

用法(在 jiarenmens/ 目录):
  venv/bin/python scripts/data_reconcile.py                 # 默认今天
  venv/bin/python scripts/data_reconcile.py --date 2026-09-14
  venv/bin/python scripts/data_reconcile.py --no-browser    # 只重算不抓 DOM(调试)
环境: KPL 直连(NO_PROXY 已内置 longhuvip/gtimg/xuangubao); node>=18 跑前端真引擎; playwright 抓 DOM。
定位: 本地巡检工具(09-14 拍板: 不进生产 CI, 仅手动跑)。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]          # jiarenmens/
APP = ROOT.parent / "stockboard-app"                # 前端(真引擎所在)
sys.path.insert(0, str(ROOT))

# KPL/腾讯/选股宝 直连(必须在 import requests 系模块前生效)
_DIRECT = ("longhuvip.com,.longhuvip.com,apphwhq.longhuvip.com,apphis.longhuvip.com,"
           "apphq.longhuvip.com,applhb.longhuvip.com,apppage.longhuvip.com,"
           "xuangubao.com.cn,.xuangubao.com.cn,flash-api.xuangubao.com.cn,"
           "gtimg.cn,.gtimg.cn,ifzq.gtimg.cn,web.ifzq.gtimg.cn,qt.gtimg.cn")
os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(
    p for p in (_DIRECT, os.environ.get("NO_PROXY", "")) if p)

import requests  # noqa: E402
from src.spiders.xuangubao import fetch_broken_pool  # noqa: E402

BJ_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(BJ_TZ)
PROD_URL = "https://wxinyi.github.io/stockboard/#/market"
UA = "okhttp/3.12.1"          # 与 SCF 中转同款(KPL 对浏览器 UA 返回空)
DEV_ID = "6CC28E90-0785-4B21-8EEF-557159D26CF1"   # 前端 useKplApi 同款固定设备号
KPL_TOKEN = "036ca9cad6e44ee4a585c22cb2c298ed"
H_RT = "https://apphwhq.longhuvip.com/w1/api/index.php"
H_HIS = "https://apphis.longhuvip.com/w1/api/index.php"
TOL = 0.2                      # 竞价% / 换手% 容差(1位小数展示 + KPL/腾讯双源微差)

R = []                         # 报告行收集器


def log(msg=""):
    print(msg)
    R.append(msg)


def _kpl(url, params, retries=2, timeout=12):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.post(url, data=params, headers={"User-Agent": UA}, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            if str(j.get("errcode", "0")) not in ("0", "None"):
                raise ValueError(f"errcode={j.get('errcode')} {j.get('errmsg') or ''}")
            return j
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.4 * (i + 1))
    raise RuntimeError(f"KPL 失败 {params.get('a')}: {last}")


# ══════════════════════════ 1. 原始接口现场抓取 ══════════════════════════

def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_raw_pool_rt():
    """今日实时涨停池 5 板位 → {pid: [raw rows]} (DailyLimitPerformance RT, 同 fetchLimitPool rt)"""
    out = {}
    for pid in range(1, 6):
        j = _kpl(H_RT, {"a": "DailyLimitPerformance", "c": "HomeDingPan", "PidType": pid,
                        "Type": 4, "Index": 0, "Order": 0, "st": 500, "apiv": "w39",
                        "PhoneOSNew": 1, "DeviceID": DEV_ID})
        out[pid] = [r for g in (j.get("info") or []) if isinstance(g, list) for r in g]
        time.sleep(0.1)
    return out


def fetch_raw_pool_his(day):
    """历史涨停池 5 板位 → {pid: [raw rows]} (DailyLimitPerformance His, 只服务已完成日)"""
    out = {}
    for pid in range(1, 6):
        j = _kpl(H_HIS, {"a": "DailyLimitPerformance", "c": "HisHomeDingPan", "Day": day,
                         "PidType": pid, "Type": 4, "Index": 0, "Order": 0, "st": 500,
                         "PhoneOSNew": 1, "DeviceID": DEV_ID})
        out[pid] = [r for g in (j.get("info") or []) if isinstance(g, list) for r in g]
        time.sleep(0.12)
    return out


def fetch_risefall():
    rt = _kpl(H_RT, {"a": "RiseFallAnalysis", "apiv": "w43", "c": "HomeDingPan",
                     "PhoneOSNew": 1, "DeviceID": DEV_ID})
    his = _kpl(H_HIS, {"a": "RiseFallAnalysis", "st": 250, "apiv": "w43", "c": "HisHomeDingPan",
                       "PhoneOSNew": 1, "Index": 0, "DeviceID": DEV_ID})

    def m(r):
        return {"zt": int(f(r[0]) or 0), "dt": int(f(r[1]) or 0), "brokeRate": f(r[4]) or 0,
                "zhaban": int(f(r[5]) or 0), "day": str(r[6] or "")}

    today = m(rt["info"][0]) if rt.get("info") else None
    series = [m(r) for r in (his.get("info") or []) if isinstance(r, list) and len(r) >= 7]
    return {"today": today, "series": series}


def fetch_tianti():
    """涨停天梯 → [raw flat rows] (GetZhangTingTianTi_W47, 同 fetchTianTi, 跨组去重)"""
    j = _kpl(H_RT, {"a": "GetZhangTingTianTi_W47", "c": "FuPanLa", "PhoneOSNew": 1,
                    "VerSion": "6.2.20.2", "Red": 0, "apiv": "w47", "DeviceID": DEV_ID})
    seen, rows = set(), []
    for g in (j.get("StockList") or []):
        for r in g:
            code = str(r[0])
            if code in seen:
                continue
            seen.add(code)
            rows.append(r)
    return rows


def fetch_mood_his():
    j = _kpl(H_HIS, {"a": "ChangeStatistics", "st": 100, "c": "HisHomeDingPan", "Index": 0,
                     "PhoneOSNew": 1, "DeviceID": DEV_ID, "Token": KPL_TOKEN, "UserID": "3807176"})
    return [{"day": str(r.get("Day") or ""), "strong": int(f(r.get("strong")) or 0),
             "zt": int(f(r.get("ztjs")) or 0), "lbgd": int(f(r.get("lbgd")) or 0),
             "df": int(f(r.get("df_num")) or 0)} for r in (j.get("info") or [])]


def fetch_unsealed():
    """未涨停池 RT pid 1/2/4/5 → {pid: rows} (DailyLimitPerformance2, 同 fetchUnsealedPool)"""
    out = {}
    for pid in (1, 2, 4, 5):
        j = _kpl(H_RT, {"a": "DailyLimitPerformance2", "PidType": pid, "Type": 5, "Order": 1,
                        "Index": 0, "st": 100, "apiv": "w40", "c": "HomeDingPan",
                        "PhoneOSNew": 1, "DeviceID": DEV_ID})
        out[pid] = [r for g in (j.get("info") or []) if isinstance(g, list) for r in g]
        time.sleep(0.1)
    return out


def fetch_bidding_today():
    """今晨竞价(MorningBiddingList RT, 盘后仍返回当日 09:25 口径) pid 0..3 合并:
    {code: {code,name,bid_pct,turnover,circ_mv,plates,tag}} —— 换手取各榜最大值(同 export_json MAX)。"""
    merged = {}
    for pid in range(4):
        j = _kpl(H_RT, {"Order": 1, "a": "MorningBiddingList", "st": 100, "c": "HomeDingPan",
                        "PhoneOSNew": 1, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                        "VerSion": "5.20.0.2", "Token": KPL_TOKEN, "Index": 0, "PidType": pid,
                        "apiv": "w41", "Type": 4, "UserID": "3807176"})
        for r in (j.get("info") or []):
            if not isinstance(r, list) or len(r) < 13:
                continue
            code = str(r[0])
            cur = merged.get(code)
            turn = f(r[7]) or 0
            item = {"code": code, "name": r[1], "bid_pct": f(r[5]),
                    "turnover": turn, "circ_mv": f(r[12]), "plates": str(r[11] or ""),
                    "tag": str(r[16] or "") if len(r) > 16 else ""}
            if cur is None:
                merged[code] = item
            else:
                if turn > (cur["turnover"] or 0):
                    cur["turnover"] = turn
                for k in ("bid_pct", "circ_mv"):
                    if cur.get(k) is None and item.get(k) is not None:
                        cur[k] = item[k]
        time.sleep(0.1)
    return merged


def fetch_bidding_his(day):
    """历史竞价(MorningBiddingList His, 已完成交易日) —— 仅 ④ 失配诊断(旧日快照指纹)用"""
    j = _kpl(H_HIS, {"Order": 1, "a": "MorningBiddingList", "st": 100, "c": "HisHomeDingPan",
                     "PhoneOSNew": 1, "DeviceID": "d66474b3-fd78-3a95-a56d-76e29e765ea3",
                     "VerSion": "5.20.0.2", "Token": KPL_TOKEN, "Index": 0, "PidType": 0,
                     "Date": day, "apiv": "w41", "Type": 4, "UserID": "3807176"})
    out = {}
    for r in (j.get("info") or []):
        if isinstance(r, list) and len(r) >= 13:
            out[str(r[0])] = {"name": r[1], "bid_pct": f(r[5]), "turnover": f(r[7]) or 0,
                              "circ_mv": f(r[12])}
    return out


def tencent_auction_amt(code):
    """腾讯分时首行 0930 = 09:25 集合竞价成交 → 金额(元); 口径同 export_json._tencent_auction_amt"""
    pfx = "sh" if code.startswith("6") else ("bj" if code[0] in "48" else "sz")
    try:
        r = requests.get(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={pfx}{code}",
                         timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        rows = r.json()["data"][pfx + code]["data"]["data"]
        first = rows[0].split()
        return float(first[3]) if first and first[0] == "0930" else None
    except Exception:  # noqa: BLE001
        return None


# ══════════════════ 2. 行映射(抄 useKplApi.js / AuctionStore, 不发明) ══════════════════

def lp_row_js(r, pid):
    """useKplApi.fetchLimitPool 的行映射(Python 复刻, 供 Node 真引擎)"""
    return {"code": str(r[0]), "name": str(r[1]), "pid": pid,
            "ztTime": r[4] if isinstance(r[4], (int, float)) else None,
            "reason": str(r[5] or ""), "seal": f(r[6]) or 0, "maxSeal": f(r[7]) or 0,
            "mainNet": f(r[8]) or 0, "amount": f(r[11]) or 0,
            "plates": [p for p in str(r[12] or "").split("、") if p],
            "circMv": f(r[13]) or 0, "turnover": f(r[14]) or 0}


def lp_row_db(day, r, pid):
    """AuctionStore.save_limit_pool 的列映射(供临时库喂 Python 引擎)"""
    return (day, str(r[0]), str(r[1]), pid, int(f(r[4]) or 0), str(r[5] or ""),
            f(r[6]) or 0, f(r[7]) or 0, f(r[8]) or 0, f(r[11]) or 0,
            str(r[12] or ""), f(r[13]) or 0,
            str(r[18] or "") if len(r) > 18 else "")


def unsealed_row_js(r, pid):
    return {"code": str(r[0]), "name": str(r[1]), "pid": pid, "price": f(r[4]) or 0,
            "pct": f(r[5]) or 0, "plates": [p for p in str(r[6] or "").split("、") if p],
            "mainNet": f(r[7]) or 0, "amount": f(r[10]) or 0}


def tianti_groups_js(rows):
    """fetchTianTi 的分组映射(去重后按 level 分组, 同款 title)"""
    groups = {}
    parsed = [{"code": str(r[0]), "name": r[1], "bkCode": str(r[4] or ""), "bkName": r[5] or "",
               "label": r[11] or "", "level": int(f(r[13]) or 0), "cap": r[10]} for r in rows]
    for row in parsed:
        lv = row["level"]
        key = f"b{lv}" if lv > 0 else "b0"
        g = groups.setdefault(key, {"title": row["label"] or ("首板" if lv == 1 else f"{lv}板"),
                                    "level": lv, "rows": []})
        g["rows"].append(row)
    return sorted(groups.values(), key=lambda g: -g["level"])


def dedup_js(rows):
    seen, out = set(), []
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        out.append(r)
    return out


# ══════════════════ 3. Node 跑生产真引擎(①②③ live 口径) ══════════════════

NODE_ENTRY = r'''
// 对账: 用仓库里生产前端真引擎(emotionCycle/leaderBattle/stockPicks)重算 ①②③
// 输入 input.json = 现场抓的 KPL 原始数据, 已映射成 useKplApi 同款形状
import { loadCycleData, STAGE_RULES } from 'file://@UTIL@/emotionCycle.js'
import { loadBattleData, gateSentence } from 'file://@UTIL@/leaderBattle.js'
import { gateTier, statusWord, resolveVerdict } from 'file://@UTIL@/stockPicks.js'
import { readFileSync } from 'node:fs'

const inp = JSON.parse(readFileSync(process.argv[2], 'utf8'))
const kpl = {
  fetchTianTi: async () => inp.tianti,
  fetchRiseFall: async () => inp.riseFall,
  fetchMarketMood: async () => inp.moodHis,
  fetchLimitPool: async (date, pid) => ((date ? inp.hisPools : inp.rtPools)[pid]) || [],
  fetchUnsealedPool: async (pid) => inp.unsealed[pid] || [],
}
const cd = await loadCycleData(kpl, inp.todayDash)
const battle = await loadBattleData(kpl, cd, null, inp.prevBroken)
const cap = battle.strike?.gate?.cap
const cands = battle.strike?.candidates || []
const actCount = cands.filter(c => statusWord(c.status).txt !== '只看不买').length
const verdict = resolveVerdict(gateTier(cap), actCount)
console.log(JSON.stringify({
  cycleDate: cd.cycle?.date, stage: cd.cycle?.stage, matrix: cd.cycle?.matrix,
  mainlines: (cd.cycle?.mainlines || []).map(m => ({ board: m.board, count: m.count })),
  cap, capWord: STAGE_RULES[cd.cycle?.stage]?.cap || null,
  verdict: verdict.verdict, poolTxt: verdict.pool,
  sentence: gateSentence(cd.cycle?.stage || '', cd.cycle?.matrix?.high, cd.cycle?.matrix?.mid,
                         cap, actCount === 0),
  candidates: cands.map(c => ({ code: c.code, name: c.name, level: c.level ?? c.pid ?? 0,
                                status: c.status, word: statusWord(c.status).txt, score: c.score })),
  empty: !!battle.empty,
}))
'''


def run_node_engine(inp_path):
    entry = Path(tempfile.mkdtemp(prefix="recon_node_")) / "entry.mjs"
    entry.write_text(NODE_ENTRY.replace("@UTIL@", str(APP / "src" / "utils")), "utf-8")
    r = subprocess.run(["node", str(entry), str(inp_path)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"node 引擎失败: {r.stderr[-800:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def build_node_input(today, tianti, rf, mood_his, rt_pools, his_prev, unsealed, broken_prev, path):
    path.write_text(json.dumps({
        "today": today, "todayDash": today,
        "tianti": tianti_groups_js(tianti),
        "riseFall": rf, "moodHis": mood_his,
        "rtPools": {str(pid): dedup_js([lp_row_js(r, pid) for r in rows])
                    for pid, rows in rt_pools.items()},
        "hisPools": {str(pid): dedup_js([lp_row_js(r, pid) for r in his_prev.get(pid, [])])
                     for pid in range(1, 6)},
        "unsealed": {str(pid): [unsealed_row_js(r, pid) for r in unsealed.get(pid, [])]
                     for pid in (1, 2, 4, 5)},
        "prevBroken": [b["code"] for b in broken_prev],
    }, ensure_ascii=False), "utf-8")
    return path


# ══════════════════ 4. 临时库喂 Python 引擎(9:26 口径: ⑤ 状态 + ④) ══════════════════

def build_temp_db(days_pools, breadth_rows, broken_rows_by_day):
    """临时 sqlite(同 AuctionStore 列结构), 数据 100% 来自现场 His 接口。
    monkeypatch emotion_cycle.DB / stage_candidates 读库函数后, 9:26 生产路径原样复跑。"""
    tmp = Path(tempfile.mkdtemp(prefix="recon_db_")) / "auction.db"
    with sqlite3.connect(tmp) as c:
        c.execute("CREATE TABLE limit_pool (date TEXT, code TEXT, name TEXT, pid_type INTEGER,"
                  " zt_time INTEGER, reason TEXT, seal_amount REAL, max_seal REAL, main_net REAL,"
                  " amount REAL, plates TEXT, circ_mv REAL, tag TEXT, PRIMARY KEY (date, code))")
        c.execute("CREATE TABLE market_breadth (date TEXT PRIMARY KEY, zt INTEGER, broke_rate REAL)")
        c.execute("CREATE TABLE broken_pool (date TEXT, code TEXT, name TEXT, break_times INTEGER,"
                  " change_pct REAL, turnover REAL, height INTEGER)")
        for day, pidrows in days_pools.items():
            for pid, rows in pidrows.items():
                for r in rows:
                    if len(r) >= 14:
                        c.execute("INSERT OR REPLACE INTO limit_pool VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  lp_row_db(day, r, pid))
        for b in breadth_rows:
            c.execute("INSERT OR REPLACE INTO market_breadth VALUES (?,?,?)",
                      (b["day"], b["zt"], b["brokeRate"]))
        for day, rows in broken_rows_by_day.items():
            for r in rows:
                c.execute("INSERT OR REPLACE INTO broken_pool VALUES (?,?,?,?,?,?,?)",
                          (day, r["code"], r["name"], r["break_times"], r["change_pct"],
                           r["turnover"], r["height"]))
    return tmp


def recompute_926(tmp_db, bidding, prev, prev2, broken_prev, broken_prev2):
    """复跑 9:26 生产路径: compute_cycle(库内最近日=昨日收盘) + stage_pool(bid_date=今日)。
    对应 auction_scan 的 fallback 分支(当日池未落 → 用池内最近日的周期判定 + 今日竞价)。"""
    import src.analysis.emotion_cycle as ec
    import src.analysis.stage_candidates as sc

    ec.DB = tmp_db
    bid_map = {code: {"code": code, "name": v["name"], "change_pct": v["bid_pct"],
                      "main_net": None, "tag": v.get("tag") or "", "plates": v.get("plates") or ""}
               for code, v in bidding.items()}
    broken2_map = {r["code"]: r["name"] for r in broken_prev2}
    broken1_map = {r["code"]: r["name"] for r in broken_prev}
    sc._bid_pool = lambda d: bid_map
    sc._broken_map = lambda d: (broken2_map if d == prev2 else broken1_map if d == prev else {})
    with sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True) as c:
        mv_map = {r[0]: r[1] for r in c.execute(
            "SELECT code, circ_mv FROM limit_pool WHERE date=?", (prev,))}
    sc._circ_mv_map = lambda d: mv_map

    cycle = ec.compute_cycle(persist=False)          # dates[-1] = 昨日(如 09-11)
    pool = sc.stage_pool(cycle, max_n=30, bid_date=datetime.now(BJ_TZ).strftime("%Y-%m-%d"))
    wzq = [{"code": p["code"], "name": p["name"], "status": p.get("status"),
            "tag": p.get("tag"), "bid_pct": p.get("bid_pct")}
           for p in pool if "弱转强" in (p.get("status") or "")]
    return cycle, wzq


def wzq_bidday_semantics(his_pools, bidding, broken_prev, prev, prev2, stage):
    """任务/页面文案口径(= _synthesize_wzq): 昨日(=上一交易日)分歧池 ∩ 今日竞价 +1.5~7%"""
    def codes(day):
        return {str(r[0]) for pid in range(1, 6) for r in his_pools.get(day, {}).get(pid, [])}

    def rows(day):
        return [r for pid in range(1, 6) for r in his_pools.get(day, {}).get(pid, [])]

    sealed = codes(prev)
    duan = codes(prev2) - sealed
    rotten = {str(r[0]) for r in rows(prev) if f(r[6]) and f(r[7]) and f(r[6]) / f(r[7]) < 0.15}
    broken = {b["code"]: b["name"] for b in broken_prev}
    names = {str(r[0]): str(r[1]) for r in rows(prev)}
    status = "可做(弱转强)" if stage in ("启动", "发酵", "分歧") else "观察(弱转强·禁买期)"
    out = []
    for code in list(duan | rotten | set(broken))[:80]:
        b = bidding.get(code)
        if not b or b.get("bid_pct") is None or not (1.5 <= b["bid_pct"] <= 7):
            continue
        tag = "断板" if code in duan else ("炸板" if code in broken else "烂板")
        nm = names.get(code) or broken.get(code) or b.get("name") or code
        out.append({"code": code, "name": nm, "tag": tag, "bid_pct": b["bid_pct"],
                    "status": status})
    return out


def bidrank_recompute(his_pools, bidding, prev, src_bidding=None):
    """④ 口径重算: 昨日涨停池 pid>=2 ∩ 今日竞价实际换手 Top5
    (auction_scan.rank_lianban_bid + 腾讯 0930 补算, 数据 100% 现场)"""
    lb = []
    for pid in range(2, 6):
        for r in his_pools.get(prev, {}).get(pid, []):
            if len(r) >= 14:
                lb.append({"code": str(r[0]), "name": str(r[1]), "pid": pid, "circ_mv": f(r[13])})
    seen = {}
    for r in lb:  # 同 code 取更高 pid(同 lianban_bid_hs.fetch_lianban)
        if r["code"] not in seen or r["pid"] > seen[r["code"]]["pid"]:
            seen[r["code"]] = r
    src = bidding if src_bidding is None else src_bidding
    rows = []
    for code, r in seen.items():
        if "ST" in (r["name"] or "").upper():
            continue
        b = src.get(code)
        turn = (b.get("turnover") or 0) if b else 0
        bid_pct = b.get("bid_pct") if b else None
        mv = (b.get("circ_mv") if b else None) or r["circ_mv"]
        if not turn and mv:
            amt = tencent_auction_amt(code)
            if amt:
                turn = amt / mv * 100
        rows.append({"code": code, "name": r["name"], "height": r["pid"],
                     "bid_pct": bid_pct, "turnover": turn or 0})
    rows = [r for r in rows if r["turnover"] > 0]
    rows.sort(key=lambda x: (-x["turnover"], -(x["bid_pct"] or 0)))
    return rows[:5], len(seen)


# ══════════════════ 5. 无头浏览器抓生产 DOM ══════════════════

DOM_JS = r'''() => {
  const txt = el => (el ? el.textContent.trim() : '')
  const sec = kw => [...document.querySelectorAll('section.mt-sec')].find(s => s.querySelector('h3') && s.querySelector('h3').textContent.includes(kw))
  const num = s => { const m = String(s||'').match(/-?\d+(\.\d+)?/); return m ? parseFloat(m[0]) : null }
  const out = { url: location.href, day: txt(document.querySelector('.pk-day')) }
  const v = document.querySelector('.pk-verdict')
  if (v) {
    out.verdict = txt(v.querySelector('.pk-badge'))
    out.capChip = txt(v.querySelector('.pk-cap'))
    out.sentence = txt(v.querySelector('.pk-sub'))
    out.warn = txt(v.querySelector('.pk-warn'))
  }
  const st = sec('今日出击')
  if (st) {
    out.strikeEm = txt(st.querySelector('.mt-sec-head em'))
    out.matrix = [...st.querySelectorAll('.sb-mtx')].map(txt).filter(t => t.includes('高位'))
    out.cards = [...st.querySelectorAll('.mt-strike')].map(c => ({
      name: txt(c.querySelector('.mt-strike-top b')), lv: txt(c.querySelector('.mt-strike-lv')),
      status: txt(c.querySelector('.mt-strike-status')), score: num(txt(c.querySelector('.mt-strike-score'))) }))
    out.slims = [...st.querySelectorAll('.mt-slim')].map(c => ({
      name: txt(c.querySelector('.nm')), lv: txt(c.querySelector('.lv')),
      status: txt(c.querySelector('.st')), score: num(txt(c.querySelector('i'))) }))
    out.emptyNote = txt(st.querySelector('.mt-hold'))
    out.toggle = txt(st.querySelector('.mt-strike-toggle'))
  }
  const pre = sec('09:25 盘前候选')
  if (pre) {
    out.preEm = txt(pre.querySelector('.mt-sec-head em'))
    out.pre = [...pre.querySelectorAll('.mt-row')].map(r => ({
      name: txt(r.querySelector('.mt-row-name')).replace(/\d+板$/, '').trim(),
      lv: num(txt(r.querySelector('.mt-strike-lv'))),
      bid: num([...r.querySelectorAll('.mt-row-count')].map(txt).find(t => t.includes('竞价'))),
      hs: num([...r.querySelectorAll('.mt-row-count')].map(txt).find(t => t.includes('换手'))) }))
  }
  const wz = sec('今日竞价弱转强')
  if (wz) {
    out.wzqEm = txt(wz.querySelector('.mt-sec-head em'))
    out.wzq = [...wz.querySelectorAll('.mt-review')].map(r => ({
      name: txt(r.querySelector('.nm')), status: txt(r.querySelector('.mt-strike-status')),
      sub: txt(r.querySelector('.wzq-sub')) }))
    out.wzqHoldNote = [...wz.querySelectorAll('.mt-hold')].map(txt)
    out.wzqToggles = [...wz.querySelectorAll('.mt-strike-toggle')].map(txt)
  }
  const ll = sec('大模型选股')
  if (ll) {
    out.llmEm = txt(ll.querySelector('.mt-sec-head em'))
    out.llmRegime = txt(ll.querySelector('.llm-regime'))
    out.llmPos = txt(ll.querySelector('.llm-pos'))
    out.llmHold = [...ll.querySelectorAll('.mt-hold')].map(txt)
    out.llmPickNames = [...ll.querySelectorAll('.mt-review .nm')].map(txt)
  }
  return out
}'''


def fetch_dom(shot=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 414, "height": 900}, is_mobile=True)
        errors, req_fail = [], []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.error: {m.text}")
                if m.type == "error" else None)
        page.on("requestfailed", lambda r: req_fail.append(f"{r.url[:110]}"))
        page.goto(PROD_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_function(
                "() => { const b=document.querySelector('.pk-verdict .pk-badge');"
                " return b && b.textContent.trim() !== '…' }", timeout=40000)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(6)   # auction.json / 竞价快照二次渲染余量
        dom = page.evaluate(DOM_JS)
        dom["_errors"] = errors[:10]
        dom["_reqfail"] = req_fail[:10]
        if shot:
            try:
                page.screenshot(path=shot, full_page=True)
            except Exception:  # noqa: BLE001
                pass
        browser.close()
        return dom


# ══════════════════ 6. 比对辅助 ══════════════════

class Verdict:
    def __init__(self):
        self.items = []

    def add(self, mod, mark, note=""):
        self.items.append((mod, mark, note))
        log(f"  {mark} {note}" if note else f"  {mark}")

    def counts(self):
        c = {"✅": 0, "⚠️": 0, "❌": 0, "⏭": 0}
        for _, m, _ in self.items:
            c[m] = c.get(m, 0) + 1
        return c


def close(a, b, tol=TOL):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def cmp_pre_rows(exp, act):
    """④ 行比对: 名称序列 + 每行(板数/竞价/换手)容差"""
    diffs = []
    if [e["name"] for e in exp] != [a["name"] for a in act]:
        diffs.append(f"名单/顺序不一致: 重算={[e['name'] for e in exp]} vs 页面={[a['name'] for a in act]}")
    for e, a in zip(exp, act):
        if e["name"] != a["name"]:
            continue
        if (e.get("height") or 0) != (a.get("lv") or 0):
            diffs.append(f"{e['name']} 板数: 重算{e.get('height')} vs 页面{a.get('lv')}")
        if not close(e.get("bid_pct"), a.get("bid")):
            diffs.append(f"{e['name']} 竞价%: 重算{e.get('bid_pct')} vs 页面{a.get('bid')}")
        if not close(e.get("turnover"), a.get("hs")):
            diffs.append(f"{e['name']} 换手%: 重算{e.get('turnover')} vs 页面{a.get('hs')}")
    return diffs


def parse_wzq_sub(sub):
    m = re.search(r"昨日(断板|烂板|炸板)分歧\s*·\s*竞价\s*([+\-\d.]+)%", sub or "")
    if not m:
        return None, None
    return m.group(1), float(m.group(2))


def sort_picks_like_page(rows):
    """页面 sortPicksFirst: 可买>待确认>只看, 次级竞价涨幅降序"""
    def w(r):
        s = r.get("status") or ""
        return 0 if s.startswith("可做") else (1 if s.startswith("备选") else 2)
    return sorted(rows, key=lambda r: (w(r), -(float(r.get("bid_pct") or 0))))


def diagnose_pre_mismatch(his_pools, prev, prev2, act_names):
    """④ 失配诊断: 页面名单是否= 旧日组合(prev2连板×prev竞价) —— auction.json 停旧日的事故指纹"""
    try:
        old_bid = fetch_bidding_his(prev)
        if not old_bid:
            return "历史竞价接口无数据, 旧日指纹无法核验"
        rows, _ = bidrank_recompute(his_pools, old_bid, prev2, src_bidding=old_bid)
        old_names = [r["name"] for r in rows]
        if old_names == act_names:
            return (f"页面名单恰好等于 {prev2}连板×{prev}竞价 的旧 Top5 → bidrank 疑停在过去交易日"
                    f"(采集/导出环节: auction.json 未更新或回退)")
        return f"亦不匹配旧日组合({prev2}连板×{prev}竞价={old_names}); 疑字段/成员错(采集或导出环节)"
    except Exception as e:  # noqa: BLE001
        return f"诊断未完成({e})"


def gh_auction_runs_today(today):
    """公开 API 查 auction.yml 当日 run(管线健康, 北京时区)"""
    try:
        r = requests.get("https://api.github.com/repos/WXinYi/stockboard/actions/workflows/"
                         "auction.yml/runs?per_page=8", timeout=15,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": "reconcile-script"})
        r.raise_for_status()
        runs = r.json().get("workflow_runs") or []

        def bj_date(x):
            try:
                return datetime.fromisoformat(
                    (x.get("run_started_at") or "").replace("Z", "+00:00")).astimezone(BJ_TZ)
            except ValueError:
                return None
        todays = [x for x in runs if (d := bj_date(x)) and d.strftime("%Y-%m-%d") == today]
        if not todays:
            return False, "当日无 auction.yml run 记录(公开API)"
        ok = [x for x in todays if x.get("conclusion") == "success"]
        if ok:
            return True, f"auction.yml 当日 {len(todays)} run 中 {len(ok)} 成功"
        detail = ",".join(f"#{x['run_number']}:{x.get('conclusion') or x.get('status')}"
                          for x in todays[:3])
        return False, f"auction.yml 当日 {len(todays)} run 无成功({detail})"
    except Exception as e:  # noqa: BLE001
        return False, f"Actions 公开API不可用({e})"


# ══════════════════ 7. 逐模块比对(①②③ live) ══════════════════

def cmp_live(js, dom, today):
    """①②③ 比对; 返回 (hard_diffs: bool, 逐模块 (mark, note) 列表)"""
    out = []
    hard = False

    # ① 结论头
    diffs = []
    if dom.get("verdict") != js["verdict"]:
        diffs.append(f"结论词: 重算={js['verdict']} 页面={dom.get('verdict')}")
    cap_page = (dom.get("capChip") or "").replace("仓位上限 ", "")
    if cap_page and js["capWord"] and cap_page != js["capWord"]:
        diffs.append(f"仓位上限: 重算={js['capWord']} 页面={cap_page}")
    m = re.search(r"情绪(冰点|启动|发酵|高潮|分歧|退潮)", dom.get("sentence") or "")
    if m and m.group(1) != js["stage"]:
        diffs.append(f"阶段: 重算={js['stage']} 页面={m.group(1)}")
    pm = re.search(r"(池关闭|池全开|池限\d+分)", dom.get("sentence") or "")
    exp_pool = "池关闭" if js["cap"] in (0, None) else ("池全开" if js["cap"] >= 100 else f"池限{js['cap']}分")
    if pm and pm.group(1) != exp_pool:
        diffs.append(f"池状态: 重算={exp_pool} 页面={pm.group(1)}")
    if diffs:
        hard = True
        out.append(("①", "❌_", "; ".join(diffs)))
    else:
        shift = ""
        if "（" in (dom.get("sentence") or ""):
            shift = " · " + dom["sentence"].split("（")[-1].rstrip("）")
        out.append(("①", "✅", f"结论={js['verdict']} 上限={js['capWord']} 阶段={js['stage']} {exp_pool}{shift}"))

    # ② 今日出击
    act_c = [{"name": c["name"], "word": c["status"], "lv": c.get("lv")}
             for c in (dom.get("cards") or []) + (dom.get("slims") or [])]
    exp_c = [{"name": c["name"], "word": c["word"], "lv": c.get("lv") or c.get("level")}
             for c in js["candidates"][:5]]
    if dom.get("emptyNote") and "暂无达标候选" in dom.get("emptyNote", ""):
        n_act = len([c for c in js["candidates"] if c["word"] != "只看不买"])
        out.append(("②", "✅" if n_act == 0 else "❌",
                    f"页面'暂无达标候选' vs 重算可动手数 {n_act}"))
        hard = hard or n_act > 0
    elif not act_c and not js["candidates"]:
        out.append(("②", "✅", "两侧均无候选"))
    else:
        diffs = []
        exp_names, act_names = [c["name"] for c in exp_c], [c["name"] for c in act_c]
        if exp_names != act_names:
            if set(exp_names) == set(act_names):
                diffs.append(f"顺序差(评分并列容忍): 重算={exp_names} 页面={act_names}")
            else:
                diffs.append(f"成员差: 重算多{[n for n in exp_names if n not in act_names]}"
                             f" 页面多{[n for n in act_names if n not in exp_names]}")
        for e, a in zip(exp_c, act_c):
            if e["name"] == a["name"] and e["word"] != a["word"]:
                diffs.append(f"{e['name']} 状态: 重算={e['word']} 页面={a['word']}")
        if any(("成员差" in d or "状态:" in d) for d in diffs):
            hard = True
            out.append(("②", "❌_", "; ".join(diffs)))
        elif diffs:
            out.append(("②", "⚠️", "; ".join(diffs) + " (评分并列/时点漂移)"))
        else:
            out.append(("②", "✅", f"前{len(act_c)}只成员与状态一致: "
                        + ", ".join(f"{c['name']}({c['word']})" for c in act_c[:5])))

    # ③ 情绪周期/梯队矩阵/决策日
    diffs = []
    act_mx = dom.get("matrix") or []
    mx = (js["matrix"] or {})
    exp_mx = f"高位{mx.get('high')}×中位{mx.get('mid')}"
    if act_mx and not any(re.search(rf"高位{mx.get('high')}×中位{mx.get('mid')}", t) for t in act_mx):
        am = re.search(r"高位(.+?)×中位(.+?)[：:]", act_mx[0])
        diffs.append(f"矩阵: 重算={exp_mx} 页面={am.groups() if am else act_mx[0][:24]}")
    day_txt = (dom.get("day") or "").replace("决策日", "").strip()
    if day_txt and day_txt != today:
        diffs.append(f"决策日: 重算={today} 页面={day_txt}")
    if diffs:
        hard = True
        out.append(("③", "❌_", "; ".join(diffs)))
    else:
        out.append(("③", "✅", f"阶段={js['stage']} 矩阵={exp_mx} 决策日={today}; "
                    f"主线(依据页口径)={[m['board'] + str(m['count']) + '只' for m in js['mainlines'][:3]]}; "
                    f"梯队晋升率 高{mx.get('high')}/中{mx.get('mid')}(晋级率阈值 CYCLE_CFG)"))
    return hard, out


# ══════════════════ 8. 主流程 ══════════════════

def main():
    ap = argparse.ArgumentParser(description="选股页 6 模块对账巡检")
    ap.add_argument("--date", default=NOW.strftime("%Y-%m-%d"))
    ap.add_argument("--no-browser", action="store_true", help="只重算不抓 DOM(调试)")
    ap.add_argument("--shot", default="/tmp/reconcile_market.png")
    args = ap.parse_args()
    today = args.date
    V = Verdict()
    log(f"══ 对账巡检 {today} · 基准=现场原始接口 · 启动 {NOW.strftime('%H:%M:%S')} ══")

    # ---- 现场抓取 ----
    log("[1/6] 现场抓取原始接口…")
    rf = fetch_risefall()
    if not rf["today"] or rf["today"]["day"] != today:
        alt = (rf["today"] or {}).get("day") or "无"
        if rf["today"]:
            log(f"    ℹ️ 宽度接口今日行={alt}(≠{today}), 按接口实际日对账")
            today = rf["today"]["day"]
        else:
            V.add("ALL", "⏭", f"宽度接口今日行为空(非交易日/源不可用), 对账终止")
            log(f"══ 总结: ⏭ 非交易日或源不可用 ══")
            return 0
    prev = next((r["day"] for r in rf["series"] if r["day"] < today), None)
    if not prev:
        log("❌ 拿不到上一交易日(涨跌宽度历史序列空)")
        return 1
    hist_days = [r["day"] for r in rf["series"] if r["day"] < today][:8]   # 近8个交易日(含prev)
    log(f"    今日={today} 上一交易日={prev} 历史池窗口={hist_days[-3:]}…({len(hist_days)}天)")

    bidding = fetch_bidding_today()
    log(f"    今晨竞价列表合并 {len(bidding)} 只(MorningBiddingList RT, 09:25 口径)")

    his_pools = {}
    for d in hist_days:
        his_pools[d] = fetch_raw_pool_his(d)
    log(f"    昨日({prev})涨停池 {sum(len(v) for v in his_pools[prev].values())} 只(His 5板位)")
    rt_pools = fetch_raw_pool_rt()
    tianti = fetch_tianti()
    mood_his = fetch_mood_his()
    unsealed = fetch_unsealed()
    broken_prev = fetch_broken_pool(prev)
    prev2 = hist_days[1] if len(hist_days) > 1 else None
    broken_prev2 = fetch_broken_pool(prev2) if prev2 else []
    log(f"    今日RT池 {sum(len(v) for v in rt_pools.values())} 只 · 天梯 {len(tianti)} 行 · "
        f"未涨停池 {sum(len(v) for v in unsealed.values())} · 昨日炸板(选股宝) {len(broken_prev)} 只 · "
        f"情绪序列 {len(mood_his)} 天")

    # ---- ④⑤ 重算(9:26 口径) ----
    log("[2/6] ④⑤ 重算(9:26 口径: 现场 His 数据建临时库 → 复跑 emotion_cycle/stage_candidates)…")
    tmp_db = build_temp_db({d: his_pools[d] for d in hist_days}, rf["series"][:12],
                           {prev: broken_prev, **({prev2: broken_prev2} if prev2 else {})})
    cycle926, wzq_archive = recompute_926(tmp_db, bidding, prev, prev2, broken_prev, broken_prev2)
    log(f"    9:26 周期(昨收口径, {cycle926['date']}): {cycle926['stage']} (置信{cycle926['confidence']}/9) "
        f"高度{cycle926['metrics']['height']} 涨停{cycle926['metrics']['zt']}")
    wzq_bidday = wzq_bidday_semantics(his_pools, bidding, broken_prev, prev, prev2, cycle926["stage"])
    log(f"    ⑤ 弱转强: stage_pool重算 {len(wzq_archive)} 只 / _synthesize口径 {len(wzq_bidday)} 只"
        f" (09-14 锚修复后两口径同锚分歧@{prev}; 旧锚@{prev2} 留作未部署诊断)")
    bidrank, n_lb = bidrank_recompute(his_pools, bidding, prev)
    log(f"    ④ bidrank: 昨日连板 {n_lb} 只 → Top5 "
        + ", ".join(f"{r['name']}(换手{r['turnover']:.2f}%)" for r in bidrank))

    # ---- ①②③ 重算(Node 跑生产真 JS 引擎) ----
    log("[3/6] ①②③ 重算(Node 跑仓库生产前端真引擎 emotionCycle/leaderBattle/stockPicks)…")
    inp_path = Path(tempfile.mkdtemp(prefix="recon_")) / "input.json"
    build_node_input(today, tianti, rf, mood_his, rt_pools, his_pools[prev], unsealed,
                     broken_prev, inp_path)
    js = run_node_engine(inp_path)
    log(f"    live 周期: {js['stage']} · 矩阵 高位{(js['matrix'] or {}).get('high')}"
        f"×中位{(js['matrix'] or {}).get('mid')} · cap={js['cap']} · 结论={js['verdict']} · "
        f"候选 {len(js['candidates'])} 只 · 主线 {[m['board'] for m in js['mainlines'][:3]]}")

    # ---- DOM ----
    dom = None
    if args.no_browser:
        log("[4/6] --no-browser 跳过 DOM")
    else:
        log("[4/6] 无头浏览器抓生产 DOM…")
        try:
            dom = fetch_dom(args.shot)
            log(f"    决策日={dom.get('day')} 结论头={dom.get('verdict')} / {dom.get('capChip')}")
            if dom.get("_errors"):
                log(f"    页面报错(前4): {dom['_errors'][:4]}")
        except Exception as e:  # noqa: BLE001
            log(f"    ⚠️ DOM 抓取失败: {e}")

    # ---- 逐模块比对 ----
    log("[5/6] 逐模块比对")
    if dom is None:
        log("  ①②③④⑤⑥ ⏭ 生产页 DOM 不可得(浏览器失败) — 仅输出上方重算基准")
        c = {"✅": 0, "⚠️": 0, "❌": 0, "⏭": 6}
    else:
        # live 模块(①②③): 失配先重抓重算一次(排除盘中分钟级漂移), 仍失配才 ❌
        hard, live_out = cmp_live(js, dom, today)
        if hard:
            log("  ℹ️ live 模块失配 → 重抓原始接口+重跑真引擎一次(排除时点漂移)…")
            try:
                rf2 = fetch_risefall()
                rt2, tt2, us2 = fetch_raw_pool_rt(), fetch_tianti(), fetch_unsealed()
                p2 = Path(tempfile.mkdtemp(prefix="recon_")) / "input2.json"
                build_node_input(today, tt2, rf2, mood_his, rt2, his_pools[prev], us2,
                                 broken_prev, p2)
                js2 = run_node_engine(p2)
                hard2, live_out = cmp_live(js2, dom, today)
                if hard2:
                    log(f"    重算(二次)仍失配: {js2['stage']}/cap={js2['cap']}/{js2['verdict']}")
                    js = js2   # 以二次结果出报告
                else:
                    for mod, mk, note in live_out:
                        V.add(mod, "⚠️" if mk == "✅" else mk,
                              note + " [一次失配=盘中时点漂移, 重抓后一致]")
                    js = js2
            except Exception as e:  # noqa: BLE001
                log(f"    ⚠️ 重抓失败({e}), 以首次重算为准")
        if not any(mod in ("①", "②", "③") for mod, _, _ in V.items):
            # 正常路径(或重试仍失配/重试异常): 重试成功分支已在上面以 ⚠️ 收录, 不重复
            for mod, mk, note in live_out:
                V.add(mod, mk if mk != "❌_" else "❌", note)

        # ⑥ 先取管线健康
        gh_ok, gh_note = gh_auction_runs_today(today)

        # ④ 09:25 盘前候选
        log("  ④ 09:25 盘前候选(auction.json bidrank, 09:25 口径):")
        act_pre = dom.get("pre") or []
        if not act_pre:
            V.add("④", "❌" if bidrank else "⚠️", "页面无盘前候选行"
                  + ("(重算有 Top5, 疑展示/数据缺失)" if bidrank else "(重算亦为空, 一致)"))
        else:
            diffs = cmp_pre_rows(bidrank, act_pre)
            if not diffs:
                V.add("④", "✅", f"Top{len(act_pre)}成员/板数/竞价/换手全一致: "
                      + ", ".join(f"{r['name']}(换手{r['turnover']:.2f}%)" for r in bidrank)
                      + f"; 昨日基准日={prev}(上一真实交易日 ✓)")
            else:
                diag = diagnose_pre_mismatch(his_pools, prev, prev2, [a["name"] for a in act_pre])
                V.add("④", "❌", "; ".join(diffs) + f" [诊断: {diag}]")

        # ⑤ 今日竞价弱转强
        log("  ⑤ 今日竞价弱转强:")
        wzq_dom = dom.get("wzq") or []
        wzq_em = dom.get("wzqEm") or ""
        if today not in wzq_em:
            V.add("⑤", "❌", f"竞价口径日期过期: 页面='{wzq_em}' 期望含 {today}"
                  " (auction.json/strike_review 停旧日的事故指纹)")
        elif not wzq_dom:
            if not wzq_bidday and not wzq_archive:
                V.add("⑤", "✅", f"页面与重算均无弱转强候选(昨日无分歧股或竞价未达 +1.5~7%)")
            else:
                V.add("⑤", "❌", "页面无弱转强行但重算有: "
                      f"文案口径={[e['name'] for e in wzq_bidday]} 存档口径={[e['name'] for e in wzq_archive]}")
        else:
            act_rows = [{"name": r["name"], "tag": parse_wzq_sub(r.get("sub"))[0],
                         "bid_pct": parse_wzq_sub(r.get("sub"))[1], "word": r["status"]}
                        for r in wzq_dom]

            def match(exp_rows):
                exp_act = sort_picks_like_page(
                    [e for e in exp_rows if (e.get("status") or "").startswith("可做")])[:5]
                names_e, names_a = [e["name"] for e in exp_act], [a["name"] for a in act_rows]
                if names_e != names_a:
                    return False, f"重算={names_e} 页面={names_a}"
                for e, a in zip(exp_act, act_rows):
                    if e.get("tag") and a.get("tag") and e["tag"] != a["tag"]:
                        return False, f"{e['name']} tag 重算={e['tag']} 页面={a['tag']}"
                    if not close(e.get("bid_pct"), a.get("bid_pct")):
                        return False, f"{e['name']} 竞价 重算={e.get('bid_pct')} 页面={a.get('bid_pct')}"
                return True, ""

            ok_bid, note_bid = match(wzq_bidday)
            ok_arch, note_arch = match(wzq_archive)
            if ok_bid:
                extra = ""
                arch_names = [e["name"] for e in wzq_archive]
                if wzq_archive and arch_names != [e["name"] for e in wzq_bidday]:
                    extra = (f" (注: 存档口径 stage_pool 分歧@{prev2} 名单不同={arch_names},"
                             f" 页面未走该口径)")
                V.add("⑤", "✅", f"名单/竞价%与文案口径(昨日={prev}分歧∩今竞价+1.5~7%)一致: "
                      + ", ".join(a["name"] for a in act_rows) + extra)
            elif ok_arch:
                V.add("⑤", "❌",
                      f"页面名单={[e['name'] for e in wzq_archive]} 命中 stage_pool 弱转强块旧锚"
                      f"(分歧池=@{prev2}=周期日前一日), 而页面文案与本模块兜底口径(_synthesize_wzq)"
                      f"的'昨日分歧'应=@{prev} → 期望名单="
                      f"{[e['name'] + '(' + e['tag'] + ')' for e in wzq_bidday]}。根因(off-by-one, 每晨"
                      f"名单恒旧一个交易日)已于 2026-09-14 修复: stage_candidates 锚改 bid_date 前一"
                      f"交易日; 仍命中此分支 = 生产端未部署修复或回归。旁证: 同股两口径 tag 不同"
                      f"(旧锚断板@{prev2} vs 新锚烂板@{prev})")
            else:
                V.add("⑤", "❌", f"两种口径均不匹配: 文案口径 {note_bid}; 存档口径 {note_arch}")

        # ⑥ 大模型选股(管线健康)
        log("  ⑥ 大模型选股(管线健康):")
        llm_em = dom.get("llmEm") or ""
        holds = dom.get("llmHold") or []
        if any("无有效输出" in h for h in holds):
            V.add("⑥", "❌", "页面显示'今日无有效输出(模型输出异常, 已降级)' — llm_review degraded")
        elif any("暂无模型输出" in h for h in holds):
            V.add("⑥", "❌", f"llm 卡空(暂无模型输出) 且 {gh_note} — 疑竞价班 llm 步骤失败/无 key")
        elif dom.get("llmRegime"):
            utc_note = ("; 卡片时间为 CI runner 本地(UTC)时戳, 页面直显 HH:MM 未换北京时间"
                        "(如 02:55=北京 10:55, 展示层小瑕疵不影响新鲜度)")
            note = f"卡片在档: {llm_em} · regime={dom.get('llmRegime')}"
            if gh_ok:
                V.add("⑥", "✅", note + f"; {gh_note}" + utc_note)
            else:
                V.add("⑥", "⚠️", note + f"; {gh_note} — 卡片有内容但当日 run 未确认成功, 新鲜度以⑤日期/早盘曾判旁证" + utc_note)
        else:
            V.add("⑥", "⚠️", f"llm 卡片状态未识别(em='{llm_em}') {gh_note}")
        c = V.counts()

    # ---- 汇总 ----
    log("")
    log(f"══ 总结: 6 模块中 ✅{c['✅']} ⚠️{c['⚠️']} ❌{c['❌']}"
        + (f" ⏭{c['⏭']}" if c["⏭"] else "") + " ══")
    Path("/tmp/reconcile_report.txt").write_text("\n".join(R), "utf-8")
    if c["❌"]:
        push_dingtalk("\n".join(R), c)
        return 1
    return 0


def push_dingtalk(report, counts):
    try:
        from src.notify.dingtalk import DingTalk
        if not os.environ.get("DINGTALK_URL"):
            log("‼️ 检出 ❌ 但本地未配置 DINGTALK_URL(钉钉 webhook) — 告警未推送; CI 侧配置后自动生效")
            return
        head = f"🚨 选股页对账巡检告警 {NOW.strftime('%Y-%m-%d %H:%M')} — ❌{counts['❌']}项"
        body = "\n".join(l for l in report.splitlines()
                         if l.strip().startswith(("  ❌", "  ⚠️", "══")))[:1800]
        print("📣 钉钉:", DingTalk().send_markdown(head, f"{head}\n\n{body}"))
    except Exception as e:  # noqa: BLE001
        log(f"‼️ 钉钉推送失败: {e}")


if __name__ == "__main__":
    sys.exit(main())
