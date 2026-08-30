<script setup>
// Canvas 2D 自绘图表: 分时(涨停板双轴/X轴固定全天) + K线(蜡烛/MA/BOLL/缠论/波浪) + 副图指标
// 数据全部由宿主 computeIndicators 计算后经 props 传入; 本组件只画 + 发射 crossinfo
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import {
  panelRects, priceToY, klineWindow, idxToX, timeTicks, priceTicksTrend, priceTicks, trendX, trendMinute, TREND_VMIN,
} from '../utils/chartDraw.js'
import { calcMACD, calcKDJ, calcRSI, calcWR, VIEW_MAX_BARS } from '../utils/indicators.js'
import { fmtHand } from '../utils/pankou.js'

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
let ctx = null
let dpr = 1
let ro = null

// 光标与平移状态
let hover = null            // {x, y, klineIdx}
let drag = null             // K线平移(桌面鼠标拖动) {startX, startOffset}
let offset = 0              // K线窗口偏移(回看)
let plotX = 0, plotW = 0    // 当前视图内容区 x 基准与宽(十字光标命中/分时 x 用)
let pointers = new Map()    // 活跃指针 pointerId → {x, y}(触屏多指跟踪)
let twoPan = null           // 双指平移基准 {startAvgX, startOffset}
let touch = null            // 触屏单指手势 {startX, startT, lastX, lastT, peakV, mode:'swipe'|'drag'}

// 手势判定: 快速甩动 = 滑动(平移窗口改可见日期); 慢移/按住 = 拖动(十字线跟手)
const SWIPE_PXMS = 0.6      // 瞬时速度 > 600px/s → 判定滑动
const HOLD_MS = 200         // 按下超过 200ms 仍无快速移动 → 判定拖动

let hideTimer = null        // 触屏选中后无操作 → 自动隐藏十字线+宫格回实时
let lastPointerType = ''    // 最近指针类型: 鼠标 hover 不自动隐藏, 触屏需要
const HIDE_MS = 2500        // 无操作自动隐藏延迟

// 颜色 token (spec §7.2, 唯一来源)
const C = {
  up: '#e74c3c', down: '#27ae60', accent: '#2980b9',
  avg: '#f2a900', signal: '#9b59b6', chan: '#7d3c98', wave: '#d35400',
  ma20: '#27ae60', ma60: '#2980b9', bollUp: '#e74c3c', bollMid: '#f39c12', bollLo: '#27ae60',
  grid: '#f2f4f7', preClose: '#b9c2cc', border: '#eef1f5',
  axisText: '#9aa2ac', timeText: '#b3bac3',
  volUp: 'rgba(231,76,60,.6)', volDown: 'rgba(39,174,96,.6)',
  histUp: 'rgba(231,76,60,.5)', histDown: 'rgba(39,174,96,.5)',
  crosshair: 'rgba(41,128,185,.45)',
}
const FONT = '10px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif'
const FONT_SM = '9px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif'
const SIGNAL_LABELS = { '1buy': '1买', '2buy': '2买', '3buy': '3买', '1sell': '1卖', '2sell': '2卖', '3sell': '3卖' }
const CHAN_MAX_ZHONGSHU = 10
const MA_COLORS = { 5: C.avg, 10: C.signal, 20: C.ma20, 60: C.ma60 }
const BOLL_COLORS = { up: C.bollUp, mid: C.bollMid, lo: C.bollLo }

const isTrend = () => props.view === 'trend'
const isKline = () => props.view !== 'trend'
const isKlineOf = v => props.view !== 'trend' && props.view !== 'm60' ? v : false

function setupCanvas() {
  const c = canvasRef.value
  if (!c) return
  ctx = c.getContext('2d')
  resize()
}

function resize() {
  const wrap = wrapRef.value
  if (!wrap || !canvasRef.value || !ctx) return
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
  if (!ctx || !canvasRef.value) return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  ctx.clearRect(0, 0, w, h)
  if (isTrend()) drawTrend()
  else drawKline()
  if (hover) drawCrosshair()
}

// ─────────────── 分时视图 ───────────────
function drawTrend() {
  const t = props.trend
  const quote = props.quote || {}
  const prevClose = quote.prevClose
  if (!t.length || typeof prevClose !== 'number' || !ctx) return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  // 东财式布局: 双轴标签叠在绘图区内侧边缘(见 drawTrendAxis), 绘图区拉通全宽 — 移动端折线不过窄;
  // 均价/最新信息行由宿主 HTML 渲染在画布上方(sd-tinfo), 不占画布
  const rects = panelRects(w, h, props.subInd !== 'none', { leftGutter: 0, rightGutter: 0 })
  const { main, vol, sub } = rects
  const upP = quote.upPx, downP = quote.downPx
  const axis = priceTicksTrend(upP, downP, prevClose, main)
  // y 轴上下界与刻度同源: upPx/downPx 缺失时用刻度的 ±10% fallback, 避免直接取 null 致 priceToY 全 NaN 整图空白
  const yMin = axis.down, yMax = axis.up
  plotX = main.x; plotW = main.width
  const x0 = main.x, w0 = main.width   // 分时 x 以内容区为基准, 避免线/量柱/副图越过 y 轴带

  // 1. 网格 + 竞价区底色 + 昨收 + 涨跌停线
  drawGrid(main)
  ctx.fillStyle = 'rgba(41,128,185,.05)'   // 集合竞价段(虚拟 [0,30])浅蓝底, 东财同款视觉区隔
  ctx.fillRect(x0, main.y, w0 * 30 / TREND_VMIN, main.height)
  drawHLine(main, prevClose, yMin, yMax, C.preClose, 'dashed')
  if (axis.up > axis.down) {
    drawHLine(main, axis.up, yMin, yMax, 'rgba(231,76,60,.55)', 'dashed')
    drawHLine(main, axis.down, yMin, yMax, 'rgba(39,174,96,.55)', 'dashed')
  }
  // 2. 左右双轴刻度
  drawTrendAxis(axis, main)
  // 3. 分时线 + 均价线(信息行在宿主 HTML)
  drawTrendLine(main, t, prevClose, yMin, yMax, x0, w0)
  drawAvgLine(main, t, prevClose, yMin, yMax, x0, w0)
  // 4. 量能
  drawTrendVolume(vol, t, prevClose, x0, w0)
  // 5. 副图(分时按 subInd 走伪K线: 指标由本组件从分时数据现算, 宿主 indCache 为空)
  if (sub && props.subInd !== 'none') {
    const tkl = t.map(p => ({ time: p.time, open: p.price, high: p.price, low: p.price, close: p.price, volume: p.vol }))
    drawSubPane(sub, tkl, { macd: calcMACD(tkl), kdj: calcKDJ(tkl), rsi: calcRSI(tkl), wr: calcWR(tkl) }, x0, w0, 0, true)
  }
  // 6. 时间轴
  drawTimeAxis(rects, timeTicks(t, w0, true), true, x0)
}

function drawGrid(rect) {
  if (!ctx) return
  ctx.strokeStyle = C.grid
  ctx.lineWidth = 1
  ctx.beginPath()
  for (let k = 1; k < 4; k++) {
    const y = rect.y + k / 4 * rect.height
    ctx.moveTo(rect.x, y); ctx.lineTo(rect.x + rect.width, y)
  }
  ctx.stroke()
}

function drawHLine(rect, price, yMin, yMax, color, style) {
  if (!ctx) return
  const y = priceToY(price, yMin, yMax, rect)
  ctx.strokeStyle = color
  ctx.lineWidth = 1
  if (style === 'dashed') ctx.setLineDash([4, 3])
  else ctx.setLineDash([])
  ctx.beginPath()
  ctx.moveTo(rect.x, y); ctx.lineTo(rect.x + rect.width, y)
  ctx.stroke()
  ctx.setLineDash([])
}

function drawTrendAxis(axis, main) {
  if (!ctx) return
  ctx.font = FONT_SM
  ctx.fillStyle = C.axisText
  // 标签叠在绘图区内侧(东财式): 白色描边垫底, 折线/网格穿过时仍可读
  ctx.lineWidth = 3
  ctx.strokeStyle = 'rgba(255,255,255,.85)'
  for (const t of axis.left) {
    // 首尾刻度在面板边缘, 'middle' 会让文字半截出界被裁 → 按位置换 baseline
    ctx.textBaseline = t.y <= main.y + 6 ? 'top' : t.y >= main.y + main.height - 6 ? 'bottom' : 'middle'
    ctx.textAlign = 'left'
    ctx.strokeText(t.label, main.x + 3, t.y)
    ctx.fillText(t.label, main.x + 3, t.y)
  }
  for (const t of axis.right) {
    ctx.textBaseline = t.y <= main.y + 6 ? 'top' : t.y >= main.y + main.height - 6 ? 'bottom' : 'middle'
    ctx.textAlign = 'right'
    ctx.strokeText(String(t.label), main.x + main.width - 3, t.y)
    ctx.fillText(String(t.label), main.x + main.width - 3, t.y)
  }
  ctx.lineWidth = 1
}

function drawTrendLine(main, t, prevClose, yMin, yMax, x0 = 0, w0 = 0) {
  if (!ctx || !t.length) return
  const w = w0 || canvasRef.value.clientWidth
  ctx.strokeStyle = C.up
  ctx.lineWidth = 1.2
  ctx.setLineDash([])
  ctx.beginPath()
  let started = false
  t.forEach(p => {
    const x = x0 + trendX(p.time, w)
    if (x < x0) return            // 午休/盘前/盘后点跳过, 前后点直接连线
    const y = priceToY(p.price, yMin, yMax, main)
    if (!started) { ctx.moveTo(x, y); started = true }
    else ctx.lineTo(x, y)
  })
  ctx.stroke()
}

function drawAvgLine(main, t, prevClose, yMin, yMax, x0 = 0, w0 = 0) {
  if (!ctx || !t.length) return
  const w = w0 || canvasRef.value.clientWidth
  let cumAmt = 0, cumVol = 0
  ctx.strokeStyle = C.avg
  ctx.lineWidth = 1.2
  ctx.beginPath()
  let started = false
  for (let i = 0; i < t.length; i++) {
    const x = x0 + trendX(t[i].time, w)
    if (x < x0) continue          // 午休点跳过: 不累计不连线
    cumAmt += t[i].amount || 0
    cumVol += t[i].vol || 0
    if (cumVol <= 0) continue
    const v = cumAmt / (cumVol * 100)
    if (!Number.isFinite(v)) continue
    const y = priceToY(v, yMin, yMax, main)
    if (!started) { ctx.moveTo(x, y); started = true }
    else ctx.lineTo(x, y)
  }
  ctx.stroke()
}

function drawTrendVolume(vol, t, prevClose, x0 = 0, w0 = 0) {
  if (!ctx || !t.length) return
  const w = w0 || canvasRef.value.clientWidth
  // 顶部 16px 头部条带(东财"成交量"行), 图形只画条带下方
  const HEADER_H = 16
  const plot = { x: vol.x, y: vol.y + HEADER_H, width: vol.width, height: vol.height - HEADER_H }
  const maxVol = Math.max(...t.map(p => p.vol), 1)
  const barW = Math.max(1, w / TREND_VMIN * 0.6)   // 按 270 虚拟分钟(含竞价段)定柱宽, 午休不占位
  const isAuction = p => { const m = trendMinute(p.time); return m >= 0 && m < 30 }
  // 竞价段: 委托量多空柱(东财) — 买委托(side0)红柱从轴向上, 卖委托(side1)绿柱向轴下;
  // 两方向各自按方向内最大 tick 量定标(若按全天分钟量定标, 竞价量占比小的票会被压成不可见)
  const aTicks = []
  let upMax = 0, downMax = 0, lastCum = 0
  for (const p of t) {
    if (!isAuction(p)) continue
    const up = p.side !== 1
    aTicks.push({ x: x0 + trendX(p.time, w), v: p.vol || 0, up })
    lastCum = p.cum || lastCum   // cumVol 有回撤修订 → 头部展示用最后一笔累计
    if (up) upMax = Math.max(upMax, p.vol || 0)
    else downMax = Math.max(downMax, p.vol || 0)
  }
  const midY = plot.y + plot.height / 2
  const halfH = plot.height / 2 - 2
  if (aTicks.length) {
    ctx.strokeStyle = '#e8ecf1'   // 竞价多空中轴
    ctx.beginPath()
    ctx.moveTo(x0, midY); ctx.lineTo(x0 + plot.width * 30 / TREND_VMIN, midY)
    ctx.stroke()
  }
  for (const tk of aTicks) {
    if (tk.v <= 0) continue
    const h = tk.up ? (upMax ? tk.v / upMax : 0) * halfH : (downMax ? tk.v / downMax : 0) * halfH
    if (h < 0.5) continue
    ctx.fillStyle = tk.up ? 'rgba(231,76,60,.7)' : 'rgba(39,174,96,.7)'
    if (tk.up) ctx.fillRect(tk.x - barW / 2, midY - h, barW, h)
    else ctx.fillRect(tk.x - barW / 2, midY, barW, h)
  }
  // 连续竞价: 分钟成交量柱(红涨绿跌)
  for (let i = 0; i < t.length; i++) {
    const p = t[i]
    if (isAuction(p)) continue
    const x = x0 + trendX(p.time, w)
    if (x < x0) continue          // 盘后等无效点跳过
    const h = (p.vol / maxVol) * (plot.height - 4)
    ctx.fillStyle = typeof prevClose === 'number' && p.price >= prevClose ? C.volUp : C.volDown
    ctx.fillRect(x - barW / 2, plot.y + plot.height - h - 2, barW, h)
  }
  // 头部(东财): [成交量▾] 总量 委托量(竞价累计) 现量(最新交易分钟); 条带下方左侧标量轴最大值(东财 55450)
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  let hx = volHeaderChip(vol)
  const total = props.quote?.volume
  const lastTrade = [...t].reverse().find(p => trendMinute(p.time) >= 30)
  const parts = []
  if (Number.isFinite(total)) parts.push([`${fmtHand(total)}`, C.axisText])
  if (aTicks.length) parts.push([`委托量:${fmtHand(lastCum)}`, '#27ae60'])
  if (lastTrade) parts.push([`现量:${fmtHand(lastTrade.vol || 0)}`, C.axisText])
  ctx.font = FONT_SM
  for (const [text, color] of parts) {
    ctx.fillStyle = color
    ctx.fillText(text, hx, vol.y + 3)
    hx += ctx.measureText(text + ' ').width
  }
  ctx.fillStyle = C.timeText
  ctx.fillText(fmtHand(maxVol), x0 + 4, plot.y + 2)
}

// 量能头部左侧"成交量"芯片(东财深色块 → 浅色主题灰底), 返回后续文本起始 x
function volHeaderChip(vol) {
  ctx.font = FONT_SM
  ctx.fillStyle = '#f0f2f5'
  ctx.fillRect(vol.x + 2, vol.y + 2, 42, 12)
  ctx.fillStyle = '#666'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  ctx.fillText('成交量', vol.x + 6, vol.y + 3)
  return vol.x + 48
}

function drawTimeAxis(rects, ticks, isIntraday, x0 = 0) {
  if (!ctx) return
  const bottom = rects.sub ? rects.sub.y + rects.sub.height : rects.vol.y + rects.vol.height
  ctx.font = FONT_SM
  ctx.fillStyle = C.timeText
  ctx.textBaseline = 'top'
  const px = t => t.x + x0
  for (const t of ticks) {
    // 首尾刻度按位置调整对齐, 避免 center 贴边裁切(分时 09:30 在 x=0, 15:00 在 x=w)
    const x = px(t)
    const w = canvasRef.value.clientWidth
    ctx.textAlign = x < 8 ? 'left' : x > w - 8 ? 'right' : 'center'
    ctx.fillText(t.label, x, bottom + 3)
  }
}

// ─────────────── K 线视图 (Task 3 补全) ───────────────
function drawKline() {
  const kl = props.kline
  if (!kl.length || !ctx) return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  const hasSub = props.subInd !== 'none'
  // 东财式: K线绘图区同样拉通全宽, 刻度叠在图内左缘(见 drawKlineAxis)
  const rects = panelRects(w, h, hasSub, { leftGutter: 0, rightGutter: 0 })
  const { main, vol, sub } = rects
  const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
  const { window: win, offset: off } = klineWindow(kl, count, offset)
  const baseI = kl.length - win.length - off   // win[0] ↔ 全局索引(指标数组与 kline 同索引)
  const { yMin, yMax } = range(win)
  plotX = main.x; plotW = main.width
  const pw = main.width   // 内容区宽
  const q = props.quote || {}
  // 涨跌停虚线(东财): 落在可视价格区间内才画
  drawGrid(main)
  if (Number.isFinite(q.upPx) && q.upPx >= yMin && q.upPx <= yMax) drawHLine(main, q.upPx, yMin, yMax, 'rgba(243,156,18,.55)', 'dashed')
  if (Number.isFinite(q.downPx) && q.downPx >= yMin && q.downPx <= yMax) drawHLine(main, q.downPx, yMin, yMax, 'rgba(243,156,18,.55)', 'dashed')
  // 蜡烛
  drawCandles(main, win, yMin, yMax, pw)
  // 叠加
  if (props.overlays.ma) drawMALines(main, win, yMin, yMax, pw, baseI)
  if (props.overlays.boll) drawBoll(main, win, yMin, yMax, pw, baseI)
  if (props.chan && props.view !== 'm60') drawChan(main, win, yMin, yMax, pw)
  if (props.wave) drawWave(main, win, yMin, yMax, pw)
  // 轴(左缘刻度 + 右缘最新价标注)
  drawKlineAxis(main, priceTicks(yMin, yMax, main), win, yMin, yMax)
  // MA/BOLL 值图例
  drawMALegend(main, win, baseI)
  // 量能
  drawVolumePane(vol, win, pw, baseI)
  // 副图
  if (sub && props.subInd !== 'none') drawSubPane(sub, win, props.indCache, main.x, pw, baseI)
  // 时间轴
  drawTimeAxis(rects, timeTicks(win, pw, false), false, main.x)
  // 存窗口供 crosshair/emit
  offset = off
}

function range(win) {
  let mn = Infinity, mx = -Infinity
  for (const k of win) {
    if (k.high > mx) mx = k.high
    if (k.low < mn) mn = k.low
  }
  if (!Number.isFinite(mn)) { mn = 0; mx = 1 }
  const pad = (mx - mn) * 0.04
  return { yMin: mn - pad, yMax: mx + pad }
}

function drawCandles(main, win, yMin, yMax, w) {
  if (!ctx) return
  const barW = w / win.length
  const bodyW = Math.max(1, barW * 0.6)
  for (let i = 0; i < win.length; i++) {
    const k = win[i]
    const up = k.close >= k.open
    const color = up ? C.up : C.down
    const x = idxToX(i, w, win.length)
    const oY = priceToY(k.open, yMin, yMax, main)
    const cY = priceToY(k.close, yMin, yMax, main)
    const hY = priceToY(k.high, yMin, yMax, main)
    const lY = priceToY(k.low, yMin, yMax, main)
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, hY); ctx.lineTo(x, lY)
    ctx.stroke()
    const top = Math.min(oY, cY)
    const bh = Math.max(Math.abs(cY - oY), 1)
    ctx.fillStyle = color
    ctx.fillRect(x - bodyW / 2, top, bodyW, bh)
  }
}

function drawMALines(main, win, yMin, yMax, w, baseI = 0) {
  const ic = props.indCache
  if (!ic?.ma) return
  for (const n of [5, 10, 20, 60]) {
    const vals = ic.ma[n]
    if (!vals) continue
    drawValueLine(main, vals, win, yMin, yMax, w, MA_COLORS[n], 1, 'solid', baseI)
  }
}

function drawBoll(main, win, yMin, yMax, w, baseI = 0) {
  const ic = props.indCache
  if (!ic?.boll) return
  for (const key of ['up', 'mid', 'lo']) {
    const vals = ic.boll[key]
    if (!vals) continue
    drawValueLine(main, vals, win, yMin, yMax, w, BOLL_COLORS[key], 1, key === 'mid' ? 'dashed' : 'solid', baseI)
  }
}

function drawValueLine(main, vals, win, yMin, yMax, w, color, lw, style = 'solid', baseI = 0) {
  if (!ctx) return
  ctx.strokeStyle = color
  ctx.lineWidth = lw
  ctx.setLineDash(style === 'dashed' ? [4, 3] : [])
  ctx.beginPath()
  let started = false
  for (let i = 0; i < win.length; i++) {
    const v = vals[baseI + i]
    if (v === null || v === undefined || !Number.isFinite(v)) continue
    const x = idxToX(i, w, win.length)
    const y = priceToY(v, yMin, yMax, main)
    if (!started) { ctx.moveTo(x, y); started = true }
    else ctx.lineTo(x, y)
  }
  ctx.stroke()
  ctx.setLineDash([])
}

// 缠论: 分型/笔/中枢/买卖点/背驰 (Task 3 补全绘制)
function drawChan(main, win, yMin, yMax, w) {
  const ic = props.indCache
  if (!ic) return
  const kl = props.kline
  const { window: win0, offset: off0 } = klineWindow(kl, Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length), offset)
  const baseI = kl.length - win0.length - off0   // win[0] 对应全局索引
  const xOf = gI => idxToX(gI - baseI, w, win0.length)
  const yOf = v => priceToY(v, yMin, yMax, main)
  // 笔
  if (ic.bis?.length) {
    ctx.strokeStyle = C.chan
    ctx.lineWidth = 1.5
    ctx.setLineDash([])
    ctx.beginPath()
    let first = true
    for (const b of ic.bis) {
      for (const p of [b.from, b.to]) {
        const gI = p.i
        if (gI < baseI || gI >= baseI + win0.length) continue
        const v = p.type === 1 ? kl[gI].high : kl[gI].low
        const x = xOf(gI), y = yOf(v)
        if (first) { ctx.moveTo(x, y); first = false }
        else ctx.lineTo(x, y)
      }
    }
    ctx.stroke()
  }
  // 中枢
  const zs = (ic.zhongshu || []).slice(-CHAN_MAX_ZHONGSHU)
  for (const z of zs) {
    const x1 = xOf(z.from), x2 = xOf(z.to)
    const y1 = yOf(z.zg), y2 = yOf(z.zd)
    if (x1 < 0 && x2 < 0) continue
    ctx.fillStyle = 'rgba(125,60,152,.15)'
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1)
    ctx.strokeStyle = C.chan
    ctx.lineWidth = 1
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1)
  }
  // 分型 + 买卖点 + 背驰
  ctx.font = FONT
  ctx.textBaseline = 'middle'
  for (const f of (ic.fractals || [])) {
    const gI = f.i
    if (gI < baseI || gI >= baseI + win0.length) continue
    const x = xOf(gI)
    const isTop = f.type === 1
    const y = isTop ? yOf(kl[gI].high) - 4 : yOf(kl[gI].low) + 4
    const sig = ic.signalAt?.get(gI)
    const dv = (ic.divergences || []).find(d => d.i === gI)
    ctx.fillStyle = C.chan
    // 三角
    ctx.beginPath()
    if (isTop) { ctx.moveTo(x - 4, y); ctx.lineTo(x + 4, y); ctx.lineTo(x, y - 5) }
    else { ctx.moveTo(x - 4, y); ctx.lineTo(x + 4, y); ctx.lineTo(x, y + 5) }
    ctx.closePath()
    ctx.fill()
    if (dv) {
      ctx.fillStyle = dv.type === 'top' ? '#e67e22' : '#5b2c8f'
      ctx.textAlign = 'left'
      ctx.fillText(dv.type === 'top' ? '顶背驰' : '底背驰', x + 5, isTop ? y - 4 : y + 4)
    } else if (sig) {
      ctx.fillStyle = /sell$/.test(sig) ? C.down : C.chan
      ctx.textAlign = 'left'
      ctx.fillText(SIGNAL_LABELS[sig], x + 5, isTop ? y - 4 : y + 4)
    }
  }
}

function drawWave(main, win, yMin, yMax, w) {
  const ic = props.indCache
  if (!ic?.waves) return
  const ws = ic.waves
  if (ws.status !== 'ok' && ws.status !== 'ok5') return
  const kl = props.kline
  const { window: win0, offset: off0 } = klineWindow(kl, Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length), offset)
  const baseI = kl.length - win0.length - off0
  const xOf = gI => idxToX(gI - baseI, w, win0.length)
  const yOf = v => priceToY(v, yMin, yMax, main)
  // 连接线
  ctx.strokeStyle = C.wave
  ctx.lineWidth = 1
  ctx.setLineDash([])
  ctx.beginPath()
  let first = true
  for (const p of ws.waves) {
    const gI = p.i
    if (gI < baseI || gI >= baseI + win0.length) continue
    const v = p.type === 1 ? kl[gI].high : kl[gI].low
    const x = xOf(gI), y = yOf(v)
    if (first) { ctx.moveTo(x, y); first = false }
    else ctx.lineTo(x, y)
  }
  ctx.stroke()
  // 标签
  ctx.font = FONT
  ctx.textBaseline = 'middle'
  for (const p of ws.waves) {
    const gI = p.i
    if (gI < baseI || gI >= baseI + win0.length) continue
    const x = xOf(gI)
    const isTop = p.type === 1
    const y = isTop ? yOf(kl[gI].high) - 12 : yOf(kl[gI].low) + 12
    ctx.fillStyle = C.wave
    ctx.textAlign = 'center'
    ctx.fillText(p.label, x, y)
  }
}

// K线轴(东财): 刻度叠在图内左缘(白色描边垫底), 右缘标注最新收盘价(按当日收开红绿)
function drawKlineAxis(main, ticks, win, yMin, yMax) {
  if (!ctx) return
  ctx.font = FONT_SM
  ctx.fillStyle = C.axisText
  ctx.textAlign = 'left'
  ctx.lineWidth = 3
  ctx.strokeStyle = 'rgba(255,255,255,.85)'
  for (const t of ticks) {
    // 首尾刻度换 baseline 防边缘裁切
    ctx.textBaseline = t.y <= main.y + 6 ? 'top' : t.y >= main.y + main.height - 6 ? 'bottom' : 'middle'
    ctx.strokeText(String(t.label), main.x + 3, t.y)
    ctx.fillText(String(t.label), main.x + 3, t.y)
  }
  const last = win[win.length - 1]
  if (last && Number.isFinite(last.close)) {
    const y = clamp(priceToY(last.close, yMin, yMax, main), main.y + 6, main.y + main.height - 6)
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'right'
    ctx.fillStyle = last.close >= last.open ? C.up : C.down
    const s = `${last.close.toFixed(2)} →`
    ctx.strokeText(s, main.x + main.width - 3, y)
    ctx.fillText(s, main.x + main.width - 3, y)
  }
  ctx.lineWidth = 1
}

// MA/BOLL 值图例(东财): 叠在主图左上两行, 值取可视窗口最后一根, ↑/↓ 表较前一根方向
function drawMALegend(main, win, baseI) {
  const ic = props.indCache
  if (!ic || (!props.overlays.ma && !props.overlays.boll)) return
  const li = baseI + win.length - 1
  const arrow = vals => (Number.isFinite(vals?.[li]) && Number.isFinite(vals?.[li - 1])) ? (vals[li] >= vals[li - 1] ? '↑' : '↓') : ''
  const f2 = v => Number.isFinite(v) ? v.toFixed(2) : '—'
  const rows = [[], []]
  if (props.overlays.ma && ic.ma) {
    rows[0].push(
      [`MA5:${f2(ic.ma[5]?.[li])}${arrow(ic.ma[5])}`, MA_COLORS[5]],
      [`10:${f2(ic.ma[10]?.[li])}${arrow(ic.ma[10])}`, MA_COLORS[10]],
      [`20:${f2(ic.ma[20]?.[li])}${arrow(ic.ma[20])}`, MA_COLORS[20]],
    )
    rows[1].push([`60:${f2(ic.ma[60]?.[li])}${arrow(ic.ma[60])}`, MA_COLORS[60]])
  }
  if (props.overlays.boll && ic.boll) {
    rows[1].push(
      [`UP:${f2(ic.boll.up?.[li])}`, BOLL_COLORS.up],
      [`MID:${f2(ic.boll.mid?.[li])}`, BOLL_COLORS.mid],
      [`LOW:${f2(ic.boll.lo?.[li])}`, BOLL_COLORS.lo],
    )
  }
  ctx.font = FONT_SM
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  ctx.lineWidth = 3
  ctx.strokeStyle = 'rgba(255,255,255,.85)'
  rows.forEach((parts, r) => {
    let x = main.x + 4
    for (const [text, color] of parts) {
      ctx.fillStyle = color
      ctx.strokeText(text, x, main.y + 3 + r * 12)
      ctx.fillText(text, x, main.y + 3 + r * 12)
      x += ctx.measureText(text + ' ').width
    }
  })
  ctx.lineWidth = 1
}

function drawVolumePane(vol, win, w, baseI = 0) {
  if (!ctx) return
  // 与分时量能区同款: 顶部 16px 头部条带, 量柱/均线只画条带下方
  const HEADER_H = 16
  const plot = { x: vol.x, y: vol.y + HEADER_H, width: vol.width, height: vol.height - HEADER_H }
  const maxVol = Math.max(...win.map(k => k.volume), 1)
  const barW = Math.max(1, w / win.length * 0.6)
  for (let i = 0; i < win.length; i++) {
    const k = win[i]
    const x = idxToX(i, w, win.length) - barW / 2
    const h = (k.volume / maxVol) * (plot.height - 4)
    ctx.fillStyle = k.close >= k.open ? C.volUp : C.volDown
    ctx.fillRect(x, plot.y + plot.height - h - 2, barW, h)
  }
  // 头部(东财): [成交量▾] 总量(较前一根↑/↓) + MA1/MA2(量均线 5/10 值, 带方向箭头); 条带下方左侧标量轴最大值
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  ctx.font = FONT_SM
  const lastK = win[win.length - 1], prevK = win[win.length - 2]
  const vDir = prevK ? (lastK.volume >= prevK.volume ? '↑' : '↓') : ''
  const li = baseI + win.length - 1
  const vArr = vals => (Number.isFinite(vals?.[li]) && Number.isFinite(vals?.[li - 1])) ? (vals[li] >= vals[li - 1] ? '↑' : '↓') : ''
  const parts = [[`${fmtHand(win.reduce((s, k) => s + (k.volume || 0), 0))}${vDir}`, C.axisText]]
  if (props.indCache?.volma?.[5]) parts.push([`MA1:${fmtHand(props.indCache.volma[5][li])}${vArr(props.indCache.volma[5])}`, C.avg])
  if (props.indCache?.volma?.[10]) parts.push([`MA2:${fmtHand(props.indCache.volma[10][li])}${vArr(props.indCache.volma[10])}`, C.signal])
  let hx = volHeaderChip(vol)
  for (const [text, color] of parts) {
    ctx.fillStyle = color
    ctx.fillText(text, hx, vol.y + 3)
    hx += ctx.measureText(text + ' ').width
  }
  ctx.fillStyle = C.timeText
  ctx.fillText(fmtHand(maxVol), plot.x + 4, plot.y + 2)
  // volma 5/10
  const ic = props.indCache
  if (ic?.volma) {
    for (const n of [5, 10]) {
      const vals = ic.volma[n]
      if (!vals) continue
      drawVolLine(plot, vals, win, w, n === 5 ? C.avg : C.signal, baseI)
    }
  }
}

function drawVolLine(vol, vals, win, w, color, baseI = 0) {
  if (!ctx) return
  const maxVol = Math.max(...win.map(k => k.volume), 1)
  ctx.strokeStyle = color
  ctx.lineWidth = 1
  ctx.setLineDash([])
  ctx.beginPath()
  let started = false
  for (let i = 0; i < win.length; i++) {
    const v = vals[baseI + i]
    if (v === null || v === undefined || !Number.isFinite(v)) continue
    const x = idxToX(i, w, win.length)
    const y = vol.y + vol.height - 2 - (v / maxVol) * (vol.height - 4)
    if (!started) { ctx.moveTo(x, y); started = true }
    else ctx.lineTo(x, y)
  }
  ctx.stroke()
}

// 副图指标: ic 缺省取宿主 indCache; 分时视图由 drawTrend 现算传入 (isIntraday=true → x 按交易分钟)
// 顶部 16px 头部条带(东财"MACD ▾"行): 下拉/数值行在条带内, 指标图形只画条带下方
function drawSubPane(sub, win, ic = props.indCache, x0 = 0, w0 = 0, baseI = 0, isIntraday = false) {
  const s = props.subInd
  if (s === 'none' || !ic) return
  const w = w0 || canvasRef.value.clientWidth
  const HEADER_H = 16
  const plot = { x: sub.x, y: sub.y + HEADER_H, width: sub.width, height: sub.height - HEADER_H }
  ctx.font = FONT_SM
  ctx.fillStyle = C.axisText
  ctx.textBaseline = 'top'
  ctx.textAlign = 'left'
  if (s === 'macd' && ic.macd) {
    drawSubMacd(plot, win, w, ic.macd, x0, baseI, isIntraday)
    drawSubMacdHeader(sub, ic.macd, baseI + win.length - 1)
  } else if (s === 'kdj' && ic.kdj) {
    drawSubOsc(plot, win, w, { k: ic.kdj.k, d: ic.kdj.d, j: ic.kdj.j }, x0, baseI, isIntraday)
    drawSubOscHeader(sub, [['K', ic.kdj.k], ['D', ic.kdj.d], ['J', ic.kdj.j]], baseI + win.length - 1)
  } else if (s === 'rsi' && ic.rsi) {
    drawSubOsc(plot, win, w, { 6: ic.rsi[6], 12: ic.rsi[12], 24: ic.rsi[24] }, x0, baseI, isIntraday)
    drawSubOscHeader(sub, [['RSI6', ic.rsi[6]], ['12', ic.rsi[12]], ['24', ic.rsi[24]]], baseI + win.length - 1)
  } else if (s === 'wr' && ic.wr) {
    drawSubOsc(plot, win, w, { 10: ic.wr[10], 6: ic.wr[6] }, x0, baseI, isIntraday)
    drawSubOscHeader(sub, [['WR10', ic.wr[10]], ['6', ic.wr[6]]], baseI + win.length - 1)
  }
}

// 副图头部公共渲染: parts=[文本, 颜色], x 从 68 起避开左侧指标下拉(同一行, 东财布局)
function drawSubHeaderParts(sub, parts) {
  if (!ctx) return
  ctx.font = FONT_SM
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  let x = sub.x + 68
  for (const [text, color] of parts) {
    ctx.fillStyle = color
    ctx.fillText(text, x, sub.y + 4)
    x += ctx.measureText(text + ' ').width
  }
}

// 头部数值方向箭头: 较数组前一根 ↑/↓
function subHeaderArrow(vals, i) {
  return (Number.isFinite(vals?.[i]) && Number.isFinite(vals?.[i - 1])) ? (vals[i] >= vals[i - 1] ? '↑' : '↓') : ''
}

// MACD 副图头部(参考东财): 数值取可视窗口最后一根, ↑/↓ 表较前一根方向; 静态参考不随十字线联动。
// 与指标线同色系: DIF 黄 / DEA 紫 / M 红绿
function drawSubMacdHeader(sub, m, lastIdx) {
  if (!ctx) return
  const f3 = v => !Number.isFinite(v) ? '—' : Math.abs(v) < 1 ? v.toFixed(3) : v.toFixed(2)
  const dif = m.dif?.[lastIdx], dea = m.dea?.[lastIdx], hist = m.hist?.[lastIdx]
  drawSubHeaderParts(sub, [
    [`DIF:${f3(dif)}${subHeaderArrow(m.dif, lastIdx)}`, C.avg],
    [`DEA:${f3(dea)}${subHeaderArrow(m.dea, lastIdx)}`, C.signal],
    [`M:${f3(hist)}${subHeaderArrow(m.hist, lastIdx)}`, hist >= 0 ? C.up : C.down],
  ])
}

// KDJ/RSI/WR 副图头部(参考东财): 数值取可视窗口最后一根带方向箭头, 与指标线同色(黄/紫/绿)
function drawSubOscHeader(sub, lines, lastIdx) {
  if (!ctx) return
  const f2 = v => Number.isFinite(v) ? v.toFixed(2) : '—'
  const colors = [C.avg, C.signal, '#27ae60']
  drawSubHeaderParts(sub, lines.map(([name, vals], i) =>
    [`${name}:${f2(vals?.[lastIdx])}${subHeaderArrow(vals, lastIdx)}`, colors[i % colors.length]],
  ))
}

function drawSubMacd(sub, win, w, m, x0 = 0, baseI = 0, isIntraday = false) {
  // DIF/DEA 峰值可能大于 |hist|(分时伪K数值大) → 缩放取三者最大, 否则线溢出副图进入量图
  const segMax = arr => arr ? Math.max(...arr.slice(baseI, baseI + win.length).map(Math.abs).filter(Number.isFinite), 0) : 0
  const scaleMax = Math.max(segMax(m.hist), segMax(m.dif), segMax(m.dea), 1)
  const barW = Math.max(1, (isIntraday ? w / TREND_VMIN : w / win.length) * 0.6)
  const midY = sub.y + sub.height / 2
  const halfH = sub.height / 2 - 2
  const subX = (i, bar) => x0 + (isIntraday ? trendX(bar.time, w) : idxToX(i, w, win.length))
  for (let i = 0; i < win.length; i++) {
    const v = m.hist[baseI + i]
    if (!Number.isFinite(v)) continue
    const x = subX(i, win[i])
    if (x < x0) continue          // 午休点跳过
    const h = (Math.abs(v) / scaleMax) * halfH
    ctx.fillStyle = v >= 0 ? C.histUp : C.histDown
    ctx.fillRect(x - barW / 2, v >= 0 ? midY - h : midY, barW, h)
  }
  // DIF/DEA 线
  const lc = (vals, color) => {
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.setLineDash([])
    ctx.beginPath()
    let started = false
    for (let i = 0; i < win.length; i++) {
      const v = vals[baseI + i]
      if (!Number.isFinite(v)) continue
      const x = subX(i, win[i])
      if (x < x0) continue
      const y = midY - (v / scaleMax) * halfH
      if (!started) { ctx.moveTo(x, y); started = true }
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }
  lc(m.dif, C.avg)
  lc(m.dea, C.signal)
}

function drawSubOsc(sub, win, w, lines, x0 = 0, baseI = 0, isIntraday = false) {
  const entries = Object.entries(lines)
  const colors = [C.avg, C.signal, '#27ae60']
  entries.forEach(([_, vals], idx) => {
    if (!vals) return
    ctx.strokeStyle = colors[idx % colors.length]
    ctx.lineWidth = 1
    ctx.setLineDash([])
    ctx.beginPath()
    let started = false
    for (let i = 0; i < win.length; i++) {
      const v = vals[baseI + i]
      if (v === null || v === undefined || !Number.isFinite(v)) continue
      const x = x0 + (isIntraday ? trendX(win[i].time, w) : idxToX(i, w, win.length))
      if (x < x0) continue
      const y = sub.y + sub.height - 2 - (v / 100) * (sub.height - 4)
      if (!started) { ctx.moveTo(x, y); started = true }
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  })
  // 参考虚线
  const refs = props.subInd === 'rsi' ? [30, 50, 70] : props.subInd === 'wr' ? [20, 80] : []
  ctx.setLineDash([2, 2])
  ctx.strokeStyle = '#eee'
  for (const r of refs) {
    const y = sub.y + sub.height - 2 - (r / 100) * (sub.height - 4)
    ctx.beginPath()
    ctx.moveTo(sub.x, y); ctx.lineTo(sub.x + sub.width, y)
    ctx.stroke()
  }
  ctx.setLineDash([])
}

// ─────────────── 十字光标 + emit ───────────────
function drawCrosshair() {
  if (!ctx || !hover) return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  ctx.strokeStyle = C.crosshair
  ctx.lineWidth = 1
  ctx.setLineDash([4, 3])
  ctx.beginPath()
  ctx.moveTo(hover.x, 0); ctx.lineTo(hover.x, h)
  ctx.moveTo(0, hover.y); ctx.lineTo(w, hover.y)
  ctx.stroke()
  ctx.setLineDash([])
}

function clamp(v, a, b) { return Math.max(a, Math.min(b, v)) }

function onPointerDown(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left, y = e.clientY - rect.top
  pointers.set(e.pointerId, { x, y })
  // 第二指落下 → 双指平移(K线视图); 期间不移动十字线
  if (e.pointerType !== 'mouse' && pointers.size === 2) {
    const xs = [...pointers.values()].map(p => p.x)
    twoPan = { startAvgX: (xs[0] + xs[1]) / 2, startOffset: offset }
    touch = null
    drag = null
    return
  }
  // 手机无 hover: 点按即选中(分时/日K)并联动宫格
  lastPointerType = e.pointerType
  hover = { x, y }
  draw()
  emitCrosshair(hover.x)
  if (e.pointerType === 'mouse') {
    // 桌面鼠标拖动 = 平移
    drag = { startX: e.clientX, startOffset: offset }
    touch = null
  } else if (pointers.size === 1) {
    // 触屏单指: 快甩 = 滑动平移窗口; 慢移/按住 = 拖动十字线
    const t = performance.now()
    touch = { startX: x, startT: t, lastX: x, lastT: t, peakV: 0, mode: null }
    drag = null
  }
}

function onPointerMove(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left, y = e.clientY - rect.top
  lastPointerType = e.pointerType
  // 双指平移: 两指中点横向位移 → 窗口平移(向右=回看更早)
  if (twoPan && pointers.has(e.pointerId)) {
    pointers.set(e.pointerId, { x, y })
    hover = null
    const xs = [...pointers.values()].map(p => p.x)
    const avg = (xs[0] + xs[1]) / 2
    const kl = props.kline
    const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
    const { window: win } = klineWindow(kl, count, 0)
    const bw = (plotW || canvasRef.value.clientWidth) / win.length
    offset = clamp(twoPan.startOffset + Math.round((avg - twoPan.startAvgX) / bw), 0, Math.max(0, kl.length - win.length))
    draw()
    return
  }
  if (drag) {   // 桌面鼠标拖动 = 平移 + 十字线跟手
    hover = { x, y }
    const kl = props.kline
    const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
    const { window: win } = klineWindow(kl, count, 0)
    const barW = (plotW || canvasRef.value.clientWidth) / win.length
    const dOff = Math.round((e.clientX - drag.startX) / barW)   // 向右滑=回看(更早), 向左滑=到最新
    offset = clamp(drag.startOffset + dOff, 0, Math.max(0, kl.length - win.length))
    draw()
    emitCrosshair(x)
    return
  }
  // 触屏单指: 滑动=平移窗口(改可见日期), 拖动=十字线(看该 K线数据)
  if (touch && pointers.has(e.pointerId) && pointers.size === 1) {
    if (isTrend()) {   // 分时整日都在视野内: 滑动/拖动都移动十字线看分钟数据, 不平移
      hover = { x, y }
      draw()
      emitCrosshair(x)
      return
    }
    const now = performance.now()
    const dt = now - touch.lastT
    const dx = x - touch.lastX
    if (dt >= 16) {   // 事件过密(<16ms)不算速度, 避免噪声误判
      touch.peakV = Math.max(touch.peakV, Math.abs(dx) / dt)
      if (touch.mode === null) {
        if (touch.peakV > SWIPE_PXMS) touch.mode = 'swipe'          // 快速甩动 → 滑动平移
        else if (now - touch.startT > HOLD_MS) touch.mode = 'drag'  // 按住/慢移 → 拖动十字线
      }
    }
    touch.lastX = x; touch.lastT = now
    if (touch.mode === 'swipe') {
      hover = null
      const kl = props.kline
      const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
      const { window: win } = klineWindow(kl, count, 0)
      const bw = (plotW || canvasRef.value.clientWidth) / win.length
      offset = clamp(offset + Math.round(dx / bw), 0, Math.max(0, kl.length - win.length))   // 右滑=回看(更早), 左滑=到最新
      draw()
      return
    }
    // drag(或尚未分类, 安全默认): 十字线跟手, 宫格实时显示手指所在 K线
    hover = { x, y }
    draw()
    emitCrosshair(x)
    return
  }
  hover = { x, y }
  draw()
  emitCrosshair(x)
}

function onPointerUp(e) {
  pointers.delete(e.pointerId)
  if (pointers.size < 2) twoPan = null
  const wasDragging = !!drag
  drag = null
  if (wasDragging && e) {   // 桌面平移结束: 停在哪个位置就选中那根 K线
    const rect = canvasRef.value.getBoundingClientRect()
    hover = { x: e.clientX - rect.left, y: e.clientY - rect.top }
    draw()
    emitCrosshair(hover.x)
  }
  if (touch && pointers.size === 0) {
    if (touch.mode === 'swipe') {   // 滑动结束: 清空选中, 宫格回实时
      hover = null
      emit('crossinfo', null)
      draw()
    }
    touch = null
  }
}

function onPointerLeave(e) {
  if (e?.pointerType === 'touch' || e?.pointerType === 'pen') return   // 手机点按后保留光标
  hover = null; draw(); emit('crossinfo', null)
}

function onPointerCancel(e) {
  pointers.delete(e?.pointerId)
  if (pointers.size < 2) twoPan = null
  touch = null
  drag = null
  hover = null
  draw()
  emit('crossinfo', null)
}

function onContextMenu(e) { e.preventDefault() }   // 长按不弹复制/粘贴菜单

function onWheel(e) {
  if (isTrend()) return
  e.preventDefault()
  const kl = props.kline
  const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
  const maxOff = Math.max(0, kl.length - count)
  offset = clamp(offset + (e.deltaY < 0 ? 3 : -3), 0, maxOff)   // 滚轮上=回看, 下=到最新(与拖动手势同向)
  draw()
}

function emitCrosshair(x) {
  const send = info => {
    emit('crossinfo', info)
    clearTimeout(hideTimer)
    if (info && lastPointerType !== 'mouse') {
      // 触屏选中后无操作 → 自动隐藏(十字线消失 + 宫格回实时); 鼠标 hover 不隐藏
      hideTimer = setTimeout(() => {
        hover = null
        draw()
        emit('crossinfo', null)
      }, HIDE_MS)
    }
  }
  if (isTrend()) {   // 分时: 显示所选分钟数据(日内累计 高/低/成交额)
    const t = props.trend
    if (!t.length) { send(null); return }
    const w = plotW || canvasRef.value.clientWidth
    const x0 = plotX || 0
    // 光标 x → 虚拟分钟(竞价段 [0,30] + 连续竞价 240) → 最近的分钟点(午休/间隙区无数据: 取前后最近点)
    const m = clamp(Math.round((x - x0) / w * TREND_VMIN), 0, TREND_VMIN)
    let i = -1, best = Infinity
    for (let k = 0; k < t.length; k++) {
      const d = Math.abs(trendMinute(t[k].time) - m)
      if (d < best) { best = d; i = k }
    }
    if (i < 0) { send(null); return }
    const p = t[i]
    let hi = -Infinity, lo = Infinity, cVol = 0, cAmt = 0
    for (let k = 0; k <= i; k++) {
      const q = t[k]
      if (q.price > hi) hi = q.price
      if (q.price < lo) lo = q.price
      cVol += q.vol || 0
      cAmt += q.amount || 0
    }
    const prevClose = props.quote?.prevClose ?? (t[0] ? t[0].price : p.price)
    const chg = p.price - prevClose
    send({
      time: p.time, open: t[0] ? t[0].price : p.price, high: hi, low: lo, close: p.price,
      prevClose, amount: cAmt, volume: cVol,
      chg, chgPct: prevClose ? chg / prevClose * 100 : 0,
      point: { x, y: hover?.y ?? 0, w: canvasRef.value.clientWidth, h: canvasRef.value.clientHeight },
    })
    return
  }
  // 日K/周K/月K/60分
  const kl = props.kline
  if (!kl.length) { send(null); return }
  const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
  const { window: win } = klineWindow(kl, count, offset)
  const w = plotW || canvasRef.value.clientWidth
  const i = clamp(Math.floor((x - plotX) / w * win.length), 0, win.length - 1)
  const k = win[i]
  const gI = kl.indexOf(k)
  const prevClose = gI > 0 ? kl[gI - 1].close : k.open
  const chg = k.close - prevClose
  send({
    time: k.time, open: k.open, high: k.high, low: k.low, close: k.close,
    prevClose, amount: k.volume * 100 * k.close, volume: k.volume,
    chg, chgPct: prevClose ? chg / prevClose * 100 : 0,
    point: { x, y: hover?.y ?? 0, w: canvasRef.value.clientWidth, h: canvasRef.value.clientHeight },
  })
}

watch(() => [props.view, props.trend, props.kline, props.quote, props.subInd, props.chan, props.wave, props.overlays.ma, props.overlays.boll, props.indCache], () => {
  draw()
}, { deep: true })

// 切视图/换股(清空 kline)时重置平移偏移: 每个视图默认显示最近 N 根
watch(() => props.view, () => { offset = 0; hover = null; emit('crossinfo', null) })
watch(() => props.kline, () => { if (!props.kline.length) offset = 0 })

onMounted(() => {
  setupCanvas()
  ro = new ResizeObserver(() => resize())
  if (wrapRef.value) ro.observe(wrapRef.value)
  resize()
})

onBeforeUnmount(() => { ro?.disconnect() })
</script>

<template>
  <div ref="wrapRef" class="scc-wrap">
    <canvas ref="canvasRef" class="scc-canvas"
      @pointerdown="onPointerDown" @pointermove="onPointerMove"
      @pointerup="onPointerUp" @pointerleave="onPointerLeave" @pointercancel="onPointerCancel"
      @contextmenu="onContextMenu"
      @wheel.prevent="onWheel"></canvas>
  </div>
</template>

<style scoped>
.scc-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 280px;
  touch-action: pan-y;
  -webkit-touch-callout: none;   /* 手机长按不弹复制/粘贴/查询菜单 */
  -webkit-user-select: none;
  -moz-user-select: none;
  user-select: none;
}
.scc-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  cursor: crosshair;
  -webkit-user-select: none;
  user-select: none;
  -webkit-touch-callout: none;
}
@media (min-width: 481px) {
  .scc-wrap { min-height: 360px; }
}
</style>
