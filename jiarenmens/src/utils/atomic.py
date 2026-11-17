"""可复用的原子写工具（F6 / AQ2=A 前置）。

从 ``scripts/export_json.py:30-42`` 的 ``_atomic_json`` / ``_atomic_text``
**逐行等价提取**（不是重写）：先写同目录 ``.tmp``，再 ``os.replace``。
导出/写盘中断不会留下被 ``git add -f`` 固化的截断文件。

现状(2026-11-17 复核)：``export_json.py`` 已随 P3 删除，本模块是**唯一**的原子写实现；
``player_snapshot.py`` / ``contract_probe.py`` / ``export_market.py`` 均已 rewire，
``watchdog.py`` / ``cycle_push.py`` 的去重状态也在 2026-11-17 起改为原子写。
"""
import json
import os
from pathlib import Path

__all__ = ["_atomic_json", "_atomic_text", "atomic_json", "atomic_text"]


def _atomic_json(path: Path, obj):
    """原子写 JSON: 先写同目录 .tmp 再 os.replace, 防进程中断留下截断文件被 git add -f 固化。"""
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _atomic_text(path: Path, text: str):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# 语义化别名，便于新代码按公开名字调用；实现与下划线版本完全相同（同一函数对象）。
atomic_json = _atomic_json
atomic_text = _atomic_text
