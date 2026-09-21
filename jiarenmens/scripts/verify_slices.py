"""校验分片导出：name_map 完整性 + changes_summary 结构自洽。

(2026-09-21 summary.json 停写后, 原第一块"每片字段与 summary.json 对拍"已随基准
一并移除; 分片正确性由 export_json 单一来源保证。)
"""
import json, sys
from pathlib import Path

base = Path(sys.argv[1] if len(sys.argv) > 1 else "stockboard-app/public/data/latest")

# name_map 必须覆盖 copy 当日所有被引用的名字
nm = json.loads((base / "name_map.json").read_text(encoding="utf-8"))
copy_d = json.loads((base / "copy.json").read_text(encoding="utf-8"))
names = set()
for sig in copy_d["copyTradeSignals"]["bs"]: names.update(sig["b"]); names.update(sig["sl"])
for sig in copy_d["copyTradeSignals"]["ch"]: names.update(sig["hd"])
for sig in copy_d["copyTradeSignals"]["sw"]: names.update(sig["sl"])
for a in copy_d["tradeAlerts"]: names.update(n for n, _ in a["players"])
for sc in copy_d["suspectedClears"]: names.add(sc["player_name"])
missing = {n for n in names if n not in nm}
assert not missing, f"name_map 缺少被引用名字: {missing}"

# changes_summary 结构自洽
cs = json.loads((base / "changes_summary.json").read_text(encoding="utf-8"))
for k in ("hasHistory", "yesterday", "today", "addedCount", "clearedCount", "changeCount"):
    assert k in cs, f"changes_summary.json 缺少字段 {k}"
if cs["hasHistory"]:
    assert cs["addedCount"] >= 0 and cs["clearedCount"] >= 0 and cs["changeCount"] >= 0
else:
    assert cs["addedCount"] == 0 and cs["clearedCount"] == 0 and cs["changeCount"] == 0

print("✅ 分片校验通过")
