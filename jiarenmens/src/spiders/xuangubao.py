"""选股宝(快讯) 炸板历史池: flash-api.xuangubao.com.cn

limit_up_broken?date=YYYY-MM-DD 支持任意历史日期(2026-09-05 实测验证):
口径 = 当日曾涨停(触板)但收盘未封住的股票, 带炸板次数/收盘涨幅/连板数/换手。
同源可用 pool_name: limit_up / limit_down / yesterday_limit_up (均支持 date 参数)。
"""
import json
import urllib.request


def fetch_broken_pool(date: str, timeout: int = 15) -> list[dict]:
    """拉取指定日期炸板池 → [{code,name,break_times,change_pct,turnover,height}]"""
    url = f"https://flash-api.xuangubao.com.cn/api/pool/detail?pool_name=limit_up_broken&date={date}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r).get("data") or []
    out = []
    for s in data:
        sym = str(s.get("symbol") or "")
        out.append({
            "code": sym.split(".")[0],
            "name": s.get("stock_chi_name") or "",
            "break_times": int(s.get("break_limit_up_times") or 0),
            "change_pct": round(float(s.get("change_percent") or 0) * 100, 2),
            "turnover": round(float(s.get("turnover_ratio") or 0) * 100, 2),
            "height": int(s.get("m_days_n_boards_boards") or 0),
        })
    return out
