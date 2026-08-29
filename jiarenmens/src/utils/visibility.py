#!/usr/bin/env python3
"""选手组合可见性状态(共享小模块)

东财组合接口对"已隐藏/已删除"的选手返回空, 此类选手无法跟单 → 推送侧自动跳过;
哪天恢复公开(detail 重新有数据), 状态自动翻转, 推送与特别关注列表自动回归。

状态文件: jiarenmens/data/player_visibility.json
  {"hidden": {"zh_id": "首次发现隐藏的日期"}, "updated_at": "..."}
watched_flash.py 每天早上探测全量关注选手并更新本文件;
notify_daily.py / watched_flash.py 读它决定是否跳过。文件随 crawl.yml「提交数据」入库持久化。
"""
import json
from datetime import datetime
from pathlib import Path

STATE = Path(__file__).resolve().parents[2] / "jiarenmens" / "data" / "player_visibility.json"


def load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"hidden": {}}


def is_hidden(zh_id: str, state: dict | None = None) -> bool:
    st = state if state is not None else load()
    return zh_id in st.get("hidden", {})


def update(results: dict) -> dict:
    """results: {zh_id: detail is None(=hidden)} → 合并写回, 返回最新状态"""
    st = load()
    hidden = st.get("hidden", {})
    today = datetime.now().strftime("%Y-%m-%d")
    for zh, is_none in results.items():
        if is_none:
            hidden.setdefault(zh, today)
        else:
            hidden.pop(zh, None)   # 恢复公开 → 自动回归
    st = {"hidden": hidden, "updated_at": today}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    return st
