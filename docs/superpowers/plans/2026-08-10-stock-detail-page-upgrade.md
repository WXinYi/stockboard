# StockDetailPage 详情页补齐 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐详情页短线核心功能(盘口/大单/涨停基因/竞价/龙虎榜)、图表方案B视觉改造、右侧盘口布局, 并落实美观规范。

**Architecture:** 纯逻辑(接口解析/指标/游资映射)抽为可测函数 + 组件按功能拆分。盘口主源用腾讯五档(实测可靠), KPL 接口走现有 postForm/getJson 封装。图表改造在 StockDetailPage.vue 内做尺寸/比例/分时副图统一。

**Tech Stack:** Vue 3.5 + lightweight-charts 5.2 + Vite 8 + vitest(新增)

## Global Constraints

- A股红涨绿跌: 涨 `#e74c3c` / 跌 `#27ae60`(spec §7.5 token), 全局唯一, 禁止第三套涨跌色
- 数字对齐: 所有数值 `font-variant-numeric: tabular-nums`
- 图表高度: `min(max(图表区宽×0.62, 220), 420)` 桌面 / `min(max(屏宽×0.62, 220), 280)` 移动
- 主图:量:副图 = **3:1:1**(setPaneStretch 改 3/1/1)
- 主图叠加 MA/BOLL/缠论/波浪**保留**, 属主图层; 副图指标 `none/macd/kdj/rsi/cci/wr/obv`
- 右盘口: 桌面 180px / 移动 116px
- 空态/加载态美观: 卡片无数据显占位("暂无数据"), 不裸报错文字
- 轮询沿用 `tick()` 静默模式, 仅交易时段
- 遵守 workflow-rules: 禁止直接 push, 本地验证后走 PR

---

### Task 1: vitest 基建 + 腾讯五档解析函数

**Files:**
- Modify: `stockboard-app/package.json`(加 devDep vitest + test script)
- Create: `stockboard-app/src/utils/pankou.js`
- Test: `stockboard-app/src/utils/__tests__/pankou.test.js`

**Interfaces:**
- Produces: `parsePankouTencent(raw: string|null) → { sell: [{px,vol}×5], buy: [{px,vol}×5], price, prevClose, upPx, downPx, turnover, volumeRatio, outer, inner } | null`
  - `raw` = 腾讯 `v_xxx` 脚本变量字符串(`~` 分隔, 见 `parseTencentQuote` 同款)
  - 字段: `[3]`现价 `[4]`昨收 `[7]`外盘 `[8]`内盘 `[9-18]`买1-5价量 `[19-28]`卖1-5价量 `[38]`换手 `[47]`涨停 `[48]`跌停 `[49]`量比
  - `sell[i]` 为卖 `i+1` 档(卖1在前), `buy[i]` 为买 `i+1` 档(买1在前)
  - 解析失败或字段缺失返回 `null`
- Produces: `loadTencentPankou(code: string, silent=false) → Promise<返回同上>`(动态加载 `v_<pfx><code>` 脚本变量, 复用 useStockDetail 里 `loadScriptVar` 同款实现)

- [ ] **Step 1: 安装 vitest**

```bash
cd stockboard-app
npm i -D vitest
```

- [ ] **Step 2: 加 test script**

在 package.json scripts 加: `"test": "vitest run"`、`"test:watch": "vitest"`。

- [ ] **Step 3: 写失败测试**

`stockboard-app/src/utils/__tests__/pankou.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { parsePankouTencent } from '../pankou.js'

// 从 fixture 读取真实返回(禁止手写省略串); fixture 见下方"禁止占位"说明
const RAW = readFileSync(fileURLToPath(new URL('./fixtures/pankou-sz000938.txt', import.meta.url)), 'utf-8')

describe('parsePankouTencent', () => {
  it('解析五档价量', () => {
    const r = parsePankouTencent(RAW)
    expect(r).not.toBeNull()
    expect(r.sell[0].px).toBe(101.60)   // 卖1
    expect(r.buy[0].px).toBe(100.10)    // 买1
    expect(r.price).toBe(101.50)
    expect(r.prevClose).toBe(100.00)
  })
  it('字段缺失返回 null', () => {
    expect(parsePankouTencent('short~string')).toBeNull()
    expect(parsePankouTencent(null)).toBeNull()
  })
})
```

> **禁止占位**: 测试里的 `RAW` 必须用真实完整串。做法: 浏览器打开 `https://qt.gtimg.cn/q=sz000938`, 把返回的 `v_sz000938="...";` 整段保存为 `src/utils/__tests__/fixtures/pankou-sz000938.txt`, 测试用 `fs.readFileSync` 读入。若无法抓包, 从 `parseTencentQuote` 的字段布局逐位构造一个 >=50 字段的合法串(字段 3=现价、4=昨收、9-18=买1-5、19-28=卖1-5、47=涨停、48=跌停), 保存为 fixture。禁止在测试里用 `...` 省略。

- [ ] **Step 4: 运行确认失败**

```bash
cd stockboard-app && npm test -- pankou
```

Expected: FAIL, `Cannot find module '../pankou.js'`

- [ ] **Step 5: 实现 `src/utils/pankou.js`**

```js
// 腾讯五档盘口解析: qt.gtimg.cn v_ 变量 → 盘口对象
// 字段布局同 useStockDetail.parseTencentQuote; 解析失败返回 null(调用方兜底)
export function parsePankouTencent(raw) {
  if (!raw || typeof raw !== 'string') return null
  const p = String(raw).split('~')
  if (p.length < 50) return null
  const num = (i) => { const v = parseFloat(p[i]); return Number.isFinite(v) ? v : null }
  const int = (i) => { const v = parseInt(p[i], 10); return Number.isFinite(v) ? v : 0 }
  const buy = [], sell = []
  for (let i = 0; i < 5; i++) {
    const bpx = num(9 + i * 2)
    if (bpx == null) return null
    buy.push({ px: bpx, vol: int(10 + i * 2) })
  }
  for (let i = 0; i < 5; i++) {
    const spx = num(19 + i * 2)
    if (spx == null) return null
    sell.push({ px: spx, vol: int(20 + i * 2) })
  }
  const price = num(3), prevClose = num(4)
  if (price == null || prevClose == null) return null
  return {
    sell, buy, price, prevClose,
    upPx: num(47), downPx: num(48),
    turnover: p[38], volumeRatio: num(49),
    outer: int(7), inner: int(8),
  }
}

// 动态加载腾讯行情脚本变量(与 useStockDetail 的 loadScriptVar 同款)
export function loadTencentPankou(code, silent = false) {
  const pfx = /^(6|5|9|11|10)/.test(code) ? 'sh' : /^(4|8|92)/.test(code) ? 'bj' : 'sz'
  const varName = 'v_' + pfx + code
  const url = `https://qt.gtimg.cn/q=${pfx}${code}`
  return new Promise((resolve) => {
    const script = document.createElement('script')
    const timer = setTimeout(() => { script.remove(); resolve(null) }, 8000)
    script.onload = () => {
      clearTimeout(timer)
      const r = parsePankouTencent(window[varName])
      script.remove()
      resolve(r)
    }
    script.onerror = () => { clearTimeout(timer); script.remove(); resolve(null) }
    script.src = url
    document.head.appendChild(script)
  })
}
```

- [ ] **Step 6: 运行确认通过**

```bash
cd stockboard-app && npm test -- pankou
```

Expected: PASS(2 tests)

- [ ] **Step 7: Commit**

```bash
cd stockboard-app && git add package.json package-lock.json src/utils/pankou.js src/utils/__tests__/pankou.test.js
cd .. && git commit -m "✅ 测试: 腾讯五档解析 + vitest 基建"
```

---

### Task 2: 新增 5 个 KPL 接口封装

**Files:**
- Modify: `stockboard-app/src/composables/useKplApi.js`(append 到文件末尾, 复用 `postForm`/`getJson`/`HOST_HQ`/`HOST_LHB`)

**Interfaces:**
- Consumes: 现有 `postForm(url, params, silent)`、`getJson(url, silent)`、`HOST_HQ`、`HOST_LHB`、`KPL_DEVICE`、`KPL_TOKEN`、`KPL_USERID`、`COMMON`
- Produces:
  - `fetchStockPankou(code, silent=false) → Promise<object|null>` — 完整盘口(含 weituo 10级); 失败/空 null
  - `fetchMainMonitor(code, silent=false) → Promise<Array|null>` — 逐笔大单 `[{time, price, side, vol, amount, type}]`; 空/失败 null
  - `fetchZhangTingGene(code, silent=false) → Promise<object|null>` — `{ztCount, premium5, nextRedPct, firstSealPct, breakPct, lianbanPct}`; 空/失败 null
  - `fetchStockBid(code, silent=false) → Promise<Array|null>` — `[{time, price, side, cumVol}]`(09:15-09:25); 空/失败 null
  - `fetchStockLhbHistory(code, silent=false) → Promise<Array|null>` — 该股上榜历史 `[{date, code, name, chgPct, buyIn, joinNum, dealer: string[]}]`; `dealer` 为当日营业部名数组(游资标签用, 联调映射); 空/失败 null

> 所有函数**防御性解析**: 接口字段结构以爬虫注释为据, 联调时若字段缺失→返回 null(空态处理), 禁止 throw 让页面崩。参数格式(来自 auction_spider.py 实测):

- [ ] **Step 1: 写失败测试**

`stockboard-app/src/composables/__tests__/useKplApi.test.js` — 用 vi.mock 拦截 `postForm`, 验证函数按期望参数调用并正确解析:

```js
import { describe, it, expect, vi, beforeEach } from 'vitest'
const MOCK = vi.hoisted(() => ({ postForm: vi.fn(), getJson: vi.fn() }))
vi.mock('../useKplApi.js', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, postForm: MOCK.postForm, getJson: MOCK.getJson }
})
import { fetchZhangTingGene, fetchMainMonitor, fetchStockBid } from '../useKplApi.js'

describe('KPL 新增接口', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('fetchZhangTingGene 解析基因六维', async () => {
    MOCK.postForm.mockResolvedValue({ List: [['5', '3', '62.5', '78', '22', '40']] })
    const r = await fetchZhangTingGene('002594')
    expect(MOCK.postForm).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ a: 'GetZhangTingGene', StockID: '002594' }), false)
    expect(r).toEqual({ ztCount: '5', premium5: '3', nextRedPct: '62.5', firstSealPct: '78', breakPct: '22', lianbanPct: '40' })
  })
  it('fetchZhangTingGene 空 List 返回 null', async () => {
    MOCK.postForm.mockResolvedValue({ List: [] })
    expect(await fetchZhangTingGene('002594')).toBeNull()
  })
  it('fetchMainMonitor 解析逐笔列表', async () => {
    MOCK.postForm.mockResolvedValue({ List: [['14:02', '101.5', '0', '200', '1', '203000']] })
    const r = await fetchMainMonitor('002594')
    expect(r).toHaveLength(1)
    expect(r[0]).toHaveProperty('vol', 200)
  })
  it('fetchStockBid 解析竞价序列', async () => {
    MOCK.postForm.mockResolvedValue({ bid: [['0925', '101.2', '1', '12000']] })
    const r = await fetchStockBid('002594')
    expect(r).toHaveLength(1)
    expect(r[0].time).toBe('0925')
  })
})
```

> ⚠️ 注意: `fetchZhangTingGene`/`fetchMainMonitor`/`fetchStockBid` 是新增导出; mock 里 `vi.mock` 路径是 `../useKplApi.js`(测试文件在 `__tests__/` 下 → 相对路径 `../useKplApi.js`)。测试**先写字段解析断言**, 若实现时接口字段与爬虫注释不符(如 `List` 行序不同), 调整实现以匹配爬虫实测格式 —— 测试断言的是爬虫注释里的格式。

- [ ] **Step 2: 运行确认失败**

```bash
cd stockboard-app && npm test -- useKplApi
```

Expected: FAIL(`fetchZhangTingGene` 未定义或 `postForm` 未被调用)

- [ ] **Step 3: 实现 5 个函数**(append 到 useKplApi.js 末尾)

```js
// ============ 详情页短线功能(2026-08-10 新增) ============

// 完整盘口 GetStockPanKou (POST) — 含 10 级 weituo/内外盘; 字段联调验证
export async function fetchStockPankou(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetStockPanKou', c: 'StockL2Data', DeviceID: KPL_DEVICE, StockID: code, State: 1, ...COMMON }, silent)
  if (!j || j.errcode !== '0') return null
  return j
}

// 逐笔大单 GetMainMonitor_w30 (POST) — 行 [时间,价格,方向0买1卖,手数,类型?,金额]
export async function fetchMainMonitor(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetMainMonitor_w30', c: 'StockYiDongKanPan', Order: 0, st: 20, Index: 0, Money: 2, StockID: code, IsBS: 0, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.List)) {
    if (silent) return null
    return null
  }
  return j.List.map(r => ({
    time: String(r[0] || ''), price: parseFloat(r[1]),
    side: r[2] === '0' ? '买' : '卖', vol: parseFloat(r[3]),
    amount: parseFloat(r[5]),
    type: parseFloat(r[3]) >= 100 ? '超大' : parseFloat(r[3]) >= 50 ? '大单' : '中单',
  }))
}

// 涨停基因 GetZhangTingGene (GET, 免Token) — List[涨停次数,5%溢价次,次日红盘%,首板封板率%,破板率%,连板率%]
export async function fetchZhangTingGene(code, silent = false) {
  const url = `${HOST_HQ}?${new URLSearchParams({ a: 'GetZhangTingGene', apiv: 'w42', c: 'StockL2Data', StockID: code, PhoneOSNew: 1, DeviceID: KPL_DEVICE, VerSion: '5.21.0.0' })}`
  const j = await getJson(url, silent)
  if (!j || !Array.isArray(j.List) || !j.List.length) {
    if (silent) return null
    return null
  }
  const g = j.List[0]
  return { ztCount: g[0], premium5: g[1], nextRedPct: g[2], firstSealPct: g[3], breakPct: g[4], lianbanPct: g[5] }
}

// 竞价分时 GetStockBid (POST) — bid[[时间,价格,买卖方向,累计量],...]
export async function fetchStockBid(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetStockBid', c: 'StockL2Data', apiv: 'w41', StockID: code, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.bid)) {
    if (silent) return null
    return null
  }
  return j.bid.map(r => ({ time: String(r[0]), price: parseFloat(r[1]), side: r[2], cumVol: parseFloat(r[3]) }))
}

// 龙虎榜个股历史 GetStockList (POST) — 全市场榜单按 code 过滤(联调确认是否支持 StockID 参数)
// ⚠️ dealer 字段: 该股当日买卖营业部名数组(游资标签用)。真实字段名以联调返回为准 ——
//   KPL 龙虎榜返回通常含买卖营业部明细(联调时找到营业部名所在字段映射到 dealer);
//   若接口无逐日营业部明细, dealer 置空数组, LhbStockCard 只显示净买入/机构家数(游资标签随之隐藏)。
export async function fetchStockLhbHistory(code, silent = false) {
  const j = await postForm(HOST_LHB, { a: 'GetStockList', st: 500, c: 'LongHuBang', Index: 0, Type: 1, Time: '', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.list)) {
    if (silent) return null
    return null
  }
  return j.list
    .filter(r => String(r.ID) === String(code))
    .map(r => ({
      date: j.Time || '', code: String(r.ID), name: r.Name,
      chgPct: parseFloat(String(r.IncreaseAmount).replace('%', '')),
      buyIn: +r.BuyIn || 0, joinNum: +r.JoinNum || 0,
      dealer: [],   // ← 联调填: 当日营业部名数组(如 ["华鑫证券上海分公司", "国泰君安南京太平南路"]); 无则空数组
    }))
}
```

> `fetchMainMonitor`/`fetchStockBid`/`fetchZhangTingGene` 的 `silent` 失败均返回 null 而非 throw —— 页面空态处理而非报错。**Token 参数**: 涨停基因/竞价接口爬虫里有的免 Token、有的带 Token; 实现时若返回 errcode≠0 的鉴权错误, 补 `Token: KPL_TOKEN, UserID: KPL_USERID`(见风险)。

- [ ] **Step 4: 运行确认通过**

```bash
cd stockboard-app && npm test -- useKplApi
```

Expected: PASS(4 tests)

- [ ] **Step 5: Commit**

```bash
cd stockboard-app && git add src/composables/useKplApi.js src/composables/__tests__/useKplApi.test.js
cd .. && git commit -m "🔌 接口: 新增盘口/大单/涨停基因/竞价/龙虎榜 5 个 KPL 封装"
```

---

### Task 3: 游资席位映射 + 图表方案B尺寸改造

**Files:**
- Create: `stockboard-app/src/utils/seatMap.js`
- Test: `stockboard-app/src/utils/__tests__/seatMap.test.js`
- Modify: `stockboard-app/src/components/StockDetailPage.vue`(图表高度/比例/分时副图)

**Interfaces:**
- Consumes: Task 2 的 `fetchStockLhbHistory`
- Produces:
  - `matchSeat(name: string) → string|null` — 营业部名→游资标签(孙哥/养家/方新侠等), 包含匹配; 无匹配 null
  - `chartHeight(containerWidth) → number` — `clamp(width*0.62, 220, 420)`
  - `setPaneStretchRatio()` — 3 pane 时 3/1/1, 2 pane 时 2.2/1

- [ ] **Step 1: 写失败测试 seatMap**

`stockboard-app/src/utils/__tests__/seatMap.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { matchSeat } from '../seatMap.js'

describe('matchSeat 游资映射', () => {
  it('包含匹配返回标签', () => {
    expect(matchSeat('国泰君安南京太平南路')).toBe('赵老哥')
  })
  it('无匹配返回 null', () => {
    expect(matchSeat('某某营业部')).toBeNull()
  })
})
```

> 映射表从 `jiarenmens/src/analysis/seat_map.py` 移植(孙哥/养家/方新侠/赵老哥等), 实现时逐条搬。

- [ ] **Step 2: 运行确认失败**

```bash
cd stockboard-app && npm test -- seatMap
```

Expected: FAIL(module not found)

- [ ] **Step 3: 实现 seatMap.js + 图表改造**

`src/utils/seatMap.js` — 从 seat_map.py 移植映射表:

```js
// 游资席位映射(移植自 jiarenmens/src/analysis/seat_map.py)
// 营业部名 → 游资标签; 包含匹配(营业部名包含关键词即命中)
// ⚠️ 实现前必须先 Read jiarenmens/src/analysis/seat_map.py, 把其中全部 (营业部关键词, 游资标签) 对逐条搬入,
//    不得只抄下列示例条目。seat_map.py 结构: 通常为 {游资标签: [营业部名...]} 或反向, 移植时转成下面
//    {keyword, label} 扁平行序。
const SEAT_MAP = [
  // 以下仅为结构示例 —— 全部条目以 seat_map.py 实际内容为准
  { keyword: '国泰君安南京太平南路', label: '赵老哥' },
  { keyword: '华鑫证券上海分公司', label: '量化打板' },
  // …(seat_map.py 其余全部条目)
]
export function matchSeat(name) {
  if (!name) return null
  for (const { keyword, label } of SEAT_MAP) {
    if (name.includes(keyword)) return label
  }
  return null
}
```

StockDetailPage.vue 图表尺寸改造(关键 3 处):

```js
// 1. 图表高度计算函数(替代固定 360)
function chartHeight() {
  const w = chartEl.value ? chartEl.value.clientWidth : 360
  const mobile = window.innerWidth <= 480
  const raw = w * 0.62
  return mobile ? Math.min(Math.max(raw, 220), 280) : Math.min(Math.max(raw, 220), 420)
}
// ensureChart() 里: height: chartHeight()
// renderSeries() 里: chart.applyOptions({ height: chartHeight(), ... })

// 2. 比例 3:1:1(替代 2.2/0.6/1.0)
function setPaneStretch() {
  const panes = chart.panes()
  if (panes.length >= 3) { panes[0].setStretchFactor(3); panes[1].setStretchFactor(1); panes[2].setStretchFactor(1) }
  else if (panes.length === 2) { panes[0].setStretchFactor(2.2); panes[1].setStretchFactor(1) }
}

// 3. 分时视图也显示副图: subInd 从 isKline 提升为共用
//    - 副图 select 的 v-if="isKline" 改为常显(分时也显示)
//    - trend 分支: 原来硬编码画分时 MACD → 改为按 subInd 渲染
//      (对 trend.value 算指标: subInd==='macd' → calcMACD 同款; kdj/rsi/wr/cci/obv 对价格序列算)
```

- [ ] **Step 4: 浏览器验证**

```bash
cd stockboard-app && npm run dev
```

验证:
1. 打开某只股票详情(`/#/stock/sz000938`), 拖动窗口宽度, 图表高度跟随(桌面 clamp 220~420, 移动 220~280)
2. 分时视图下副图有 MACD, 切换下拉副图选项生效
3. K 线视图 MA/BOLL/缠/波叠加仍在, 三栏比例明显是 3:1:1
4. 数字对齐(报价/图表刻度 tabular-nums)

- [ ] **Step 5: Commit**

```bash
cd stockboard-app && git add src/utils/seatMap.js src/utils/__tests__/seatMap.test.js src/components/StockDetailPage.vue
cd .. && git commit -m "📐 图表: 方案B尺寸(宽×0.62 clamp) + 3:1:1 + 分时副图 + 游资映射表"
```

---

### Task 4: 右侧盘口面板(PankouPanel) + 盘口布局

**Files:**
- Create: `stockboard-app/src/components/PankouPanel.vue`
- Modify: `stockboard-app/src/components/StockDetailPage.vue`(引入组件 + 布局: 图表/盘口 flex 行)

**Interfaces:**
- Consumes: Task 1 的 `loadTencentPankou`; Task 2 的 `fetchStockPankou`(可选增强)
- Produces: `<PankouPanel :code="code" :quote="quote" />` — props `code`, `quote`(用于现价/昨收高亮)
- Produces: `quote.pankou` — 详情页把盘口数据并入 quote ref(轮询 5s 更新, 与报价同频)

- [ ] **Step 1: 写失败测试(盘口数据并入逻辑)**

在 useKplApi.test.js 或新 `useStockDetail.test.js` 验证盘口数据并入:

```js
// 盘口解析已在 Task 1 测; 这里验证 StockDetailPage 的 loadPankou 轮询逻辑:
// loadPankou(silent) 调 loadTencentPankou(code), 成功后 quote.value.pankou = r
```

> 组件级逻辑(轮询/响应式)用浏览器验证, 纯解析已有单测覆盖。此步为占位 → **改为**: 写 `useStockDetail.test.js` 测 `useStockDetail` 暴露的盘口字段初始化(简单):

```js
import { describe, it, expect } from 'vitest'
import { useStockDetail } from '../useStockDetail.js'
describe('useStockDetail 盘口字段', () => {
  it('quote 初始 pankou 为 null', () => {
    const { quote } = useStockDetail('sz000938')
    expect(quote.value.pankou).toBeNull()
  })
})
```

- [ ] **Step 2: 运行确认失败**

```bash
cd stockboard-app && npm test -- useStockDetail
```

Expected: FAIL(`quote.value.pankou` undefined → toBeNull 失败)

- [ ] **Step 3: 实现 PankouPanel.vue + 接入**

`src/components/PankouPanel.vue` — 五档表(卖1-5红 → 现价高亮 → 买1-5绿) + 委比/委差/外盘/内盘/涨停/跌停/换手/量比:

```vue
<script setup>
// props: code, quote; emit 无
// 委比/委差 = Σ买vol - Σ卖vol 计算
// 视觉: 挂单量条 rgba(红/绿, .13) 背景, 现价行 #f2f6fb, 数字 tabular-nums
</script>
```

StockDetailPage.vue 接入:
- import + 布局: 图表区与盘口包 `<div class="sd-chart-row">`(flex, 盘口 `flex:none` 宽 180px/116px)
- `loadPankou(silent)`: `const r = await loadTencentPankou(code.value, silent); if (r) quote.value.pankou = r`
- 轮询: `timers.pankou = setInterval(() => tick(() => loadPankou(true)), 5000)`
- 初始加载: 并入 `onActivated` 的 `loadPankou()` 与 `watch(code)` 的重载列表
- 视觉规范: 桌面 180px 右盘口, 移动 116px(`@media (max-width:480px)` 收窄)

- [ ] **Step 4: 浏览器验证 + 单测**

```bash
cd stockboard-app && npm test -- useStockDetail   # PASS
npm run dev  # 验证五档真实渲染、价格高亮、移动端收窄
```

- [ ] **Step 5: Commit**

```bash
cd stockboard-app && git add src/components/PankouPanel.vue src/components/StockDetailPage.vue src/composables/__tests__/useStockDetail.test.js
cd .. && git commit -m "💹 盘口: 右侧五档面板(桌面180/移动116) + 5s轮询并入"
```

---

### Task 5: 涨停基因 + 竞价分时卡片

**Files:**
- Create: `stockboard-app/src/components/LimitGeneCard.vue`
- Create: `stockboard-app/src/components/BidAuctionCard.vue`
- Modify: `stockboard-app/src/components/StockDetailPage.vue`(功能卡区引入)

**Interfaces:**
- Consumes: Task 2 `fetchZhangTingGene`, `fetchStockBid`
- Produces:
  - `<LimitGeneCard :gene="gene" />` — props `gene`(Task 2 的基因对象或 null)
  - `<BidAuctionCard :bid="bid" :prev-close="quote.prevClose" />` — props `bid`(竞价序列) + `prevClose`(红涨绿跌基准)

- [ ] **Step 1: 写失败测试(竞价图表数据转换)**

`stockboard-app/src/utils/__tests__/bidChart.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { bidToPoints } from '../bidChart.js'
describe('bidToPoints 竞价序列→SVG点', () => {
  it('映射价格为 viewBox 坐标', () => {
    const pts = bidToPoints([{ time: '0915', price: 101, side: '1', cumVol: 100 }, { time: '0925', price: 102, side: '1', cumVol: 200 }], 100, 220, 64)
    expect(pts).toHaveLength(2)
    expect(pts[0].x).toBe(0)
    expect(pts[1].x).toBe(220)
  })
})
```

> `bidToPoints(bid, prevClose, w, h)` 在 `src/utils/bidChart.js` 定义: 价格映射到涨停价~跌停价区间(同分时 Y 轴约定), 成交量柱在底部。

- [ ] **Step 2: 运行确认失败**

```bash
cd stockboard-app && npm test -- bidChart
```

Expected: FAIL(module not found)

- [ ] **Step 3: 实现 bidChart.js + 两卡片**

`src/utils/bidChart.js`:

```js
// 竞价分时 → SVG 点序列(涨停~跌停 Y 轴, 与分时一致); bidToPoints 返回 {line:[{x,y}], bars:[{x,y,h,color}]}
export function bidToPoints(bid, prevClose, w = 220, h = 64) {
  const up = prevClose * 1.1, down = prevClose * 0.9   // 涨停/跌停(近似, 详细板规则可后续接 calcLimitPx)
  const n = bid.length
  if (!n) return { line: [], bars: [] }
  const xw = w / (n - 1)
  const yp = (px) => (up - px) / (up - down) * (h - 8)
  const line = bid.map((p, i) => ({ x: i * xw, y: yp(p.price) }))
  const maxVol = Math.max(...bid.map(p => p.cumVol), 1)
  const bars = bid.map((p, i) => ({
    x: i * xw, y: h - 8 - (p.cumVol / maxVol) * (h - 8), h: (p.cumVol / maxVol) * (h - 8),
    color: p.price >= prevClose ? '#e74c3c' : '#27ae60',
  }))
  return { line, bars }
}
```

`LimitGeneCard.vue` — 6 维基因展示(涨停次数/5%溢价/次日红盘%/首板封板率/破板率/连板率), 空态"暂无涨停基因数据"。
`BidAuctionCard.vue` — 竞价 SVG 折线+量柱, 角标"09:15-09:25", 空态/非竞价时段"非竞价时段"。

StockDetailPage.vue 接入:
- state: `gene = ref(null)`, `bid = ref(null)`
- `loadGene()`: `gene.value = await fetchZhangTingGene(code.value)`; `loadBid()`: `bid.value = await fetchStockBid(code.value)`
- 初始加载入 onActivated; 页面激活时加载一次(不轮询, 低频)
- 功能卡区: 插入 `<LimitGeneCard>` + `<BidAuctionCard>`(与后续大单/龙虎榜同排 4 列, 移动堆叠)

- [ ] **Step 4: 浏览器验证 + 单测**

```bash
cd stockboard-app && npm test -- bidChart   # PASS
npm run dev  # 验证两卡真实数据/空态
```

- [ ] **Step 5: Commit**

```bash
cd stockboard-app && git add src/utils/bidChart.js src/utils/__tests__/bidChart.test.js src/components/LimitGeneCard.vue src/components/BidAuctionCard.vue src/components/StockDetailPage.vue
cd .. && git commit -m "🧬 涨停: 涨停基因 + 竞价分时卡片"
```

---

### Task 6: 大单监控 + 历史涨停 + 龙虎榜卡片

**Files:**
- Create: `stockboard-app/src/components/BigOrderCard.vue`
- Create: `stockboard-app/src/components/ZtHistoryCard.vue`
- Create: `stockboard-app/src/components/LhbStockCard.vue`
- Modify: `stockboard-app/src/components/StockDetailPage.vue`(功能卡区补全 + 4 卡 4 列)

**Interfaces:**
- Consumes: Task 2 `fetchMainMonitor`, `fetchLimitReason`(现有), `fetchStockLhbHistory`; Task 3 `matchSeat`
- Produces:
  - `<BigOrderCard :orders="orders" />` — props `orders`(大单列表)
  - `<ZtHistoryCard :reason="limitReason" />` — props `reason`(现有 limitReason: zsCodes + reason)
  - `<LhbStockCard :history="lhbHistory" />` — props `history`(上榜历史, 已含游资标签)

- [ ] **Step 1: 写失败测试(游资标签注入)**

`stockboard-app/src/utils/__tests__/lhb.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { withSeatLabels } from '../lhb.js'
describe('withSeatLabels 龙虎榜营业部→游资标签', () => {
  it('给营业部打上游资标签', () => {
    const rows = [
      { name: '比亚迪', dealer: ['国泰君安南京太平南路', '华鑫证券上海分公司'] },
      { name: '某某', dealer: ['某某营业部'] },
      { name: '无营业部', dealer: [] },
    ]
    const out = withSeatLabels(rows)
    expect(out[0].seats).toEqual(['赵老哥', '量化打板'])
    expect(out[1].seats).toEqual([])
    expect(out[2].seats).toEqual([])
  })
})
```

> `withSeatLabels(rows)` 在 `src/utils/lhb.js`: 每行读 `dealer: string[]`, 逐项 `matchSeat` 得到命中标签数组 `seats`(原数组顺序, 无命中则跳过)。**输入 `dealer` 来自 Task 2 的 `fetchStockLhbHistory`(联调时把真实营业部字段映射进去)**。`LhbStockCard` 展示 `seats` 标签; 空数组则不显示游资标签。

- [ ] **Step 2: 运行确认失败**

```bash
cd stockboard-app && npm test -- lhb
```

Expected: FAIL(module not found)

- [ ] **Step 3: 实现三卡**

`src/utils/lhb.js`:

```js
import { matchSeat } from './seatMap.js'
export function withSeatLabels(rows) {
  return (rows || []).map(r => ({
    ...r,
    seats: (r.dealer || []).map(d => matchSeat(d)).filter(Boolean),
  }))
}
```

`BigOrderCard.vue` — 逐笔大单表(时间/价格/方向红买绿卖/手数/金额/超大·大·中标签), 空态"暂无大单数据"。
`ZtHistoryCard.vue` — 用现有 `limitReason`(`fetchLimitReason` 的最近涨停原因)展示: 原因文本 + 关联板块 chips; 非涨停股隐藏整卡(现有 `isLimitUp` 判断复用)。
`LhbStockCard.vue` — 上榜记录(日期/涨幅/净买入/机构家数) + 游资标签 tag, 空态"近期未上榜"。

StockDetailPage.vue 接入:
- state: `orders = ref(null)`, `lhbHistory = ref(null)`
- `loadBigOrder()`: `orders.value = await fetchMainMonitor(code.value)` — 15s 轮询并入 `timers`
- `loadLhb()`: `lhbHistory.value = await fetchStockLhbHistory(code.value)` — 激活时加载一次
- 历史涨停复用现有 `limitReason`(无新请求); `ZtHistoryCard` 复用 `isLimitUp` 显隐
- 功能卡区 4 列: BidAuction / LimitGene / BigOrder / LhbStock(移动堆叠), 第二行 ZtHistory

- [ ] **Step 4: 浏览器验证 + 单测**

```bash
cd stockboard-app && npm test -- lhb   # PASS
npm run dev  # 验证三卡真实数据/空态/游资标签
```

- [ ] **Step 5: Commit**

```bash
cd stockboard-app && git add src/utils/lhb.js src/utils/__tests__/lhb.test.js src/components/BigOrderCard.vue src/components/ZtHistoryCard.vue src/components/LhbStockCard.vue src/components/StockDetailPage.vue
cd .. && git commit -m "📊 大单: 大单监控 + 历史涨停 + 龙虎榜卡(游资标签)"
```

---

### Task 7: 交互优化(吸顶/tooltip/复制/时效/骨架屏) + 空态统一

**Files:**
- Create: `stockboard-app/src/components/StickyQuoteBar.vue`
- Modify: `stockboard-app/src/components/StockDetailPage.vue`

**Interfaces:**
- Consumes: 现有 `quote`, `crossInfo`, `upColor`(详情页 state)
- Produces: `<StickyQuoteBar :quote="quote" :cross-info="crossInfo" />` — 吸顶条(名称/现价/涨跌幅/数据时间), `position: sticky; top:0`, 不透明白底 + 轻阴影

- [ ] **Step 1: 写失败测试(数据时效文本)**

`stockboard-app/src/utils/__tests__/timeText.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { freshnessText } from '../timeText.js'
describe('freshnessText 数据时效', () => {
  it('交易时段返回时间', () => {
    expect(freshnessText('15:00', true)).toBe('15:00')
  })
  it('非交易时段返回已收盘', () => {
    expect(freshnessText('15:00', false)).toBe('已收盘 15:00')
  })
})
```

> `freshnessText(timestampStr, isTrading)` 在 `src/utils/timeText.js`: 把行情时间戳(腾讯返回 `YYYYMMDDHHMMSS` 或 `HHMMSS`)格式化, 非交易时段前缀"已收盘"。`isTradingTime()` 已有。

- [ ] **Step 2: 运行确认失败**

```bash
cd stockboard-app && npm test -- timeText
```

Expected: FAIL(module not found)

- [ ] **Step 3: 实现 timeText.js + StickyQuoteBar + 交互**

`src/utils/timeText.js`:

```js
// 行情时间戳 → "HH:MM" 展示; 非交易时段加"已收盘"前缀
export function freshnessText(raw, trading = false) {
  if (!raw) return ''
  const s = String(raw)
  let hh, mm
  if (s.length >= 12) { hh = s.slice(8, 10); mm = s.slice(10, 12) }
  else if (s.length >= 6) { hh = s.slice(0, 2); mm = s.slice(2, 4) }
  else return s
  const t = `${hh}:${mm}`
  return trading ? t : `已收盘 ${t}`
}
```

`StickyQuoteBar.vue` + StockDetailPage.vue:
1. **吸顶**: 报价区名称行 → `<StickyQuoteBar>`, `position: sticky; top: 0; z-index: 30; background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,.08)`; 数据与顶部报价同源(quote + crossInfo)
2. **tooltip**: 十字光标处, 在图表右侧光标位置显示浮动小卡(时间/开/高/低/收/涨跌/量); `onCrosshair` 已有 `param` 回调, 加一个 `cursorTip = ref({x,y,info})`, 定位在光标右侧, 轻量不遮挡
3. **代码复制**: 报价区代码旁按钮, `navigator.clipboard.writeText(code)`, 成功显示"已复制"1.5s
4. **数据时效**: 报价区显示 `freshnessText(quote.quoteTime, isTradingTime())`
5. **骨架屏**: 图表/报价加载时灰块微光(纯 CSS `@keyframes pulse`), 替换"加载中…"文字

- [ ] **Step 4: 浏览器验证 + 单测**

```bash
cd stockboard-app && npm test -- timeText   # PASS
npm run dev  # 验证吸顶(滚动)、tooltip(悬停)、复制(点击)、时效、骨架屏
```

- [ ] **Step 5: Commit**

```bash
cd stockboard-app && git add src/utils/timeText.js src/utils/__tests__/timeText.test.js src/components/StickyQuoteBar.vue src/components/StockDetailPage.vue
cd .. && git commit -m "✨ 交互: 吸顶报价条 + 悬浮tooltip + 复制代码 + 数据时效 + 骨架屏"
```

---

## Self-Review

### 1. Spec 覆盖

| Spec 章节 | 对应任务 |
|---|---|
| §3 图表方案B(尺寸/3:1:1/分时副图/副图选项) | Task 3 |
| §4.1 组件拆分(6 新组件) | Task 4/5/6(PankouPanel, BidAuction, LimitGene, BigOrder, ZtHistory, LhbStock) |
| §4.2 新增 5 接口 | Task 2 |
| §4.3 轮询并入 | Task 4(盘口 5s)+ Task 6(大单 15s) |
| §5 P1 交互(吸顶/tooltip/复制/时效/骨架屏) | Task 7 |
| §7 分阶段交付 | Task 3=Phase1, Task4=Phase2, Task5=Phase3, Task6=Phase4, Task7=Phase5 |
| §7.5 视觉规范 | 各任务内嵌(盘口量条/卡片/空态/数字对齐) |
| §9 不做(P2) | 无任务(符合) |

**覆盖检查发现**: `GetStockPanKou`(Task 2 的 `fetchStockPankou`)返回结构完全未定义字段(只 `return j`), 且盘口主源实际是腾讯五档(Task 1/4)。spec §4.2 列了它但标"可选增强"。**修正**: Task 4 明确 `fetchStockPankou` 是可选增强, 默认走腾讯五档; 若联调验证 KPL 盘口字段可用则替换/增强。

### 2. Placeholder 扫描

- Task 1 测试的 `RAW` 已改为 readFileSync 读 fixture 文件(`fixtures/pankou-sz000938.txt`), 并明确禁止 `...` 占位 —— 已落实。
- Task 4 Step 1 注释里"此步为占位 → 改为"的自相矛盾残留 —— 已重写为明确的 useStockDetail 测试(断言 `quote.value.pankou` 初始为 null)。
- Task 3 seatMap 映射表 —— 已改为明确指令: 实现前 Read `jiarenmens/src/analysis/seat_map.py` 全量搬, 不得只抄示例条目。

### 3. Type 一致性

- `parsePankouTencent` 返回 `{sell, buy, price, prevClose, upPx, downPx, turnover, volumeRatio, outer, inner}` — Task 1 定义, Task 4 PankouPanel 消费, 一致。
- `quote.value.pankou` — Task 4 定义, PankouPanel props `quote` 读 `quote.pankou`, 一致。
- `fetchZhangTingGene` 返回 `{ztCount, premium5, nextRedPct, firstSealPct, breakPct, lianbanPct}` — Task 2 定义, Task 5 LimitGeneCard 消费, 一致。
- `fetchStockLhbHistory` 返回 `[{date, code, name, chgPct, buyIn, joinNum, dealer: string[]}]` — Task 2 定义; Task 6 的 `withSeatLabels(rows)` 读 `row.dealer`(数组)→ 逐项 `matchSeat` → 输出 `row.seats: string[]`; LhbStockCard 消费 `seats`。整条链已修正一致(dealer 数组, seats 数组)。
- `withSeatLabels` 输出字段名 `seats`(非单值 `seat`) — Task 6 测试与实现已同步改为 `seats`。

以上修正(占位 fixture / seatMap 来源 / dealer↔seats 链)已在计划正文落实, 本 self-review 记录存档。
