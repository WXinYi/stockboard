#!/usr/bin/env python3
"""A股市场前缀映射(腾讯/新浪行情符号) — 唯一实现, 勿在脚本里另写 _market。

历史教训(2026-09-05): 三处拷贝(_market×3)都把北交所新段 920xxx 当沪市
(9→sh), 行情/K线全部拉空。统一后: 92→bj 必须先于 9→sh 判断。

仅支持 A 股正股; 转债(1xxxxx)/ETF(5xxxxx) 由调用方按需过滤或自行处理。
"""
BJ_PREFIXES = ("4", "8")  # 北交所老段(43/83/87等)


def market_prefix(code: str) -> str:
    """6位A股代码 → 行情符号前缀 sh/sz/bj (腾讯 qt.gtimg.cn / fqkline 与新浪 hq.sinajs.cn 通用)"""
    if code.startswith("92"):
        return "bj"
    if code.startswith(("6", "5", "9")):
        return "sh"
    if code.startswith(BJ_PREFIXES):
        return "bj"
    return "sz"
