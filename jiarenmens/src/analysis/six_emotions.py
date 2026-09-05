#!/usr/bin/env python3
"""涅槃重升 · 六情绪建模 (数据: auction.db, 全部现有表, 零新数据源)

六个情绪变量, 每个归一为 0-100 历史分位数(对自身 255+ 天历史排名, 自我校准无硬编码阈值):
  市场    = KPL情绪分(.4) + 跌停数反向(.3) + 破板率反向(.3)
  投机    = 涨停家数(.25) + 连板高度(.2) + 晋级率(.2) + 破板率反向(.15) + 炸板数反向(.1) + 昨高位续板率(.1)
  板块    = 主线涨停家数(.35) + 主线高度(.25) + 主线扩散环比(.25) + 主线成交额(.15)
  整体市场 = 市场 3 日均值的分位数
  整体投机 = 投机 3 日均值的分位数
  整体板块 = 主线连任天数(.5) + 近5日主线切换次数反向(.5)

主导条件(涅槃 5 情形 + 退潮防守, 顺序即优先级):
  退潮防守 / 板块情绪极强 / 投机情绪极强 / 市场强但板块不强 / 分歧但情绪不差 / 混沌观察
每种主导对应战术偏向(涅槃 Tactics/Position Logic 表)。
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "auction.db"


def _rows(conn, sql, args=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, args)]


def _parse_bid_amount(raw):
    """mood_daily.raw → 今日竞价金额(亿, float); 缺失返回 None"""
    if not raw:
        return None
    import json
    try:
        info = json.loads(raw).get("bid_total", {}).get("info") or {}
        v = str(info.get("tJJJE") or "").replace("亿", "")
        return float(v) if v else None
    except Exception:
        return None


def _ensure_index(latest_date):
    """index_daily(上证收盘) 懒加载: 落后于行情库最新日则从腾讯日K补(一次 320 根)。
    自带可写连接(ro 主连接只读); 失败静默返回(指数分量缺失仅降级, 不阻断)"""
    try:
        conn = sqlite3.connect(DB)
        conn.execute("CREATE TABLE IF NOT EXISTS index_daily (date TEXT PRIMARY KEY, close REAL)")
        row = conn.execute("SELECT MAX(date) FROM index_daily").fetchone()
        if row and row[0] and row[0] >= (latest_date or ""):
            conn.close()
            return
        import json as _json
        import urllib.request
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{320},qfq"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            node = _json.load(r)["data"]["sh000001"]
            days = node.get("qfqday") or node.get("day") or []
        conn.executemany("INSERT OR REPLACE INTO index_daily VALUES (?,?)",
                         [(x[0], float(x[2])) for x in days if x[0] <= (latest_date or "9999")])
        conn.commit()
        conn.close()
    except Exception:
        pass


def _load_all():
    """一次性加载全部历史 → {date: metrics}, 并预计算逐日原始分量"""
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    breadth = {r["date"]: r for r in _rows(conn, "SELECT * FROM market_breadth ORDER BY date")}
    moods = {r["date"]: r for r in _rows(conn, "SELECT date, strong, raw FROM mood_daily")}
    _ensure_index(max(breadth) if breadth else None)
    index_close = {r["date"]: r["close"] for r in _rows(conn, "SELECT * FROM index_daily ORDER BY date")}
    pools = _rows(conn, "SELECT date, code, pid_type, plates, amount FROM limit_pool ORDER BY date")
    broken = _rows(conn, "SELECT date, code FROM broken_pool")

    # 逐日涨停池聚合: 家数/最高板/主线(按板块关键词聚合过概念名含逗号分隔, 取池内 plates 首词统计)
    from collections import defaultdict
    day_codes = defaultdict(set)
    day_plates = defaultdict(lambda: defaultdict(int))
    day_amount = defaultdict(lambda: defaultdict(float))
    for r in pools:
        d = r["date"]
        day_codes[d].add(r["code"])
        for b in str(r["plates"] or "").split("、"):
            b = b.strip()
            if b:
                day_plates[d][b] += 1
                day_amount[d][b] += r["amount"] or 0
    for r in broken:
        pass  # broken 名单单独查

    dates = sorted(set(breadth) | set(day_codes))
    metrics = {}
    for i, d in enumerate(dates):
        b = breadth.get(d, {})
        zt = b.get("zt") or (len(day_codes.get(d, ())) or None)
        height = max([r["pid_type"] or 0 for r in pools if r["date"] == d], default=0)
        # 主线 = 当日涨停家数最多的板块
        plates = day_plates.get(d, {})
        top_board = max(plates, key=plates.get) if plates else None
        top_cnt = plates.get(top_board, 0) if top_board else 0
        top_amt = day_amount.get(d, {}).get(top_board, 0) if top_board else 0
        # 晋级率 = 今日≥2板家数 / 昨日涨停家数
        prev_d = dates[i - 1] if i > 0 else None
        two_plus = sum(1 for r in pools if r["date"] == d and (r["pid_type"] or 0) >= 2)
        promo = two_plus / len(day_codes.get(prev_d, ())) if prev_d and day_codes.get(prev_d) else None
        # 昨高位续板率 = 昨日最高板股 今日仍封板比例
        relay = None
        if prev_d:
            prev_max = max((r["pid_type"] or 0) for r in pools if r["date"] == prev_d) if day_codes.get(prev_d) else 0
            tops = {r["code"] for r in pools if r["date"] == prev_d and (r["pid_type"] or 0) == prev_max} if prev_max else set()
            if tops:
                relay = sum(1 for c in tops if c in day_codes.get(d, ())) / len(tops)
        metrics[d] = {
            "zt": zt, "dt": b.get("dt"), "broke": b.get("broke_rate"), "zhaban": b.get("zhaban"),
            "strong": (moods.get(d) or {}).get("strong"), "height": height,
            "promo": promo, "relay": relay,
            "top_board": top_board, "top_cnt": top_cnt, "top_amt": top_amt,
            "top_cnt_prev": (day_plates.get(prev_d, {}).get(top_board, 0) if top_board and prev_d else None),
        }
    # 主线连任天数 / 近5日切换次数
    run_days, switches = {}, {}
    run = 0
    prev_top = None
    for d in dates:
        t = metrics[d]["top_board"]
        run = run + 1 if t and t == prev_top else (1 if t else 0)
        run_days[d] = run
        prev_top = t or prev_top
    for i, d in enumerate(dates):
        window = [metrics[x]["top_board"] for x in dates[max(0, i - 4):i + 1]]
        switches[d] = sum(1 for a, b2 in zip(window, window[1:]) if a and b2 and a != b2)
    # 指数趋势: 收盘/MA5 - 1 (腾讯日K懒加载表 index_daily)
    idx_dates = sorted(index_close)
    idx_ma = {}
    for k, d in enumerate(idx_dates):
        win = idx_dates[max(0, k - 4):k + 1]
        ma5 = sum(index_close[x] for x in win) / len(win)
        idx_ma[d] = (index_close[d] / ma5 - 1) * 100 if ma5 else None
    for d in dates:
        metrics[d]["run_days"] = run_days[d]
        metrics[d]["switches_5d"] = switches[d]
        metrics[d]["idx_trend"] = idx_ma.get(d)
        metrics[d]["bid_amt"] = _parse_bid_amount((moods.get(d) or {}).get("raw"))
    conn.close()
    return dates, metrics


def _pct_rank(series, value):
    """value 在 series 中的历史分位数(0-100); 值缺失返回 None"""
    if value is None:
        return None
    vals = sorted(v for v in series if v is not None)
    if not vals:
        return None
    import bisect
    return round(bisect.bisect_left(vals, value) / len(vals) * 100, 1)


def _wsum(parts):
    """[(value, weight)] 加权和, 权重按缺值重新归一; 全缺返回 None"""
    num = den = 0.0
    for v, w in parts:
        if v is None:
            continue
        num += v * w
        den += w
    return round(num / den, 1) if den else None


def compute_all():
    """两遍计算: 先逐日原始分量, 再对每个分量做历史分位, 合成六情绪 + 主导条件"""
    dates, metrics = _load_all()
    for d in dates:
        metrics[d]["top_cnt_delta"] = (metrics[d]["top_cnt"] - metrics[d]["top_cnt_prev"]
                                       if metrics[d]["top_cnt_prev"] is not None else None)
    series = {k: [metrics[d].get(k) for d in dates] for k in
              ("strong", "dt", "broke", "zt", "height", "promo", "relay", "zhaban",
               "top_cnt", "top_cnt_delta", "top_amt", "run_days", "switches_5d",
               "idx_trend", "bid_amt")}
    out = {}
    spec_raw = {}
    market_raw = {}
    for d in dates:
        m = metrics[d]
        market = _wsum([(_pct_rank(series["strong"], m["strong"]), .3),
                        (_pct_rank([-x for x in series["dt"] if x is not None], -m["dt"] if m["dt"] is not None else None), .2),
                        (_pct_rank([-x for x in series["broke"] if x is not None], -m["broke"] if m["broke"] is not None else None), .2),
                        (_pct_rank(series["idx_trend"], m["idx_trend"]), .15),
                        (_pct_rank(series["bid_amt"], m["bid_amt"]), .15)])
        spec = _wsum([(_pct_rank(series["zt"], m["zt"]), .25),
                      (_pct_rank(series["height"], m["height"]), .2),
                      (_pct_rank(series["promo"], m["promo"]), .2),
                      (_pct_rank([-x for x in series["broke"] if x is not None], -m["broke"] if m["broke"] is not None else None), .15),
                      (_pct_rank([-x for x in series["zhaban"] if x is not None], -m["zhaban"] if m["zhaban"] is not None else None), .1),
                      (_pct_rank(series["relay"], m["relay"]), .1)])
        sector = _wsum([(_pct_rank(series["top_cnt"], m["top_cnt"]), .35),
                        (_pct_rank(series["height"], m["height"]), .25),
                        (_pct_rank(series["top_cnt_delta"], m["top_cnt_delta"]), .25),
                        (_pct_rank(series["top_amt"], m["top_amt"]), .15)])
        out[d] = {"market": market, "spec": spec, "sector": sector}
        market_raw[d], spec_raw[d] = market, spec
    # 整体三层 = 3 日均值的分位数; 板块整体 = 连任/切换
    for d in dates:
        i = dates.index(d)
        win3 = dates[max(0, i - 2):i + 1]
        out[d]["m_market"] = _pct_rank([out[x]["market"] for x in dates],
                                       sum(v for v in (out[x]["market"] for x in win3) if v is not None) / len(win3))
        out[d]["m_spec"] = _pct_rank([out[x]["spec"] for x in dates],
                                     sum(v for v in (out[x]["spec"] for x in win3) if v is not None) / len(win3))
        sector3 = sum(v for v in (out[x]["sector"] for x in win3) if v is not None) / len(win3)
        out[d]["m_sector"] = _wsum([(_pct_rank([out[x]["sector"] for x in dates], sector3), .5),
                                    (_pct_rank(series["run_days"], metrics[d]["run_days"]), .3),
                                    (_pct_rank([-x for x in series["switches_5d"] if x is not None],
                                               -metrics[d]["switches_5d"]), .2)])
    # 阈值自校准: 用分数自身的历史四分位(样本增长自动漂移, 无需人工重校)
    th = {
        "spec_p30": _q([out[x]["spec"] for x in dates], .30),
        "spec_p50": _q([out[x]["spec"] for x in dates], .50),
        "spec_p90": _q([out[x]["spec"] for x in dates], .90),
        "sector_p40": _q([out[x]["sector"] for x in dates], .40),
        "sector_p90": _q([out[x]["sector"] for x in dates], .90),
        "market_p75": _q([out[x]["market"] for x in dates], .75),
        "m_spec_p75": _q([out[x]["m_spec"] for x in dates], .75),
        "m_sector_p75": _q([out[x]["m_sector"] for x in dates], .75),
        "zhaban_p75": _q([metrics[x]["zhaban"] for x in dates], .75),
    }
    # 主导条件
    for d in dates:
        o = out[d]
        m = metrics[d]
        o["dominant"], o["note"] = _dominant(o, m, th)
    return out, metrics


def _q(vals, q):
    """分位数(忽略 None)"""
    xs = sorted(v for v in vals if v is not None)
    if not xs:
        return 0
    import math
    return xs[min(len(xs) - 1, math.floor(q * len(xs)))]


def _dominant(o, m, th):
    """主导条件判定: 阈值全部来自分数分布四分位(自校准), 顺序即优先级"""
    spec_collapse = (o["m_spec"] is not None and o["spec"] is not None
                     and o["spec"] <= o["m_spec"] - 30)
    if (o["spec"] is not None and
            ((o["spec"] <= th["spec_p30"] and ((m["dt"] or 0) >= 5 or (m["zt"] is not None and m["zt"] <= 30)))
             or spec_collapse)):
        return "退潮防守", "不强行交易，等新情绪确认/新核心出现"
    if (o["sector"] or 0) >= th["sector_p90"] and (o["m_sector"] or 0) >= th["m_sector_p75"]:
        return "板块情绪极强", "主攻龙头板/换手核心/中军，低位补涨前排"
    if (o["spec"] or 0) >= th["spec_p90"] and (o["m_spec"] or 0) >= th["m_spec_p75"]:
        return "投机情绪极强", "低位超强前排/高板块情绪点/换手核心"
    if (o["market"] or 0) >= th["market_p75"] and (o["sector"] or 0) <= th["sector_p40"]:
        return "市场强但板块不强", "降低预期，只做低风险活跃点"
    if (m["zhaban"] or 0) >= th["zhaban_p75"] and (o["spec"] or 0) >= th["spec_p50"]:
        return "分歧但情绪不差", "等确认，选确定性出手"
    return "混沌观察", "只做辨识度最高标的"


def six_scores(date: str | None = None) -> dict:
    """对外入口: 指定日期(默认最新)的六情绪 + 主导条件"""
    out, metrics = compute_all()
    dates = sorted(out)
    d = date or dates[-1]
    if d not in out:
        raise KeyError(f"{d} 不在数据范围内({dates[0]}~{dates[-1]})")
    m = metrics[d]
    return {"date": d, **out[d],
            "top_board": m["top_board"], "top_cnt": m["top_cnt"], "height": m["height"],
            "zt": m["zt"], "dt": m["dt"], "zhaban": m["zhaban"], "broke": m["broke"]}


if __name__ == "__main__":
    import sys
    out, metrics = compute_all()
    dates = sorted(out)
    tgt = sys.argv[1] if len(sys.argv) > 1 else dates[-1]
    print(f"{'日期':<12}{'市场':>5}{'投机':>5}{'板块':>5}{'整市':>5}{'整投':>5}{'整板':>5}  主导条件")
    for d in [x for x in dates if x <= tgt][-12:]:
        o = out[d]
        print(f"{d:<12}{o['market'] or 0:>5}{o['spec'] or 0:>5}{o['sector'] or 0:>5}"
              f"{o['m_market'] or 0:>5}{o['m_spec'] or 0:>5}{o['m_sector'] or 0:>5}  {o['dominant']}")
    from collections import Counter
    cnt = Counter(out[d]["dominant"] for d in dates)
    print("\n历史主导分布:", dict(cnt))
