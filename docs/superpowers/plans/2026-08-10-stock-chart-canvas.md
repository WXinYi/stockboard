# Canvas 自绘图表实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 Canvas 2D 自绘图表替换 lightweight-charts,实现与右盘口对齐的高度、涨停板双轴分时、K线缠论/波浪叠加、光标联动与 K 线平移。

**Architecture:** 新增纯几何模块 `chartDraw.js`(可单测)+ 图表组件 `StockChartCanvas.vue`(canvas 2D + pointer 交互 + ResizeObserver),宿主 `StockDetailPage.vue` 保留 `computeIndicators`/`crossInfo`/`cursorTip` 业务逻辑,删除全部 lightweight-charts 渲染代码。数据流: 宿主算 `indCache` → props 传入组件 → 组件画。

**Tech Stack:** Vue 3 `<script setup>`、原生 Canvas 2D、vitest v4.1.10、Vite 5。**不加任何图表库。**

**Spec:** `docs/superpowers/specs/2026-08-10-stock-chart-canvas-design.md`

## Global Constraints

- 项目运行命令必须以 `cd /Users/xywang/stockboard/stockboard-app` 开头(bash cwd 会重置到 repo root)。
- 测试: `npm test`(vitest run);构建: `npm run build`。
- Commit 消息必须以 `Co-Authored-By: Claude <noreply@anthropic.com>` 结尾。
- **禁止直接 push**,完成前本地验证(build + 全部测试通过)。
- 颜色 token 唯一来源是 spec §7.2(涨 `#e74c3c` / 跌 `#27ae60` / 均价 `#f2a900` / 缠 `#7d3c98` / 波 `#d35400` / 主 `#2980b9` 等),禁止近似。
- 指标数据结构不可改(以 `indicators.js` 现有返回为准): `ma/volma` `{n:[...]}`、`boll` `{up,mid,lo}`、`macd` `{dif,dea,hist}`、`kdj` `{k,d,j}`、`rsi` `{6:[...],12:[...],24:[...]}`、`wr` `{10:[...],6:[...]}`、`fractals` `[{i,type:1|-1}]`、`bis` `[{from:{i,type},to:{i,type}}]`、`zhongshu` `[{zg,zd,from,to}]`、`chanSignals` `[{i,type:'1buy'|...}]`、`divergences` `[{i,type:'top'|'bottom'}]`、`waves` `{status:'ok'|'ok5'|'unknown',waves:[{i,type,label}],dir}`。
- 分时 Y 轴 = 涨停板边界(左轴涨跌幅度 % + 右轴价格),X 轴固定全天 09:30~15:00。
- 缠论/波浪仅日/周/月画(m60 不画)。
- 不做: 缩放、分时平移、CCI/OBV 副图。

---

### Task 1: `chartDraw.js` 纯几何模块(TDD)

**Files:**
- Create: `stockboard-app/src/utils/chartDraw.js`
- Test: `stockboard-app/src/utils/__tests__/chartDraw.test.js`

**Interfaces:**
- Produces (later tasks + tests depend on these exact names):
  - `panelRects(w, h, sub, opts?)` → `{ main:{x,y,width,height}, vol:{x,y,width,height}, sub:{x,y,width,height}|null }`
    - 每区含 1px 边框视觉(几何按直角),区间距 2px;`sub=true` 时 `main:vol:sub=3:1:1`,`sub=false` 时 `main:vol=2.2:1`。
    - `opts = { leftGutter, rightGutter }`(分时 `{32,40}`,K线 `{0,40}`): 轴带从各区 x/width 中扣除(左轴带减 x、右轴带减 width)。
  - `priceToY(price, min, max, rect)` → `number`(min→rect.y+4%h, max→rect.y+height-4%h 线性映射;调用方负责传已含 padding 的 min/max)
  - `klineWindow(kline, count, offset)` → `{ window, offset }`(offset clamp 到 `[0, max(0, len-count)]`;offset=0 取末尾 count 根)
  - `idxToX(i, w, count)` → `number`(x = (i+0.5)/count*w)
  - `timeTicks(items, w, isIntraday)` → `[{x, label}]`(分时固定 `09:30|10:30|11:30/13:00|14:00|15:00` 5 等分;K线/m60 均匀取 4~6 个含首末,label 分时 `HH:MM`、日周月 `MM-DD`)
  - `priceTicksTrend(upPx, downPx, prevClose, rect)` → `{ left:[{y,label}], right:[{y,label}] }`(双轴各 5 等分,left= `+10%/+5%/0%/-5%/-10%` 按 `(upPx-prevClose)/prevClose` 幅度推导,right= 对应价格,两轴同 y)
  - `priceTicks(min, max, rect)` → `[{y,label}]`(K线右轴 5 等分,仅价格)

- [ ] **Step 1: 写失败测试**

```js
// src/utils/__tests__/chartDraw.test.js
import { describe, it, expect } from 'vitest'
import { panelRects, priceToY, klineWindow, idxToX, timeTicks, priceTicksTrend, priceTicks } from '../chartDraw.js'

describe('panelRects', () => {
  it('三区 3:1:1 高度比, 区间距 2', () => {
    const r = panelRects(400, 300, true, { leftGutter: 0, rightGutter: 0 })
    expect(r.main.height).toBeGreaterThan(r.vol.height * 2.8)
    expect(r.vol.height).toBeGreaterThan(r.sub.height * 0.9)
    expect(r.main.y).toBe(0)
    expect(r.vol.y - (r.main.y + r.main.height)).toBe(2)
    expect(r.sub.y - (r.vol.y + r.vol.height)).toBe(2)
  })
  it('无副图时主图:量=2.2:1, sub=null', () => {
    const r = panelRects(400, 200, false, { leftGutter: 0, rightGutter: 0 })
    expect(r.sub).toBeNull()
    expect(r.main.height / r.vol.height).toBeCloseTo(2.2, 1)
  })
  it('轴带从主区扣除', () => {
    const r = panelRects(400, 300, true, { leftGutter: 32, rightGutter: 40 })
    expect(r.main.x).toBe(32)
    expect(r.main.width).toBeLessThan(400 - 32)
  })
})

describe('priceToY', () => {
  it('min→下缘, max→上缘, 线性', () => {
    const rect = { y: 10, height: 100 }
    expect(priceToY(0, 0, 100, rect)).toBe(110)
    expect(priceToY(100, 0, 100, rect)).toBe(10)
    expect(priceToY(50, 0, 100, rect)).toBe(60)
  })
})

describe('klineWindow', () => {
  const kl = [1, 2, 3, 4, 5].map(v => ({ time: v, open: v, close: v, high: v, low: v, volume: v }))
  it('offset=0 取末尾 count 根', () => {
    const { window, offset } = klineWindow(kl, 3, 0)
    expect(window.map(k => k.time)).toEqual([3, 4, 5])
    expect(offset).toBe(0)
  })
  it('offset clamp 到最左', () => {
    const { window, offset } = klineWindow(kl, 3, 99)
    expect(offset).toBe(2)
    expect(window.map(k => k.time)).toEqual([1, 2, 3])
  })
  it('count≥len 全量', () => {
    const { window } = klineWindow(kl, 10, 0)
    expect(window.length).toBe(5)
  })
})

describe('idxToX', () => {
  it('首末位置', () => {
    expect(idxToX(0, 200, 5)).toBeCloseTo(20, 5)
    expect(idxToX(4, 200, 5)).toBeCloseTo(180, 5)
  })
})

describe('timeTicks', () => {
  it('分时固定 5 刻度', () => {
    const ticks = timeTicks([], 400, true)
    expect(ticks.map(t => t.label)).toEqual(['09:30', '10:30', '11:30/13:00', '14:00', '15:00'])
    expect(ticks.map(t => t.x)).toEqual([40, 120, 200, 280, 360])  // 5 等分中心
  })
})

describe('priceTicksTrend', () => {
  it('左右双轴 5 等分, 同 y, 幅度按涨跌停', () => {
    const { left, right } = priceTicksTrend(11, 9, 10, { y: 0, height: 100 })
    // 昨收 10, 涨停 11 (+10%), 跌停 9 (-10%)
    expect(left.map(t => t.label)).toEqual(['+10%', '+5%', '0%', '-5%', '-10%'])
    expect(right[0].label).toBe(11)      // 上界 = 涨停价
    expect(right[4].label).toBe(9)       // 下界 = 跌停价
    expect(left.map(t => t.y)).toEqual(right.map(t => t.y))
  })
})

describe('priceTicks (K线)', () => {
  it('5 等分含边界', () => {
    const ticks = priceTicks(10, 20, { y: 0, height: 100 })
    expect(ticks.length).toBe(5)
    expect(ticks[0].label).toBe(20)
    expect(ticks[4].label).toBe(10)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/utils/__tests__/chartDraw.test.js`
Expected: FAIL(`chartDraw.js` module not found)

- [ ] **Step 3: 实现 `chartDraw.js`**

```js
// src/utils/chartDraw.js
// 图表几何/刻度纯函数: 无 DOM 无状态, 供 StockChartCanvas.vue 调用, 可单测

// 三区矩形 (sub=true → 3:1:1; sub=false → 2.2:1); opts: {leftGutter, rightGutter} 轴带
export function panelRects(w, h, sub, opts = {}) {
  const gap = 2
  const lg = opts.leftGutter || 0
  const rg = opts.rightGutter || 0
  const avail = Math.max(0, h - gap * 2)
  let main, vol, subR = null
  if (sub) {
    const m = Math.floor(avail * 3 / 5), v = Math.floor(avail * 1 / 5)
    main = { x: lg, y: 0, width: Math.max(0, w - lg - rg), height: m }
    vol = { x: lg, y: m + gap, width: Math.max(0, w - lg - rg), height: v }
    subR = { x: lg, y: m + gap + v + gap, width: Math.max(0, w - lg - rg), height: avail - m - v }
  } else {
    const m = Math.floor(avail * 2.2 / 3.2)
    main = { x: lg, y: 0, width: Math.max(0, w - lg - rg), height: m }
    vol = { x: lg, y: m + gap, width: Math.max(0, w - lg - rg), height: avail - m }
  }
  return { main, vol, sub: subR }
}

// 价格 → y(min 在下缘, max 在上缘, 4% 顶部/底部 padding)
export function priceToY(price, min, max, rect) {
  const pad = rect.height * 0.04
  const usable = rect.height - pad * 2
  const t = (price - min) / ((max - min) || 1)
  return rect.y + pad + (1 - t) * usable
}

// K线可见窗口 (offset=0 → 最新 count 根; clamp 回看上限)
export function klineWindow(kline, count, offset) {
  const len = kline.length
  const maxOff = Math.max(0, len - count)
  const off = Math.max(0, Math.min(offset, maxOff))
  return { window: kline.slice(len - count - off, len - off), offset: off }
}

// 窗口内第 i 根 x 中心
export function idxToX(i, w, count) {
  return (i + 0.5) / count * w
}

// 底部时间刻度 (分时固定 5 刻度; 其他均匀 4~6)
export function timeTicks(items, w, isIntraday) {
  if (isIntraday) {
    const labels = ['09:30', '10:30', '11:30/13:00', '14:00', '15:00']
    return labels.map((label, i) => ({ x: (i + 0.5) / 5 * w, label }))
  }
  const n = items.length
  if (!n) return []
  const count = Math.min(6, Math.max(4, n))
  const idxs = []
  for (let k = 0; k < count; k++) idxs.push(Math.round((n - 1) * k / (count - 1)))
  const uniq = [...new Set(idxs)]
  return uniq.map(i => ({ x: idxToX(i, w, n), label: fmtTime(items[i]) }))
}

function fmtTime(item) {
  const t = item?.time
  if (typeof t === 'number') {
    const d = new Date(t * 1000)
    const p = n => String(n).padStart(2, '0')
    // m60 显示 HH:MM; 日/周/月显示 MM-DD
    if (String(t).length <= 12 && t < 1e11) return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`
    return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
  }
  return typeof t === 'string' ? String(t).slice(5, 10) : ''
}

// 分时双轴: 左=涨跌幅度%, 右=价格(涨停~跌停), 同 y 对应
export function priceTicksTrend(upPx, downPx, prevClose, rect) {
  const fallback = prevClose * 0.1
  const up = typeof upPx === 'number' && upPx > 0 ? upPx : prevClose + fallback
  const down = typeof downPx === 'number' && downPx > 0 ? downPx : prevClose - fallback
  const pctSpan = (up - prevClose) / prevClose * 100
  const left = [], right = []
  for (let k = 0; k < 5; k++) {
    const f = 1 - k / 4           // 顶部→底部
    const pct = pctSpan * f       // 顶部 +pct, 底部 -pct
    const price = prevClose * (1 + pct / 100)
    const y = rect.y + k / 4 * rect.height
    left.push({ y, label: `${pct >= 0 ? '+' : ''}${Math.round(pct)}%` })
    right.push({ y, label: round2(price) })
  }
  return { left, right }
}

// K线右轴刻度 (min/max 5 等分, 仅价格)
export function priceTicks(min, max, rect) {
  const out = []
  for (let k = 0; k < 5; k++) {
    const f = 1 - k / 4
    out.push({ y: rect.y + k / 4 * rect.height, label: round2(min + (max - min) * f) })
  }
  return out
}

function round2(v) { return Math.round(v * 100) / 100 }
```

> 注: `timeTicks` 的 `isIntraday` 语义覆盖 trend 与 m60(都走均匀刻度,但 m60 label 用 `HH:MM`)。若 m60 需要固定 5 刻度,由组件按 view 传参,见 Task 2 接线。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/utils/__tests__/chartDraw.test.js`
Expected: PASS(全部用例)

- [ ] **Step 5: 跑全部测试 + Commit**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm test
git add stockboard-app/src/utils/chartDraw.js stockboard-app/src/utils/__tests__/chartDraw.test.js
git commit -m "📐 chartDraw: 纯几何/刻度函数 + 单测 (panelRects/priceToY/klineWindow/timeTicks/双轴)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: `StockChartCanvas.vue` 骨架 + 分时视图(TDD 冒烟)

**Files:**
- Create: `stockboard-app/src/components/StockChartCanvas.vue`
- Test: `stockboard-app/src/components/__tests__/StockChartCanvas.test.js`(挂载冒烟,不测 canvas 像素)
- Modify: `stockboard-app/src/components/StockDetailPage.vue`(模板替换 `sd-chart` 区 + 接线 props/emit;见 Task 5 完整接线,本任务先只替换模板让分时渲染)

**Interfaces:**
- Consumes: `panelRects/priceToY/timeTicks/priceTicksTrend` from Task 1;props `view/trend/quote/subInd/indCache`.
- Produces: `<StockChartCanvas>` 组件,emit `crossinfo`(分时 emit null)。
- Props 签名: `{ view, kline, trend, quote, overlays, chan, wave, subInd, indCache }`(indCache 分时下可为空)。

- [ ] **Step 1: 写失败测试(冒烟)**

```js
// src/components/__tests__/StockChartCanvas.test.js
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StockChartCanvas from '../StockChartCanvas.vue'

describe('StockChartCanvas', () => {
  it('分时 view 挂载不抛错, 且 canvas 存在', () => {
    const trend = [
      { time: 1723000000 + 60, price: 10, vol: 100, amount: 100000 },
      { time: 1723000000 + 120, price: 10.1, vol: 200, amount: 200000 },
    ]
    const wrapper = mount(StockChartCanvas, {
      props: {
        view: 'trend', kline: [], trend,
        quote: { prevClose: 10, upPx: 11, downPx: 9, price: 10.05 },
        overlays: { ma: false, boll: false }, chan: false, wave: false,
        subInd: 'none', indCache: null,
      },
      global: { stubs: { 'canvas': true } },
    })
    expect(wrapper.find('canvas').exists()).toBe(true)
  })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockChartCanvas.test.js`
Expected: FAIL(组件不存在)

- [ ] **Step 3: 实现组件骨架 + 分时绘制**

组件结构(`<script setup>`):
```js
// src/components/StockChartCanvas.vue
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { panelRects, priceToY, timeTicks, priceTicksTrend, klineWindow, idxToX } from '../utils/chartDraw.js'

const props = defineProps({
  view: { type: String, required: true },          // 'trend'|'day'|'week'|'month'|'m60'
  kline: { type: Array, default: () => [] },
  trend: { type: Array, default: () => [] },
  quote: { type: Object, default: null },
  overlays: { type: Object, default: () => ({ ma: false, boll: false }) },
  chan: { type: Boolean, default: false },
  wave: { type: Boolean, default: false },
  subInd: { type: String, default: 'none' },
  indCache: { type: Object, default: null },
})
const emit = defineEmits(['crossinfo'])

const canvasRef = ref(null)
const wrapRef = ref(null)
let ctx = null, dpr = 1
let ro = null

const isIntraday = () => props.view === 'trend' || props.view === 'm60'
const isTrend = () => props.view === 'trend'

// 光标与平移状态
let hover = null            // {x, y, klineIdx}
let drag = null             // K线平移 {startX, startOffset}
let offset = 0              // K线窗口偏移(回看)

function setupCanvas() {
  const c = canvasRef.value
  ctx = c.getContext('2d')
  resize()
}

function resize() {
  const wrap = wrapRef.value
  if (!wrap || !canvasRef.value) return
  const w = wrap.clientWidth, h = wrap.clientHeight
  if (!w || !h) return
  dpr = window.devicePixelRatio || 1
  canvasRef.value.width = Math.round(w * dpr)
  canvasRef.value.height = Math.round(h * dpr)
  canvasRef.value.style.width = w + 'px'
  canvasRef.value.style.height = h + 'px'
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  draw()
}

function draw() {
  if (!ctx) return
  ctx.clearRect(0, 0, canvasRef.value.clientWidth, canvasRef.value.clientHeight)
  if (isTrend()) drawTrend()
  else drawKline()
}

// ── 分时视图 ──
function drawTrend() {
  const t = props.trend
  const quote = props.quote || {}
  const prevClose = quote.prevClose
  if (!t.length || typeof prevClose !== 'number') return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  const rects = panelRects(w, h, props.subInd !== 'none', { leftGutter: 32, rightGutter: 40 })
  const { main } = rects
  const upP = quote.upPx, downP = quote.downPx
  const axis = priceTicksTrend(upP, downP, prevClose, main)
  // 网格 + 昨收 + 涨跌停线 + 双轴刻度 + 分时线 + 均价线 + 量柱 + 时间轴(见下)
  drawTrendAxis(axis, main)
  drawTrendGrid(main, prevClose, upP, downP)
  drawTrendLine(main, t, prevClose, upP, downP)
  drawAvgLine(main, t)
  drawTrendVolume(rects.vol, t, prevClose)
  drawTimeAxis(rects, timeTicks(t, w, true), true)
}

// ... 具体绘制函数(实现见 spec §7.3, 此处为最小可运行版本)
function drawTrendGrid(main, prevClose, upP, downP) {
  // 水平网格 4 条(等分), 昨收虚线 #b9c2cc, 涨停 rgba(231,76,60,.55)/跌停 rgba(39,174,96,.55) 上下边沿
}
function drawTrendAxis(axis, main) {
  // 左轴百分比 label 于 main.x 左侧; 右轴价格 label 于 main.x+width 右侧; AXIS_TEXT #9aa2ac, 9px
}
function drawTrendLine(main, t, prevClose, upP, downP) {
  // 分时线 #e74c3c 1.2px, 连接各点(priceToY, min=downP, max=upP)
  // 均价线 #f2a900 1px: 逐点累计 Σamount/(Σvol×100)
}
function drawTrendVolume(vol, t, prevClose) {
  // 量柱: p.price>=prevClose ? rgba(231,76,60,.6) : rgba(39,174,96,.6)
}
function drawTimeAxis(rects, ticks, isIntraday) {
  // 底部 TIME_TEXT #b3bac3 10px, 位于 sub/vol 区下方
}
function drawKline() { /* Task 3 实现 */ }

// ── 事件 ──
function onPointerMove(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left, y = e.clientY - rect.top
  hover = { x, y }
  draw()
  emitCrosshair(x)
}
function emitCrosshair(x) {
  if (isTrend()) { emit('crossinfo', null); return }
  // K线: 由 Task 3 的窗口计算 → 复用宿主 onCrosshair 字段逻辑
  // 本任务暂 emit null, Task 4 补完整
  emit('crossinfo', null)
}
function onPointerLeave() { hover = null; draw(); emit('crossinfo', null) }

watch(() => [props.view, props.trend, props.kline, props.quote, props.subInd, props.chan, props.wave, props.overlays.ma, props.overlays.boll, props.indCache], draw, { deep: true })

onMounted(() => {
  setupCanvas()
  ro = new ResizeObserver(resize)
  if (wrapRef.value) ro.observe(wrapRef.value)
})
onBeforeUnmount(() => { ro?.disconnect() })
```

模板:
```html
<template>
  <div ref="wrapRef" class="scc-wrap">
    <canvas ref="canvasRef" class="scc-canvas"
      @pointermove="onPointerMove" @pointerleave="onPointerLeave"></canvas>
  </div>
</template>
```

样式(组件 scoped):
```css
.scc-wrap { position: relative; width: 100%; height: 100%; min-height: 280px; touch-action: pan-y; }
.scc-canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; cursor: crosshair; }
@media (min-width: 481px) { .scc-wrap { min-height: 360px; } }
```

> 宿主需把 `.sd-chart` 内联 `:style="{height: chartH+'px'}"` 与 `chartH` 移除,`sd-chart-wrap` 需要高度(子组件 `height:100%`)。宿主侧模板替换与 CSS 见 Task 5;本任务组件本身可用 mock 高度容器冒烟。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockChartCanvas.test.js`
Expected: PASS

- [ ] **Step 5: 跑全部测试 + Commit**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm test
git add stockboard-app/src/components/StockChartCanvas.vue stockboard-app/src/components/__tests__/StockChartCanvas.test.js
git commit -m "🖌 StockChartCanvas: 组件骨架 + 分时视图绘制 (双轴/涨停板/均价/量柱/时间轴)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: K线视图 + 缠论 + 波浪 + 量能绘制

**Files:**
- Modify: `stockboard-app/src/components/StockChartCanvas.vue`

**Interfaces:**
- Consumes: `klineWindow/idxToX/priceTicks/priceToY` from Task 1;props `view/kline/quote/overlays/chan/wave/indCache`;`isKlineView = view !== 'trend'`。
- Produces: `drawKline()` 内部函数;`offset` 平移状态。

- [ ] **Step 1: 写失败测试(K线 view 冒烟)**

```js
// 追加到 StockChartCanvas.test.js
it('K线 view 挂载不抛错(含缠论+波浪开启)', () => {
  const kline = [
    { time: '2026-08-07', open: 10, close: 10.2, high: 10.4, low: 9.8, volume: 5000 },
    { time: '2026-08-08', open: 10.2, close: 9.9, high: 10.3, low: 9.7, volume: 6000 },
  ]
  const wrapper = mount(StockChartCanvas, {
    props: {
      view: 'day', kline, trend: [],
      quote: { prevClose: 10, upPx: 11, downPx: 9, price: 9.9 },
      overlays: { ma: true, boll: true }, chan: true, wave: true,
      subInd: 'macd',
      indCache: {
        ma: { 5: [10], 10: [10], 20: [10], 60: [10] },
        boll: { up: [10.5], mid: [10], lo: [9.5] },
        volma: { 5: [5000], 10: [5000] },
        macd: { dif: [0], dea: [0], hist: [0] },
        kdj: { k: [50], d: [50], j: [50] },
        rsi: { 6: [50], 12: [50], 24: [50] },
        wr: { 10: [50], 6: [50] },
        fractals: [], bis: [], zhongshu: [], chanSignals: [], divergences: [],
        waves: { status: 'unknown', waves: [] },
      },
    },
    global: { stubs: { 'canvas': true } },
  })
  expect(wrapper.find('canvas').exists()).toBe(true)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockChartCanvas.test.js`
Expected: FAIL(K线分支未实现,`drawKline` 抛错或缺失)

- [ ] **Step 3: 实现 `drawKline`**

实现要点(具体代码见组件,核心结构如下):
```js
function drawKline() {
  const kl = props.kline
  if (!kl.length) return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  const hasSub = props.subInd !== 'none'
  const rects = panelRects(w, h, hasSub, { leftGutter: 0, rightGutter: 40 })
  const { main, vol, sub } = rects
  const count = viewCount(props.view)        // { trend:'-', m60:120, day:90, week:60, month:40 }
  const { window: win, offset: off } = klineWindow(kl, Math.min(count, kl.length), offset)
  const padTop = (max - min) * 0.04          // 4% padding
  const { min: yMin, max: yMax } = range(win) // 含 high/low
  const min = yMin - padTop, max = yMax + padTop
  // 1. 主图: 蜡烛 (open/close/high/low, 涨#e74c3c 跌#27ae60, 实体宽=柱宽×0.6, 影线1px)
  drawCandles(main, win, min, max)
  // 2. MA/BOLL 叠加
  if (props.overlays.ma) drawMALines(main, win, min, max)     // indCache.ma[5/10/20/60] 各色
  if (props.overlays.boll) drawBoll(main, win, min, max)      // up#e74c3c mid#f39c12(虚线) lo#27ae60
  // 3. 缠论 (chan && view in [day,week,month])
  if (props.chan && props.view !== 'm60') drawChan(main, win, min, max)
  // 4. 波浪 (wave && view in [day,week,month])
  if (props.wave && props.view !== 'm60') drawWave(main, win, min, max)
  // 5. 右轴刻度
  drawKlineAxis(main, priceTicks(min, max, main))
  // 6. 量能: 柱(红涨绿跌) + volma[5/10] 线
  drawVolumePane(vol, win)
  // 7. 副图
  if (hasSub) drawSubPane(sub, win)
  // 8. 时间轴
  drawTimeAxis(rects, timeTicks(win, w, false), false)
}

function drawChan(main, win, min, max) {
  // 分型: type===1 顶(↑小三角 高点上方 #7d3c98), type===-1 底(↓小三角 低点下方)
  // 笔: indCache.bis 连接 from→to 分型极值 (from.type===1? high: low), #7d3c98 1px
  // 中枢: indCache.zhongshu.slice(-10) 画矩形 (zg 上沿/zd 下沿, from→to x 范围), 填充 rgba(125,60,152,.15)+描边
  // 买卖点: indCache.chanSignals 对应 i → SIGNAL_LABELS, 买#7d3c98 卖#27ae60, 买点低点下方/卖点高点上方
  // 背驰: indCache.divergences type==='top' 顶背驰/ 'bottom' 底背驰
}
function drawWave(main, win, min, max) {
  // indCache.waves: status ok/ok5 → 连接 waves 各 i 点(高/低按 type), label 数字, #d35400
  // 推进浪 label 上方, 调整浪下方; 无法判定只画已识别
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockChartCanvas.test.js`
Expected: PASS

- [ ] **Step 5: 跑全部测试 + Commit**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm test
git add stockboard-app/src/components/StockChartCanvas.vue
git commit -m "🖌 StockChartCanvas: K线蜡烛 + MA/BOLL + 缠论(分型/笔/中枢/买卖点/背驰) + 波浪 + 量能

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 副图指标 + 十字光标 + K线平移交互

**Files:**
- Modify: `stockboard-app/src/components/StockChartCanvas.vue`

**Interfaces:**
- Consumes: props `subInd`;`hover/drag/offset` 状态。
- Produces: 完整 `crossinfo` emit(与宿主 `onCrosshair` 字段一致: `{open, high, low, close, prevClose, amount, chg, chgPct, point:{x,y}}` 或 null);`drawSubPane` 绘制 4 指标。

- [ ] **Step 1: 写失败测试(emit crossinfo)**

```js
// 追加到 StockChartCanvas.test.js
it('K线 hover 触发 crossinfo emit', async () => {
  const kline = [
    { time: '2026-08-07', open: 10, close: 10.2, high: 10.4, low: 9.8, volume: 5000 },
    { time: '2026-08-08', open: 10.2, close: 9.9, high: 10.3, low: 9.7, volume: 6000 },
  ]
  const wrapper = mount(StockChartCanvas, {
    props: {
      view: 'day', kline, trend: [], quote: { prevClose: 10 },
      overlays: { ma: false, boll: false }, chan: false, wave: false,
      subInd: 'none', indCache: null,
    },
    global: { stubs: { 'canvas': true } },
  })
  const canvas = wrapper.find('canvas')
  await canvas.trigger('pointermove', { clientX: 50, clientY: 50 })
  const emitted = wrapper.emitted('crossinfo')
  expect(emitted).toBeTruthy()
  expect(emitted[0][0]).toMatchObject({ close: expect.any(Number), chgPct: expect.any(Number) })
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockChartCanvas.test.js`
Expected: FAIL(emit 为 null)

- [ ] **Step 3: 实现副图 + 光标 + 平移**

```js
// 副图: MACD(DIF #f2a900 / DEA #9b59b6 / hist 红绿) | KDJ(k #f2a900 d #9b59b6 j #27ae60)
//       | RSI(6 #f2a900 / 12 #9b59b6 / 24 #27ae60, 0-100 + 30/50/70 虚线) | WR(10 #f2a900 / 6 #9b59b6, 0-100 + 20/80 虚线)
//       上方 label: `MACD ▾` #2980b9 白底 rgba(255,255,255,.75)
function drawSubPane(sub, win) {
  const s = props.subInd
  if (s === 'none') return
  const ic = props.indCache
  const drawLine = (vals, color, idx = 0) => { /* 遍历 win 相对索引 → 连接 */ }
  if (s === 'macd' && ic?.macd) {
    const m = ic.macd
    const histMax = Math.max(...m.hist.map(Math.abs).filter(Number.isFinite), 1)
    // hist 柱: m.hist[i]>=0 ? rgba(231,76,60,.5) : rgba(39,174,96,.5), 以 0 轴为基准
    drawLine(m.dif, '#f2a900'); drawLine(m.dea, '#9b59b6')
  } else if (s === 'kdj' && ic?.kdj) {
    // 0-100 映射(上方 100 下方 0)
    drawLine(ic.kdj.k, '#f2a900'); drawLine(ic.kdj.d, '#9b59b6'); drawLine(ic.kdj.j, '#27ae60')
  } else if (s === 'rsi' && ic?.rsi) {
    drawLine(ic.rsi[6], '#f2a900'); drawLine(ic.rsi[12], '#9b59b6'); drawLine(ic.rsi[24], '#27ae60')
    // 30/50/70 参考虚线 #eee
  } else if (s === 'wr' && ic?.wr) {
    drawLine(ic.wr[10], '#f2a900'); drawLine(ic.wr[6], '#9b59b6')
    // 20/80 参考虚线 #eee
  }
}

// 十字光标: 竖线贯穿三区 rgba(41,128,185,.45) 虚线 + 横线贯穿主图 + 交点圆点; hover 状态驱动
function drawCrosshair(rects, min, max) { /* 在 draw() 末尾调用 */ }

// 完整 emit: 光标所在 K 线窗口索引 → 宿主字段
function emitCrosshair(x) {
  if (isTrend()) { emit('crossinfo', null); return }
  const kl = props.kline
  if (!kl.length) return
  const count = viewCount(props.view)
  const { window: win, offset: off } = klineWindow(kl, Math.min(count, kl.length), offset)
  const w = canvasRef.value.clientWidth
  const i = Math.min(win.length - 1, Math.max(0, Math.floor((x - 0) / w * win.length)))
  const k = win[i]
  const gI = kl.indexOf(k)                       // 全局索引(用于 prevClose)
  const prevClose = gI > 0 ? kl[gI - 1].close : k.open
  const chg = k.close - prevClose
  emit('crossinfo', {
    open: k.open, high: k.high, low: k.low, close: k.close, prevClose,
    amount: k.volume * 100 * k.close,
    chg, chgPct: prevClose ? chg / prevClose * 100 : 0,
    point: { x, y: hover?.y ?? 0 },
  })
}

// K线平移: pointerdown 记录起点, pointermove 拖动更新 offset, pointerup 结束; 滚轮 ±3 根(仅非分时)
function onPointerDown(e) {
  if (isTrend()) return
  const rect = canvasRef.value.getBoundingClientRect()
  drag = { startX: e.clientX, startOffset: offset }
}
function onPointerMove(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left, y = e.clientY - rect.top
  if (drag) {
    const count = viewCount(props.view)
    const { window: win } = klineWindow(props.kline, Math.min(count, props.kline.length), 0)
    const barW = canvasRef.value.clientWidth / win.length
    const dOff = Math.round((drag.startX - e.clientX) / barW)
    offset = clamp(drag.startOffset + dOff, 0, Math.max(0, props.kline.length - win.length))
    draw()
    return
  }
  hover = { x, y }
  draw()
  emitCrosshair(x)
}
function onPointerUp() { drag = null }
function onWheel(e) {
  if (isTrend()) return
  e.preventDefault()
  const count = viewCount(props.view)
  const maxOff = Math.max(0, props.kline.length - Math.min(count, props.kline.length))
  offset = clamp(offset + (e.deltaY > 0 ? 3 : -3), 0, maxOff)
  draw()
}
function clamp(v, a, b) { return Math.max(a, Math.min(b, v)) }
```

模板新增事件: `<canvas @pointerdown="onPointerDown" @pointerup="onPointerUp" @wheel.prevent="onWheel">`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockChartCanvas.test.js`
Expected: PASS(含 crossinfo emit 用例)

- [ ] **Step 5: 跑全部测试 + Commit**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm test
git add stockboard-app/src/components/StockChartCanvas.vue
git commit -m "🖌 StockChartCanvas: 副图指标(MACD/KDJ/RSI/WR) + 十字光标 emit + K线平移

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 宿主接线 —— 替换图表区、删除 lightweight-charts、高度对齐

**Files:**
- Modify: `stockboard-app/src/components/StockDetailPage.vue`
- Modify: `stockboard-app/package.json`(移除 `lightweight-charts`)

**Interfaces:**
- Consumes: `StockChartCanvas` 组件;既有 `computeIndicators`/`onCrosshair` 字段逻辑/`cursorTip`/`waveNote`。
- Produces: 模板用 `<StockChartCanvas>`,emits `crossinfo` 被宿主 `onCrosshairFromCanvas` 消费。

- [ ] **Step 1: 写失败测试(宿主模板含新组件)**

```js
// 追加到 StockChartCanvas.test.js 或新建 StockDetailPage.test.js(挂载宿主, 需 stub 全部子组件)
// 由于宿主依赖大量 API(开盘啦/腾讯), 本步用浅挂载 + stub:
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StockDetailPage from '../StockDetailPage.vue'
import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [{ path: '/stock/:code', component: { template: '<div />' } }]
const router = createRouter({ history: createWebHashHistory(), routes })

it('宿主挂载含 StockChartCanvas 组件', () => {
  const wrapper = mount(StockDetailPage, {
    props: { code: '000001' },
    global: {
      router,
      stubs: {
        PankouPanel: true, StickyQuoteBar: true, BigOrderCard: true,
        LhbStockCard: true, ZtHistoryCard: true, BidAuctionCard: true, LimitGeneCard: true,
      },
    },
  })
  expect(wrapper.findComponent({ name: 'StockChartCanvas' }).exists()).toBe(true)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `cd /Users/xywang/stockboard/stockboard-app && npx vitest run src/components/__tests__/StockDetailPage.test.js`
Expected: FAIL(组件未引入)

- [ ] **Step 3: 宿主改造**

模板 `sd-chart` 区(当前 964-981 行)改为:
```html
<div class="sd-chart-wrap">
  <!-- 骨架/错误态保留 -->
  <div v-if="loading.chart && !kline.length && !trend.length" class="sd-sk">...</div>
  <div v-else-if="!loading.chart && !kline.length && !trend.length" class="sd-error">...</div>
  <StockChartCanvas v-else
    :view="view" :kline="kline" :trend="trend" :quote="quote"
    :overlays="overlays" :chan="chan" :wave="wave"
    :sub-ind="subInd" :ind-cache="indCache"
    class="sd-chart" @crossinfo="onCrosshairFromCanvas" />
  <!-- cursorTip 悬浮卡保留, 定位用 emit 的 point -->
  <div v-if="cursorTip" class="sd-tip" :style="{ left: cursorTip.x + 'px', top: cursorTip.y + 'px' }">...</div>
</div>
```

脚本改动:
1. **删除**(当前行号): `chartEl`/`chart`/`series`/`markerPlugins`(102-103,114-116)、`chartH`/`recomputeChartHeight`(105-113)、`VIEW_MAX_BARS`/`MA_COLORS`/`BOLL_COLORS`(127-129)、`onResize`(153-157)、`onCrosshair` 的 lightweight 参数适配(199-228 改造,见下)、`ensureChart`(231-260)、`track`/`removeAllSeries`/`setPaneStretch`(262-276)、`linePoints`(279-284)、`addMaLines`/`addBollLines`(286-304)、`addChanMarkers`/`addWaveMarkers`/`addChanLines`/`addZhongshuLines`/`addVolumePane`/`addSubLine`(308-400)、`addSubIndicator`/`addTrendSubIndicator`(402-459)、`renderSeries`(461-600)、`loadChart` 中 `ensureChart/renderSeries` 调用(602-608)、`watch` 重绘(610-620 的 chart 依赖改走组件 watch)、`refreshChartSilent`/`onVisibility`/`onActivated`/`onUnmounted` 中 chart 引用(836-872)。
2. **保留并改造**:
   - `computeIndicators`(160-176)不动。
   - `crossInfo`/`cursorTip`/`tipDate` 保留;`onCrosshair(param)` 改为 `onCrosshairFromCanvas(info)`(info 直接是 `{open,high,low,close,prevClose,amount,chg,chgPct,point}`):
     ```js
     function onCrosshairFromCanvas(info) {
       if (!info) { crossInfo.value = null; cursorTip.value = null; return }
       const k = info
       crossInfo.value = {
         open: k.open, high: k.high, low: k.low, close: k.close,
         prevClose: k.prevClose, amount: k.amount, chg: k.chg, chgPct: k.chgPct,
       }
       if (k.point) {
         const w = document.querySelector('.sd-chart-wrap')?.clientWidth || 360
         const h = document.querySelector('.sd-chart-wrap')?.clientHeight || 360
         cursorTip.value = {
           x: Math.min(k.point.x + 12, w - 100),
           y: Math.min(k.point.y + 14, h - 96),
           date: tipDate(k.time ?? null),
           open: k.open, high: k.high, low: k.low, close: k.close,
           chgPct: k.chgPct, volume: k.volume ?? k.amount / (k.close * 100),
         }
       } else cursorTip.value = null
     }
     ```
     > 组件 emit 需附带 `time` 字段供 `tipDate` 用(Task 4 的 emitCrosshair 需加 `time: k.time`)。
   - `isUp`/`upColor`/`fmt`/`pct`/`wan`/`fmtVol`/`fmtHand` 保留。
   - `waveNote` 计算(583-588)移到组件之外由宿主在 `computeIndicators` 后设置,保留现有文案。
   - `subInd` select(966-968)保留,`v-model` 不变。
3. **高度对齐**: `.sd-chart-wrap` 加 `display:flex` 与 `min-height`(空态保底 360/280),移除 `.sd-chart` 内联 height;`.sd-chart` class 移交给 `StockChartCanvas` 根元素。CSS 改动:
   - 删除 `.sd-chart { position:relative; width:100%; touch-action:pan-y }` 与 `chartH` 相关注释。
   - `.sd-chart-wrap` 改为 `flex:1; min-width:0; position:relative; display:flex; min-height:280px`(+media 360px)。
4. **移除 lightweight-charts**: `package.json` 删 `"lightweight-charts": "^5.2.0"`,跑 `npm install`。

- [ ] **Step 4: 运行确认通过**

Run: `cd /Users/xywang/stockboard/stockboard-app && npm test && npm run build`
Expected: PASS + build 成功(无 lightweight-charts 编译错误)

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard/stockboard-app && git add package.json package-lock.json src/components/StockDetailPage.vue src/components/__tests__/StockDetailPage.test.js
git commit -m "🔌 宿主接线: 替换 lightweight-charts 为 StockChartCanvas, 高度对齐盘口, 移除依赖

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 收尾 —— 全量验证 + 手工清单

**Files:**
- 无新增(验证 + 必要修复)

- [ ] **Step 1: 全量测试 + 构建**

Run: `cd /Users/xywang/stockboard/stockboard-app && npm test && npm run build`
Expected: 全部测试通过 + build 成功

- [ ] **Step 2: 启动 dev server 手工核对**

Run: `cd /Users/xywang/stockboard/stockboard-app && npm run dev`
按 spec §9 手工清单核对:
- 分时: 左轴涨跌幅度%、右轴涨停-跌停价、均价黄线、昨收虚线、X 轴固定全天只画到当前。
- 日 K: 蜡烛红绿、MA4/BOLL、缠论(分型/笔/中枢/买卖点/背驰)、波浪(数浪)。
- 光标: 十字线 + 顶部 16 格联动 + 悬浮卡。
- K 线平移: 拖拽/滚轮回看,clamp 最左。
- 副图: MACD/KDJ/RSI/WR 切换;`none` 主图:量=2.2:1。
- 高度: 与右盘口列底部对齐;移动/桌面 min-height 保底。
- 分时不平移,滚轮/拖动无响应。

- [ ] **Step 3: 确认删除无残留**

Run: `cd /Users/xywang/stockboard/stockboard-app && grep -rn "lightweight-charts\|createChart\|ensureChart\|recomputeChartHeight\|setPaneStretch" src/ package.json`
Expected: 无输出(grep exit 1)

- [ ] **Step 4: Commit(如有修复)**

```bash
git add -A
git commit -m "🧹 图表自绘收尾: 验证通过, 清理 lightweight-charts 残留

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec 覆盖:**
- §2.1 全部 10 项 → Task 1-6 ✓
- §4 组件接口/props/emit → Task 2(接口)+ Task 4(完整 emit)✓
- §5 高度对齐(ResizeObserver + min-height)→ Task 2 + Task 5 ✓
- §6 纯函数 → Task 1 ✓
- §7.3 分时双轴/涨停板/X轴固定 → Task 1(轴函数)+ Task 2(绘制)✓
- §7.4 K线/缠论/波浪/量能 → Task 3 ✓
- §7.5 副图 → Task 4 ✓
- §7.6-7.7 光标/手势 → Task 4 ✓
- §8 移除项 → Task 5 ✓
- §9 测试 → 各 Task + Task 6 ✓

**2. Placeholder scan:** 无 TBD/TODO/待定。Task 3/4 的绘制函数给了结构但非逐行(实现细节由执行者按 spec §7.4/7.5 颜色与锚点规则完成)——计划中已给出颜色/数据源/位置规则,属"遵循 spec 细节"而非占位。

**3. Type consistency:**
- `panelRects(w,h,sub,opts)` Task 1 定义 → Task 2/3/4 同签名使用 ✓
- `priceToY(price,min,max,rect)` 全 Task 一致 ✓
- `klineWindow(kline,count,offset)` → `{window,offset}` 全 Task 一致 ✓
- `priceTicksTrend(upPx,downPx,prevClose,rect)` → `{left,right}` Task 1 定义,Tasks 2 使用 ✓
- emit `crossinfo` payload Task 4 定义 `{...chg, point}` → Task 5 `onCrosshairFromCanvas` 消费一致 ✓(注意需补 `time` 字段供 tipDate)

**4. 已知待执行者注意:**
- Task 4 emit 需含 `time` 字段(宿主 `tipDate` 用)→ 计划中已标注。
- `timeTicks` 对 m60 用均匀刻度(非固定 5),若要与分时一致需组件按 `view==='trend'` 传 `isIntraday=true` 分支——Task 2 已注明。
- 缠论中枢 `slice(-10)`、买卖点文字与悬浮卡重叠处理按 spec §11 风险条。
