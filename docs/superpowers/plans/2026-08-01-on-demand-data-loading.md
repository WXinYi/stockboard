# 按需数据加载优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把首屏 `/copy` 从无条件下载 ~3MB（summary.json 707K + players_index.json 1.1M + changes.json 609K）降到按需加载，各页面只拉自己消费的分片文件，首屏降到 ~300KB；同时每个页面支持手动刷新最新数据（移动端下拉手势 + 桌面顶栏按钮），并修复现有"数据已更新·点击刷新"缓存不失效的 bug。

**Architecture:** 后端 `export_json.py` 把 summary.json 拆成按页切片（core/copy/stocks/trades/sectors/compare/overview/name_map）并新增 changes_summary.json；前端 loader.js 增加分片加载函数与缓存失效，useData/useHistory 重构为按片 ref，App.vue 用路由映射触发懒加载。分片在 loader 层做模块级缓存，KeepAlive 切 Tab 天然不重复请求。新增 PullToRefresh 组件（包住 router-view，移动端下拉手势）+ 顶栏 ⟳ 按钮（桌面入口），统一走 `refreshData()`：先 `clearDataCache()` 清缓存再重载 core + 当前路由分片，刷新期间不闪全屏 loading。

**Tech Stack:** Python 3.11 (export_json.py)、Vue 3 + Vite (stockboard-app)、vue-router hash 模式。无前端测试框架，验证用 Python 校验脚本 + `npm run build` + dev server 网络清单。

## Global Constraints

- **禁止直接 push**：所有改动本地验证通过后才允许提交；push 需用户明确授权（见 memory/workflow-rules）
- npm 命令必须在 `stockboard-app/` 目录下执行（见 memory/common-mistakes）
- 前端每个 `fetch*` 函数必须保持模块级缓存；`clearDataCache()` 一次性清空全部，用于"数据已更新"刷新
- 组件 inject 的数据名（`copyTradeSignals`/`tradeAlerts`/`stockStats`/`tradeConsensus` 等）一律不变，只改提供方内部实现，避免组件大面积改动
- summary.json 保留全量输出（作为调试参照 + verify 基准），但**前端不再 fetch 它**
- Python 校验脚本是唯一自动化检查；前端无单测，靠 build + 手动清单
- 下拉刷新（手势/按钮）共用同一个 `refreshData()`：先 `clearDataCache()` 再重载 core + 当前路由分片；刷新期间**不**触发全屏 loading（用 PullToRefresh 的顶部 spinner 表示）
- 下拉手势只在 `window.scrollY === 0`（页面在顶部）时接管 touch 事件，避免与页面/表格内部滚动冲突

---

### Task 1: 后端拆分 summary.json → 分片 + name_map 子集 + changes_summary.json

**Files:**
- Modify: `jiarenmens/scripts/export_json.py:649-668`（构建 summary 段）
- Modify: `jiarenmens/scripts/export_json.py:814-876`（写文件段）
- Modify: `jiarenmens/scripts/export_json.py:1-12`（docstring 注释更新）
- Create: `jiarenmens/scripts/verify_slices.py`

**Interfaces:**
- Consumes: 既有变量 `copy_trade_signals`(300)、`stock_stats`(201)、`trade_consensus`(341)、`sector_stats`(379)、`position_dist`(388)、`profit_bins`(467)、`stock_compare`(553)、`trade_alerts`(422)、`suspected_clears`(456)、`traded_player_ids`(644)、`all_signals`(278)、`players_flat`(176)、`changes_data`(617)
- Produces: `latest/core.json`、`latest/copy.json`、`latest/stocks.json`、`latest/trades.json`、`latest/sectors.json`、`latest/compare.json`、`latest/overview.json`、`latest/name_map.json`、`latest/changes_summary.json`，字段与 Task 2 loader 函数一一对应

- [ ] **Step 1: 写校验脚本（先定义"正确"的判定标准）**

Create `jiarenmens/scripts/verify_slices.py`：

```python
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
```

- [ ] **Step 2: 重构 summary 构建段（export_json.py:649-668）**

替换原来的 `summary` 字典构造：

```python
    # ── 12. 构建 summary 分片（按页面切片，前端按需加载）──
    # summary.json 仍全量输出作为调试参照 + verify 基准
    summary_slices = {
        "core": {
            "date": crawl_date,
            "crawl_time": "",  # 下面填入
            "qualityPlayerCount": len(quality_ids),
            "tradedPlayerIds": traded_player_ids,
            "fullRankCount": sum(1 for p in players_flat if len(p.get("ranks") or []) >= 5),
        },
        "copy": {
            "copyTradeSignals": copy_trade_signals,
            "tradeAlerts": trade_alerts,
            "suspectedClears": suspected_clears,
        },
        "stocks": {"stockStats": stock_stats},
        "trades": {"tradeConsensus": trade_consensus},
        "sectors": {"sectorStats": sector_stats},
        "compare": {"stockCompare": stock_compare},
        "overview": {"positionDist": position_dist, "profitDist": profit_bins},
    }

    # name_map 只保留当日实际被引用的名字（copy 信号 + alerts + consensus 里出现的名字）
    referenced_names = set()
    for sig in all_signals:
        referenced_names.update(sig.get("holders") or [])
        referenced_names.update(sig.get("buyer_names") or [])
        referenced_names.update(sig.get("seller_names") or [])
    for alert in trade_alerts:
        for name, _pid in alert.get("players", []):
            referenced_names.add(name)
    for sc in suspected_clears:
        referenced_names.add(sc["player_name"])
    for tc in trade_consensus:
        referenced_names.update(tc["bp"])
        referenced_names.update(tc["sp"])
    name_map = {p["name"]: p["id"] for p in players_flat
                if p["name"] and p["name"] in referenced_names}

    # 全量参照文件（字段=各分片并集 + 精简后的 name_map）
    summary = {**summary_slices["core"], **summary_slices["copy"],
               **summary_slices["stocks"], **summary_slices["trades"],
               **summary_slices["sectors"], **summary_slices["compare"],
               **summary_slices["overview"], "playerNameMap": name_map}
```

注意：`summary_slices["core"]["crawl_time"]` 在原文件 line 802-808 读取 `crawl_start.txt` 后填入，需把 `summary["crawl_time"] = crawl_time` 改为 `summary_slices["core"]["crawl_time"] = crawl_time`。

- [ ] **Step 3: 新增写文件段（export_json.py 写 summary.json 之后）**

在现有 `summary.json` 写文件（line 830-831）后面追加分片写入：

```python
    # 分片文件（前端按需加载）
    with open(latest_dir / "core.json", "w", encoding="utf-8") as f:
        json.dump(summary_slices["core"], f, ensure_ascii=False, separators=(",", ":"))
    for slice_name in ("copy", "stocks", "trades", "sectors", "compare", "overview"):
        with open(latest_dir / f"{slice_name}.json", "w", encoding="utf-8") as f:
            json.dump(summary_slices[slice_name], f, ensure_ascii=False, separators=(",", ":"))

    # name_map.json（只含当日被引用名字）
    with open(latest_dir / "name_map.json", "w", encoding="utf-8") as f:
        json.dump(name_map, f, ensure_ascii=False, separators=(",", ":"))

    # changes_summary.json（/copy 摘要栏用，不含明细）
    if changes_data:
        changes_summary = {
            "hasHistory": True,
            "yesterday": changes_data["yesterday"],
            "today": changes_data["today"],
            "addedCount": len(changes_data["added"]),
            "clearedCount": len(changes_data["cleared"]),
            "changeCount": len(changes_data["changes"]),
        }
    else:
        changes_summary = {"hasHistory": False, "yesterday": "", "today": "",
                           "addedCount": 0, "clearedCount": 0, "changeCount": 0}
    with open(latest_dir / "changes_summary.json", "w", encoding="utf-8") as f:
        json.dump(changes_summary, f, ensure_ascii=False, separators=(",", ":"))
```

同时更新文件头 docstring（line 3-7），把 `latest/summary.json — 所有 Tab 聚合数据` 改为说明分片结构。

- [ ] **Step 4: 运行 export + 校验**

```bash
cd /Users/xywang/stockboard/jiarenmens
python scripts/export_json.py --out ../stockboard-app/public/data
python scripts/verify_slices.py ../stockboard-app/public/data/latest
```

Expected: `✅ 分片校验通过`。若输出缺失名字/计数不一致，说明子集构造有遗漏，需修正 Step 2 再跑。

- [ ] **Step 5: 确认新文件尺寸与体积收益**

```bash
ls -lh ../stockboard-app/public/data/latest/*.json | sort -k5 -h
```

Expected: core ~20KB、copy ~230KB、name_map 显著小于原 203KB（预计 50-80KB）、changes_summary ~1KB。

- [ ] **Step 6: Commit**

```bash
cd /Users/xywang/stockboard
git add jiarenmens/scripts/export_json.py jiarenmens/scripts/verify_slices.py
git commit -m "✨ 后端: summary 拆分为按页分片 + name_map 子集 + changes_summary"
```

---

### Task 2: 前端 loader.js — 分片加载函数 + 统一缓存 + clearDataCache

**Files:**
- Modify: `stockboard-app/src/data/loader.js`（整文件 34 行重写）

**Interfaces:**
- Consumes: 无（纯静态函数）
- Produces: `fetchCore()`、`fetchCopy()`、`fetchStocks()`、`fetchTrades()`、`fetchSectors()`、`fetchCompare()`、`fetchOverview()`、`fetchNameMap()`、`fetchChangesSummary()`、`clearDataCache()`；保留 `fetchPlayersIndex()`、`fetchChanges()`、`fetchPlayerDetail()`、`fetchPlayerHistory()`、`fetchSummary()`（兼容保留，前端不再调用 summary）
- 约定：`fetchPlayerDetail/fetchPlayerHistory` **不走缓存**（PlayerDetail 每次进入都要新鲜数据）

- [ ] **Step 1: 重写 loader.js**

```js
const BASE = import.meta.env.BASE_URL

// 统一模块级缓存：按请求路径缓存，clearDataCache() 一次性清空
const _cache = {}

async function getJson(path) {
  if (_cache[path]) return _cache[path]
  const resp = await fetch(`${BASE}${path}`)
  const data = await resp.json()
  _cache[path] = data
  return data
}

export function clearDataCache() {
  for (const k of Object.keys(_cache)) delete _cache[k]
}

// 分片（按需加载）
export const fetchCore          = () => getJson('data/latest/core.json')
export const fetchCopy          = () => getJson('data/latest/copy.json')
export const fetchStocks        = () => getJson('data/latest/stocks.json')
export const fetchTrades        = () => getJson('data/latest/trades.json')
export const fetchSectors       = () => getJson('data/latest/sectors.json')
export const fetchCompare       = () => getJson('data/latest/compare.json')
export const fetchOverview      = () => getJson('data/latest/overview.json')
export const fetchNameMap       = () => getJson('data/latest/name_map.json')
export const fetchChangesSummary = () => getJson('data/latest/changes_summary.json')

// 全量（路由级懒加载）
export const fetchPlayersIndex  = () => getJson('data/latest/players_index.json')
export const fetchChanges       = () => getJson('data/latest/changes.json')

// 按需新鲜数据（PlayerDetail，不缓存）
export const fetchPlayerDetail  = (zhId) => fetch(`${BASE}data/latest/players/${zhId}.json`).then((r) => r.json())
export const fetchPlayerHistory = (zhId) => fetch(`${BASE}data/history/${zhId}.json`).then((r) => r.json())

// 兼容保留（新架构下前端不再调用）
export const fetchSummary = () => getJson('data/latest/summary.json')
```

- [ ] **Step 2: build 验证**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build
```

Expected: 构建成功，无未定义导入报错（此时 useData 仍 import 旧 `fetchSummary/fetchPlayersIndex`，暂不报错）。

- [ ] **Step 3: Commit**

```bash
cd /Users/xywang/stockboard
git add stockboard-app/src/data/loader.js
git commit -m "✨ 前端: loader 分片加载 + 统一缓存 + clearDataCache"
```

---

### Task 3: useData.js — 重构为按片 ref + ensureSlices

**Files:**
- Modify: `stockboard-app/src/composables/useData.js`

**Interfaces:**
- Consumes: Task 2 的 `fetchCore/fetchCopy/fetchStocks/fetchTrades/fetchSectors/fetchCompare/fetchOverview/fetchNameMap/fetchPlayersIndex`
- Produces（保持既有导出名不变，组件零改动）:
  - computeds: `currentDate`、`crawlTime`、`qualityPlayerCount`、`tradedPlayerIds`、`copyTradeSignals`、`tradeAlerts`、`suspectedClears`、`stockStats`、`tradeConsensus`、`sectorStats`、`stockCompare`、`positionDist`、`profitDist`、`playerNameMap`、`allPlayers`、`sortedPlayers`、`fullRankPlayers`、`playerLookup`、`playerStyles`
  - loading: `loading`（对象，`loading.core/copy/stocks/trades/sectors/compare/overview/nameMap/playersIndex`）
  - 方法: `ensureSlices(names)`、`loadData()`（仅 core）、`isQuality`

- [ ] **Step 1: 重构数据源为分片 ref**

替换 `_summary`/`_playersIndex` 两个 ref 为分片 ref 表，并重写直接取自 summary 的 computeds（`currentDate`、`crawlTime`、`qualityPlayerCount`、`tradedPlayerIds`、`copyTradeSignals`、`tradeAlerts`、`suspectedClears`、`stockStats`、`tradeConsensus`、`sectorStats`、`stockCompare`、`positionDist`、`profitDist`、`playerNameMap`）：

```js
import { ref, computed } from 'vue'
import {
  fetchCore, fetchCopy, fetchStocks, fetchTrades, fetchSectors,
  fetchCompare, fetchOverview, fetchNameMap, fetchPlayersIndex,
} from '../data/loader.js'

const WATCHED_IDS = new Set(['900240956', '900354116', '900438148', '900376763', '900013608', '900429191', '900369020', '900223455'])

// 分片 ref 表 + 加载器表（ensureSlices 用）
const SLICE_REF = {
  core: 'core', copy: 'copy', stocks: 'stocks', trades: 'trades',
  sectors: 'sectors', compare: 'compare', overview: 'overview',
  nameMap: 'nameMap', playersIndex: 'playersIndex',
}
const SLICE_LOADER = {
  core: fetchCore, copy: fetchCopy, stocks: fetchStocks, trades: fetchTrades,
  sectors: fetchSectors, compare: fetchCompare, overview: fetchOverview,
  nameMap: fetchNameMap, playersIndex: fetchPlayersIndex,
}
```

在 `useData()` 内部：

```js
export function useData() {
  const loading = ref({
    core: false, copy: false, stocks: false, trades: false,
    sectors: false, compare: false, overview: false,
    nameMap: false, playersIndex: false,
  })
  const slices = {
    core: ref(null), copy: ref(null), stocks: ref(null), trades: ref(null),
    sectors: ref(null), compare: ref(null), overview: ref(null),
    nameMap: ref(null), playersIndex: ref(null),
  }

  // ══ 来自 core.json ══
  const currentDate = computed(() => slices.core.value?.date || '')
  const crawlTime = computed(() => slices.core.value?.crawl_time || '')
  const qualityPlayerCount = computed(() => slices.core.value?.qualityPlayerCount || 0)
  const tradedPlayerIds = computed(() => new Set(slices.core.value?.tradedPlayerIds || []))

  // ══ 来自 copy.json ══
  const copyTradeSignals = computed(() => slices.copy.value?.copyTradeSignals || { bs: [], ch: [], sw: [], hq: [] })
  const tradeAlerts = computed(() => slices.copy.value?.tradeAlerts || [])
  const suspectedClears = computed(() => slices.copy.value?.suspectedClears || [])

  // ══ 来自 stocks.json / trades.json / sectors.json ══
  const stockStats = computed(() => slices.stocks.value?.stockStats || [])
  const tradeConsensus = computed(() => slices.trades.value?.tradeConsensus || [])
  const sectorStats = computed(() => slices.sectors.value?.sectorStats || [])

  // ══ 来自 compare.json ══
  const stockCompare = computed(() => slices.compare.value?.stockCompare || { concentration: [], divergence: [], qualityCount: 0 })

  // ══ 来自 overview.json ══
  const positionDist = computed(() => slices.overview.value?.positionDist || {})
  const profitDist = computed(() => slices.overview.value?.profitDist || {})

  // ══ 来自 name_map.json + players_index 兜底 ══
  const playerNameMap = computed(() => {
    const map = { ...(slices.nameMap.value || {}) }
    if (slices.playersIndex.value) {
      for (const p of slices.playersIndex.value) map[p[0]] = p[0]
    }
    return map
  })
```

保留 `isQuality`、`normalize`、`allPlayers`、`sortedPlayers`、`fullRankPlayers`、`playerStyles`、`playerLookup` 原样（它们只依赖 `slices.playersIndex`，把原 `_playersIndex` 引用改为 `slices.playersIndex`）。

- [ ] **Step 2: 新增 ensureSlices + 改造 loadData**

```js
  async function ensureSlices(names) {
    await Promise.all([...new Set(names)].map(async (name) => {
      const r = slices[name]
      if (!r || r.value) return
      loading.value[name] = true
      try { r.value = await SLICE_LOADER[name]() }
      finally { loading.value[name] = false }
    }))
  }

  // App 挂载时只等 core（~20KB），其余分片由路由触发
  async function loadData() {
    await ensureSlices(['core'])
  }
```

- [ ] **Step 3: 更新 return**

返回值改为：

```js
  return {
    currentDate, loading, crawlTime,
    sortedPlayers, stockStats, tradeConsensus, positionDist, profitDist,
    sortKey, qualityOnly, isQuality,
    playerStyles, sectorStats, fullRankPlayers, copyTradeSignals, stockCompare,
    qualityPlayerCount, tradedPlayerIds, tradeAlerts, suspectedClears, playerNameMap,
    playerLookup,
    ensureSlices, loadData,
  }
```

- [ ] **Step 4: build 验证**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build
```

Expected: 构建成功（此时 useHistory/App.vue 仍调旧 API，暂不报错）。

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard
git add stockboard-app/src/composables/useData.js
git commit -m "✨ 前端: useData 重构为分片 ref + ensureSlices 按需加载"
```

---

### Task 4: useHistory.js — 拆分 changes summary / full

**Files:**
- Modify: `stockboard-app/src/composables/useHistory.js`
- Modify: `stockboard-app/src/components/CopyTradeTab.vue:10,53-56`（改用 changesSummary）

**Interfaces:**
- Consumes: Task 2 的 `fetchChangesSummary`、`fetchChanges`
- Produces: `changesSummary`（/copy 摘要栏）、`positionChanges`（/tracking 完整）、`loadChangesSummary()`、`loadChanges()`；保留 `getPlayerHistory/loadPlayerHistory`

- [ ] **Step 1: 拆分加载函数**

替换 `useHistory.js` 的数据加载段：

```js
import { ref, computed } from 'vue'
import { fetchChanges, fetchChangesSummary, fetchPlayerHistory } from '../data/loader.js'

export function useHistory() {
  const historyLoaded = ref(false)
  const dateList = ref([])
  const changesData = ref(null)      // 完整 changes（/tracking）
  const changesSummary = ref(null)   // 摘要 counts（/copy）
  const alerts = ref({ highByStock: [], mid: [], totalClear: 0 })

  const positionChanges = computed(() => changesData.value || { hasHistory: false, changes: [] })

  // /copy 用：{ hasHistory, today, yesterday, addedCount, clearedCount, changeCount }
  async function loadChangesSummary() {
    const data = await fetchChangesSummary()
    changesSummary.value = data
    dateList.value = [data.yesterday, data.today].filter(Boolean)
  }

  // /tracking 用：完整 { changes, alerts }
  async function loadChanges() {
    const data = await fetchChanges()
    changesData.value = data.changes
    alerts.value = data.alerts || { highByStock: [], mid: [], totalClear: 0 }
    historyLoaded.value = true
  }
```

保留 `playerHistoryCache/getPlayerHistory/loadPlayerHistory` 原样。return 改为：`historyLoaded, dateList, positionChanges, changesSummary, alerts, getPlayerHistory, loadPlayerHistory, loadChangesSummary, loadChanges`。

- [ ] **Step 2: 改 CopyTradeTab 消费 changesSummary**

`CopyTradeTab.vue` line 10：`const { positionChanges: posCh } = inject('stockHistory')` → `const { changesSummary: posCh } = inject('stockHistory')`。

模板 line 53-56 摘要栏改为计数：

```html
<div v-if="posCh && posCh.hasHistory" class="summary-bar">
  {{ posCh.today }} · +{{ posCh.addedCount }}新进 -{{ posCh.clearedCount }}清仓 {{ posCh.changeCount }}笔变动
</div>
```

（`v-else` 分支保持不变；`posCh` 初始为 null，需用 `posCh &&` 守卫。）

- [ ] **Step 3: build 验证**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build
```

Expected: 构建成功，无未定义变量报错。

- [ ] **Step 4: Commit**

```bash
cd /Users/xywang/stockboard
git add stockboard-app/src/composables/useHistory.js stockboard-app/src/components/CopyTradeTab.vue
git commit -m "✨ 前端: useHistory 拆分 changesSummary/完整 changes"
```

---

### Task 5: App.vue — 路由级懒加载 + 数据刷新修复

**Files:**
- Modify: `stockboard-app/src/App.vue`

**Interfaces:**
- Consumes: `stockData.ensureSlices/loadData/loading/fullRankPlayers`、`stockHistory.loadChangesSummary/loadChanges`、`loader.clearDataCache`
- Produces: 路由→分片映射表 `ROUTE_SLICES`、`ensureRoute()`、刷新逻辑 `refreshData()`

- [ ] **Step 1: 定义路由→分片映射 + ensureRoute**

替换 `App.vue` 的 `<script setup>` 数据加载相关代码：

```js
import { clearDataCache } from './data/loader.js'

const { currentDate, loading, fullRankPlayers, crawlTime, loadData, ensureSlices } = stockData
const { loadChangesSummary, loadChanges } = stockHistory
const { updateAvailable, initCheck, dismiss } = useDataRefresh()

// 路由 → 需要的分片（data 来自 useData，history 来自 useHistory）
const ROUTE_SLICES = {
  '/copy':     { data: ['copy', 'nameMap'],            history: 'summary' },
  '/overview': { data: ['overview', 'stocks', 'trades', 'playersIndex'], history: null },
  '/rankings': { data: ['playersIndex'],               history: null },
  '/stocks':   { data: ['stocks', 'playersIndex'],     history: null },
  '/sectors':  { data: ['sectors'],                    history: null },
  '/trades':   { data: ['trades', 'nameMap'],          history: null },
  '/compare':  { data: ['compare'],                    history: null },
  '/tracking': { data: [],                             history: 'full' },
  '/player':   { data: ['playersIndex'],               history: null },
}

async function ensureRoute() {
  const path = route.path
  const key = path.startsWith('/player/') ? '/player' : path
  const m = ROUTE_SLICES[key] || { data: [], history: null }
  await Promise.all([
    ensureSlices(m.data),
    m.history === 'summary' ? loadChangesSummary()
      : m.history === 'full' ? loadChanges() : Promise.resolve(),
  ])
}

watch(() => route.path, () => { ensureRoute() })
```

- [ ] **Step 2: 改造挂载 + 首屏 gating + 刷新**

```js
onMounted(async () => {
  await loadData()          // 只等 core（~20KB），首屏尽快渲染
  ensureRoute()             // 当前 Tab 分片，不阻塞首屏
  initCheck()
})

// 首屏 gating：只等 core，不再等 players_index
// ⚠️ loading 是 ref，脚本内必须写 loading.value.core（Task 6 会在此基础上排除 refreshing）
const initialLoading = computed(() => loading.value.core && !isPlayerDetail.value)

// 数据更新 → 清缓存后重载 core + 当前路由分片（修复旧代码缓存不失效的 bug）
function refreshData() {
  dismiss()
  clearDataCache()
  loadData()
  ensureRoute()
}
```

模板中 banner 点击事件改为 `@click="refreshData()"`。

- [ ] **Step 3: 处理头部"五榜"角标延迟**

`fullRankPlayers` 依赖 players_index，现为懒加载。落地 `/copy` 时角标不显示属预期（进入 `/rankings` 等页后出现）。在 `App.vue` 模板角标处加注释说明：

```html
<!-- 依赖 players_index 懒加载：首屏 /copy 不显示，进入榜单/重仓等页后出现 -->
<span v-if="fullRankPlayers.length" class="header-badge">{{ fullRankPlayers.length }}人五榜</span>
```

- [ ] **Step 4: 新增路由级骨架屏（可选打磨）**

为减少"分片到达前空态闪现"，加一个轻量 overlay：

```js
const routeLoading = computed(() => {
  const key = route.path.startsWith('/player/') ? '/player' : route.path
  const m = ROUTE_SLICES[key] || { data: [] }
  return m.data.some((k) => loading.value[k])
})
```

模板 `<main>` 内：`<div v-if="routeLoading" class="loading-view">…</div><router-view v-else …>`（复用现有 skeleton 样式；分片缓存后再次进入不触发）。

- [ ] **Step 5: build 验证**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build
```

Expected: 构建成功。

- [ ] **Step 6: Commit**

```bash
cd /Users/xywang/stockboard
git add stockboard-app/src/App.vue
git commit -m "✨ 前端: 路由级懒加载 + 数据刷新修复"
```

---

### Task 6: 每页下拉刷新 — PullToRefresh 组件 + 顶栏按钮

**Files:**
- Create: `stockboard-app/src/components/PullToRefresh.vue`
- Modify: `stockboard-app/src/App.vue`（顶栏按钮 + `refreshing` 状态 + `refreshData` 异步化 + loading gating）
- Modify: `stockboard-app/src/style.css`（追加 `.ptr*` / `.refresh-btn` 样式）

**Interfaces:**
- Consumes: Task 5 的 `refreshData()`、`clearDataCache()`、`loadData()`、`ensureRoute()`、`dismiss()`；Task 3 的 `loading`（ref 对象）
- Produces: 组件 `PullToRefresh`（`props: refreshing`，`emit: ['refresh']`，slot 包内容）；App.vue 的 `refreshing` ref、async `refreshData()`；顶栏按钮 `.refresh-btn`（桌面入口，移动端手势由组件接管）
- 注意：Task 5 已把 `loading.core` 修正为 `loading.value.core`（ref 取值），本任务在其基础上把 `initialLoading` 扩展为排除 `refreshing`，避免刷新时闪全屏 loading

- [ ] **Step 1: 新建 PullToRefresh.vue**

```vue
<script setup>
import { ref } from 'vue'

const props = defineProps({ refreshing: { type: Boolean, default: false } })
const emit = defineEmits(['refresh'])

const THRESHOLD = 60      // 触发刷新的下拉距离
const MAX_PULL = 110      // 阻尼后的最大下拉距离
const RESISTANCE = 0.4    // 阻尼系数：拉 100px 手指 ≈ 40px 位移
const INDICATOR_H = 44    // 指示器展示高度（px）

const pull = ref(0)
let startY = 0
let pulling = false

function onTouchStart(e) {
  if (window.scrollY > 0 || props.refreshing) return   // 页面在顶部才接管
  startY = e.touches[0].clientY
  pulling = true
}

function onTouchMove(e) {
  if (!pulling) return
  const dy = e.touches[0].clientY - startY
  if (dy <= 0) { pull.value = 0; return }              // 向上滑 → 放行原生滚动
  pull.value = Math.min(MAX_PULL, dy * RESISTANCE)
  if (pull.value > 0) e.preventDefault()               // 向下拉 → 拦截页面滚动
}

function onTouchEnd() {
  if (!pulling) return
  pulling = false
  if (pull.value >= THRESHOLD) emit('refresh')
  pull.value = 0
}
</script>

<template>
  <div class="ptr"
       @touchstart="onTouchStart" @touchmove="onTouchMove"
       @touchend="onTouchEnd" @touchcancel="onTouchEnd">
    <div class="ptr-indicator" :class="{ show: pull > 0 || refreshing }">
      <span v-if="refreshing" class="ptr-spinner"></span>
      <span v-else>{{ pull >= THRESHOLD ? '释放刷新' : '下拉刷新' }}</span>
    </div>
    <div class="ptr-content" :style="pull ? { transform: `translateY(${pull}px)` } : null">
      <slot />
    </div>
  </div>
</template>
```

要点：
- `window.scrollY === 0` 判定"在顶部"，避免和页面滚动打架；向下拖时 `preventDefault()` 阻止原生滚动
- `refreshing` 为 true 时展示 spinner（由 App.vue 传入）；触发阈值内展示"下拉刷新/释放刷新"文字
- 拖拽用阻尼系数让"拉得越远越费力"，松手未过阈值弹回（transform 清除）

- [ ] **Step 2: 追加 CSS（style.css 末尾）**

```css
/* ---- Pull-to-Refresh ---- */
.ptr { position: relative; min-height: 100%; }
.ptr-indicator {
  position: absolute; top: 0; left: 0; right: 0; height: 44px;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  font-size: 12px; font-weight: 420; color: #5b6daa;
  opacity: 0; transform: translateY(-44px); transition: all .25s ease;
}
.ptr-indicator.show { opacity: 1; transform: translateY(0); }
.ptr-spinner {
  width: 16px; height: 16px; border: 2px solid rgba(107,125,179,.15);
  border-top-color: rgba(107,125,179,.5); border-radius: 50%;
  animation: spin .7s linear infinite;
}
.ptr-content { transition: transform .25s ease; }
@media (hover: none) {
  /* 触屏设备：下拉时内容跟手，去掉过渡延迟 */
  .ptr:active .ptr-content { transition: none; }
}
.refresh-btn {
  width: 30px; height: 30px; padding: 0; line-height: 30px; text-align: center;
  background: rgba(107,125,179,.06); color: #5b6daa;
  border: none; border-radius: 100px; cursor: pointer; font-size: 15px;
  flex-shrink: 0; transition: all .25s;
}
.refresh-btn:hover { background: rgba(107,125,179,.12); }
.refresh-btn:disabled { opacity: .5; cursor: default; }
.refresh-btn.spinning { animation: spin .9s linear infinite; }
```

- [ ] **Step 3: 改 App.vue — 引入组件 + refreshing 状态 + async refreshData**

`<script setup>` 中：

```js
import { ref } from 'vue'                                  // 若未引入则补上
import PullToRefresh from './components/PullToRefresh.vue'
// ... 其余 import 不变

const refreshing = ref(false)

// 统一刷新入口：清缓存 → 重载 core + 当前路由分片 + 变化摘要
async function refreshData() {
  if (refreshing.value) return
  refreshing.value = true
  dismiss()
  clearDataCache()
  try {
    await Promise.all([loadData(), ensureRoute()])
  } finally {
    refreshing.value = false
  }
}

// 修正 Task 5 的 loading gating：loading 是 ref；刷新期间不闪全屏 loading
const initialLoading = computed(() => loading.value.core && !isPlayerDetail.value && !refreshing.value)
```

模板改三处：

1. 顶栏按钮（`header-right` 内，紧跟 `header-time` 后）：
```html
<button class="refresh-btn" :class="{ spinning: refreshing }"
        @click="refreshData()" :disabled="refreshing" title="刷新最新数据">⟳</button>
```

2. 现有"数据已更新"横幅点击改为统一入口（原 `loadData(); loadHistory();` → `refreshData()`）：
```html
<div v-if="updateAvailable" class="update-banner" @click="refreshData()">
  📊 数据已更新 · 点击刷新
</div>
```

3. `<main>` 内用 `<PullToRefresh>` 包住 router-view：
```html
<main class="main-content">
  <PullToRefresh :refreshing="refreshing" @refresh="refreshData()">
    <div v-if="initialLoading" class="loading-view">
      <div class="loading-spinner"></div>
      <p class="loading-text">正在加载数据…</p>
      <p class="loading-sub">从服务器获取最新行情</p>
    </div>
    <router-view v-else v-slot="{ Component }">
      <KeepAlive :exclude="['PlayerDetail']">
        <component :is="Component" />
      </KeepAlive>
    </router-view>
  </PullToRefresh>
</main>
```

说明：PullToRefresh 包住 router-view 即覆盖所有页面（含 PlayerDetail）。桌面用顶栏按钮，移动端下拉手势；两者都走 `refreshData()`。

- [ ] **Step 4: build 验证**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build
```

Expected: 构建成功，无未定义导入报错。

- [ ] **Step 5: dev server 手动验证**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run dev
```

浏览器 DevTools → 设备模拟（触屏）：
- 页面在顶部下拉 → 出现"下拉刷新"→ 超过阈值变"释放刷新"→ 松手 → spinner 转 → Network 里 core.json + 当前路由分片重新请求
- 页面未在顶部时下拉 → 不触发（正常滚动）
- 桌面点顶栏 ⟳ → 同样重新请求，`refreshing` 期间按钮转圈
- 停靠在页面 → 改 `core.json` 的 `crawl_time` → 切后台再切回 → 点横幅 → 数据实际更新（旧 bug 修复确认）

- [ ] **Step 6: Commit**

```bash
cd /Users/xywang/stockboard
git add stockboard-app/src/components/PullToRefresh.vue stockboard-app/src/App.vue stockboard-app/src/style.css
git commit -m "✨ 前端: 每页下拉刷新（PullToRefresh 手势 + 顶栏按钮）"
```

---

### Task 7: 全量验证 + 文档更新

**Files:**
- Test: `jiarenmens/scripts/verify_slices.py`（已建）
- Modify: `CLAUDE.md`（三、数据架构 + 5.8 速查表）
- Test: dev server 手动清单

- [ ] **Step 1: 重新导出 + 全量校验**

```bash
cd /Users/xywang/stockboard/jiarenmens
python scripts/export_json.py --out ../stockboard-app/public/data
python scripts/verify_slices.py ../stockboard-app/public/data/latest
```

Expected: `✅ 分片校验通过`。确认 `latest/` 下新增 9 个分片文件。

- [ ] **Step 2: 构建 + 各路由网络请求清单**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build && npm run dev
```

浏览器 DevTools → Network，逐路由核对**首次访问**只请求该页分片（JSON 类）：

| 路由 | 应请求的 JSON | 不应出现 |
|------|--------------|---------|
| `/copy` | core, copy, name_map, changes_summary, index.json | summary, players_index, changes |
| `/overview` | core, overview, stocks, trades, players_index | summary, changes |
| `/rankings` | core, players_index | summary |
| `/stocks` | core, stocks, players_index | summary |
| `/sectors` | core, sectors | summary |
| `/trades` | core, trades, name_map | summary |
| `/compare` | core, compare | summary |
| `/tracking` | core, changes | summary, changes_summary |
| `/player/:id` | core, players_index, players/{id}.json, history/{id}.json | summary |

再核对：切 Tab 往返（KeepAlive）不再重复请求；**`/copy` 首屏 JSON 传输合计 < ~300KB**。

- [ ] **Step 3: 刷新功能验证（修复点 + 手动入口）**

刷新入口共三个，需逐一验证都走 `refreshData()`（清缓存 → 实际拉到新数据）：
1. 改 `public/data/latest/core.json` 的 `crawl_time` → 切后台再切回（触发 `visibilitychange`）→ 点"📊 数据已更新 · 点击刷新" → 数据实际变化（旧代码缓存不失效，本次修复确认）
2. 桌面点顶栏 ⟳ 按钮 → 同样拉到新数据，Network 里 core + 当前路由分片重新请求
3. 移动端（DevTools 触屏模拟）页面顶部下拉 → "释放刷新" → 松手 → 数据更新
   停靠在页面时手动刷新，若部署环境有缓存，`fetch` 需带 `cache: 'no-cache'` 或靠 GH Pages 刷新指纹（后续若发现 CDN 缓存问题再加，见 Task 6 Step 5）。

- [ ] **Step 4: 更新 CLAUDE.md**

- `三、数据架构` 数据流图：`export_json.py → public/data/*.json` 改为分片结构说明
- `5.8 各组件数据来源速查`：补充每页懒加载的分片名
- `二、路由设计` 或 `五`：注明 `/copy` 首屏只拉 core + copy + name_map + changes_summary

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard
git add CLAUDE.md
git commit -m "📝 文档: 数据架构更新为分片按需加载"
```

---

## Self-Review 记录

- **Spec coverage**：目标（首屏按需、/copy 降 90% + 每页刷新最新数据）→ Task1 后端切片 + Task2-5 前端懒加载 + Task6 下拉刷新（手势+按钮） + Task7 验证/文档，全覆盖。原"无组件消费的 changes.json.alerts"通过 Task4 拆分成 summary 暴露保持不变；`stockHistory.alerts` 仍暴露但无消费，保持现状不删（避免无关改动）。
- **Placeholder scan**：无 TBD/略写；每个文件给出可粘贴的完整代码或精确行号引用。
- **Type consistency**：`addedCount/clearedCount/changeCount` 在 Task1(后端)、Task4(前端消费) 一致；`ensureSlices` 在 Task3(useData)、Task5/6(App.vue) 一致；分片文件名 `core/copy/stocks/trades/sectors/compare/overview/name_map/changes_summary` 在后端与 loader 间一致；`refreshing`/`refreshData`/`PullToRefresh` 在 Task5(引入 clearDataCache)、Task6(落地) 一致。
