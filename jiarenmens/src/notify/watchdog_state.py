"""盯盘当日已推状态读取（AQ2=A / T2.1.2）。

从 ``scripts/notify_daily.py:42,71-85`` 逐行等价迁入。日报读它跳过已即时推送的
操作 → 一条操作只响一次铃；读不到 = watchdog 失灵 → 日报全量兜底。
"""
import json
from pathlib import Path

# 本文件在 jiarenmens/src/notify/ 下，parents[3] = 仓库根（原 notify_daily.py 用 parents[2]）
REPO_ROOT = Path(__file__).resolve().parents[3]
WATCHDOG_STATE = REPO_ROOT / "jiarenmens" / "data" / ".watchdog_state.json"


def load_watchdog_pushed(date_str: str) -> set:
    """盯盘 watchdog 当日已推送的 (zh, sc, 方向) 集合(来自共享缓存恢复的状态文件)。
    读不到或不是当天 → 空集: 日报回退为全量推送(watchdog 失灵时的兜底语义)。"""
    try:
        st = json.loads(WATCHDOG_STATE.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(st, dict) or st.get("date") != date_str:
        return set()
    out = set()
    for zh, by_code in (st.get("pushed") or {}).items():
        for sc, by_dr in (by_code or {}).items():
            for dr in (by_dr or {}):
                out.add((str(zh), str(sc), str(dr)))
    return out
