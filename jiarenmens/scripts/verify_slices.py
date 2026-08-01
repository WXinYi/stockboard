"""校验分片导出：每片字段与 summary.json 一致 + name_map 完整性 + changes_summary 计数一致"""
import json, sys
from pathlib import Path

base = Path(sys.argv[1] if len(sys.argv) > 1 else "stockboard-app/public/data/latest")
s = json.loads((base / "summary.json").read_text(encoding="utf-8"))

checks = {
    "core.json":     ["date", "crawl_time", "qualityPlayerCount", "tradedPlayerIds", "fullRankCount"],
    "copy.json":     ["copyTradeSignals", "tradeAlerts", "suspectedClears"],
    "stocks.json":   ["stockStats"],
    "trades.json":   ["tradeConsensus"],
    "sectors.json":  ["sectorStats"],
    "compare.json":  ["stockCompare"],
    "overview.json": ["positionDist", "profitDist"],
}
for fname, keys in checks.items():
    d = json.loads((base / fname).read_text(encoding="utf-8"))
    for k in keys:
        assert d[k] == s[k], f"{fname}.{k} 与 summary.json 不一致"

# name_map 必须覆盖 copy/trades 当日所有被引用的名字
nm = json.loads((base / "name_map.json").read_text(encoding="utf-8"))
copy_d = json.loads((base / "copy.json").read_text(encoding="utf-8"))
trades_d = json.loads((base / "trades.json").read_text(encoding="utf-8"))
names = set()
for sig in copy_d["copyTradeSignals"]["bs"]: names.update(sig["b"]); names.update(sig["sl"])
for sig in copy_d["copyTradeSignals"]["ch"]: names.update(sig["hd"])
for sig in copy_d["copyTradeSignals"]["sw"]: names.update(sig["sl"])
for a in copy_d["tradeAlerts"]: names.update(n for n, _ in a["players"])
for sc in copy_d["suspectedClears"]: names.add(sc["player_name"])
for tc in trades_d["tradeConsensus"]: names.update(tc["bp"]); names.update(tc["sp"])
missing = {n for n in names if n not in nm}
assert not missing, f"name_map 缺少被引用名字: {missing}"

# changes_summary 计数必须与 changes.json 一致
cs = json.loads((base / "changes_summary.json").read_text(encoding="utf-8"))
ch = json.loads((base / "changes.json").read_text(encoding="utf-8"))
if ch["changes"] is not None:
    assert cs["addedCount"] == len(ch["changes"]["added"]), "addedCount 不一致"
    assert cs["clearedCount"] == len(ch["changes"]["cleared"]), "clearedCount 不一致"
    assert cs["changeCount"] == len(ch["changes"]["changes"]), "changeCount 不一致"
else:
    assert cs["hasHistory"] is False

print("✅ 分片校验通过")
