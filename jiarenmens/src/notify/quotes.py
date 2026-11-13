"""腾讯→新浪行情链 + 竞价成交额补算（AQ2=A / T2.1.2）。

从 ``scripts/notify_daily.py:102-169`` 与 ``scripts/export_json.py:1258-1271``
**逐行等价迁入**，不重写语义。删除 ``notify_daily.py`` / ``export_json.py`` 后，
``watchdog.py`` 与 ``auction_scan.py`` 仍可独立使用这两个能力。
"""
import json
import re
import urllib.request

from src.utils.market import market_prefix as _market

__all__ = ["fetch_quotes", "tencent_auction_amt"]


def _quotes_tencent(codes) -> dict:
    out = {}
    for i in range(0, len(codes), 30):
        symbols = ",".join(f"{_market(c)}{c}" for c in codes[i:i + 30])
        try:
            with urllib.request.urlopen(f"http://qt.gtimg.cn/q={symbols}", timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            m = re.match(r'v_(?:s[hz]|bj)(\d+)="(.*)"', line.strip())
            if not m:
                continue
            fields = m.group(2).split("~")
            if len(fields) <= 32:
                continue
            try:
                price = float(fields[3])
            except (ValueError, TypeError):
                price = None
            try:
                pct = float(fields[32].strip())
            except (ValueError, TypeError):
                pct = None
            if price is not None:
                out[m.group(1)] = {"price": price, "pct": pct}
    return out


def _quotes_sina(codes) -> dict:
    out = {}
    for i in range(0, len(codes), 30):
        symbols = ",".join(f"{_market(c)}{c}" for c in codes[i:i + 30])
        try:
            req = urllib.request.Request(
                f"http://hq.sinajs.cn/list={symbols}",
                headers={"Referer": "https://finance.sina.com.cn"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("gbk", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            m = re.search(r'hq_str_(?:s[hz]|bj)(\d+)="(.*)";', line.strip())
            if not m:
                continue
            fields = m.group(2).split(",")
            if len(fields) < 6:
                continue
            try:
                price, prev = float(fields[3]), float(fields[2])
            except (ValueError, TypeError):
                continue
            if price <= 0:
                continue
            out[m.group(1)] = {"price": price,
                               "pct": (price - prev) / prev * 100 if prev > 0 else None}
    return out


def fetch_quotes(codes) -> dict:
    if not codes:
        return {}
    out = _quotes_tencent(codes)
    missing = [c for c in codes if c not in out]
    if missing:
        out.update(_quotes_sina(missing))
    return out


def tencent_auction_amt(code: str):
    """腾讯分时首行 0930 行 = 09:25 集合竞价成交打印 → 竞价成交额(元), 取不到返回 None。
    口径同 scripts/lianban_bid_hs.py tencent_auction(09-03 双源验证); 分时接口只回当天,
    历史日期/盘外自然拿不到 → 调用方降级用 KPL 行。"""
    pfx = "sh" if code.startswith("6") else ("bj" if code[0] in "48" else "sz")
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={pfx}{code}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            rows = json.load(resp)["data"][pfx + code]["data"]["data"]
        first = rows[0].split()
        return float(first[3]) if first and first[0] == "0930" else None
    except Exception:
        return None
