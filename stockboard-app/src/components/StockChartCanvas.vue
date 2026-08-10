<script setup>
// Canvas 2D 自绘图表: 分时(涨停板双轴/X轴固定全天) + K线(蜡烛/MA/BOLL/缠论/波浪) + 副图指标
// 数据全部由宿主 computeIndicators 计算后经 props 传入; 本组件只画 + 发射 crossinfo
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import {
  panelRects, priceToY, klineWindow, idxToX, timeTicks, priceTicksTrend, priceTicks,
} from '../utils/chartDraw.js'

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
let drag = null             // K线平移 {startX, startOffset}
let offset = 0              // K线窗口偏移(回看)

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
const VIEW_MAX_BARS = { m60: 120, day: 90, week: 60, month: 40 }
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
  const rects = panelRects(w, h, props.subInd !== 'none', { leftGutter: 32, rightGutter: 40 })
  const { main, vol, sub } = rects
  const upP = quote.upPx, downP = quote.downPx
  const axis = priceTicksTrend(upP, downP, prevClose, main)
  const yMin = downP, yMax = upP

  // 1. 网格 + 昨收 + 涨跌停线
  drawGrid(main)
  drawHLine(main, prevClose, yMin, yMax, C.preClose, 'dashed')
  if (typeof upP === 'number' && typeof downP === 'number' && upP > downP) {
    drawHLine(main, upP, yMin, yMax, 'rgba(231,76,60,.55)', 'dashed')
    drawHLine(main, downP, yMin, yMax, 'rgba(39,174,96,.55)', 'dashed')
  }
  // 2. 左右双轴刻度
  drawTrendAxis(axis, main, rects)
  // 3. 分时线 + 均价线
  drawTrendLine(main, t, prevClose, yMin, yMax)
  drawAvgLine(main, t, prevClose, yMin, yMax)
  // 4. 量能
  drawTrendVolume(vol, t, prevClose)
  // 5. 副图(分时按 subInd 走伪K线)
  if (sub && props.subInd !== 'none') drawSubPane(sub, t.map(p => ({ ...p, open: p.price, close: p.price, high: p.price, low: p.price, volume: p.vol })))
  // 6. 时间轴
  drawTimeAxis(rects, timeTicks(t, w, true), true)
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

function drawTrendAxis(axis, main, rects) {
  if (!ctx) return
  ctx.font = FONT_SM
  ctx.fillStyle = C.axisText
  ctx.textBaseline = 'middle'
  // 左轴百分比 (main.x 左侧)
  for (const t of axis.left) {
    ctx.textAlign = 'right'
    ctx.fillText(t.label, main.x - 5, t.y)
  }
  // 右轴价格 (main 右侧)
  for (const t of axis.right) {
    ctx.textAlign = 'left'
    ctx.fillText(String(t.label), main.x + main.width + 5, t.y)
  }
}

function drawTrendLine(main, t, prevClose, yMin, yMax) {
  if (!ctx || !t.length) return
  const w = canvasRef.value.clientWidth
  ctx.strokeStyle = C.up
  ctx.lineWidth = 1.2
  ctx.setLineDash([])
  ctx.beginPath()
  t.forEach((p, i) => {
    const x = idxToX(i, w, t.length)
    const y = priceToY(p.price, yMin, yMax, main)
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.stroke()
  // 区顶标签
  ctx.font = FONT
  ctx.fillStyle = C.axisText
  ctx.textAlign = 'left'
  ctx.textBaseline = 'top'
  ctx.fillText('分时', main.x + 4, main.y + 3)
  const avgX = main.x + 4 + ctx.measureText('分时 ').width
  ctx.fillStyle = C.avg
  ctx.fillText('─ 均价', avgX, main.y + 3)
}

function drawAvgLine(main, t, prevClose, yMin, yMax) {
  if (!ctx || !t.length) return
  const w = canvasRef.value.clientWidth
  let cumAmt = 0, cumVol = 0
  const pts = []
  for (const p of t) {
    cumAmt += p.amount
    cumVol += p.vol
    if (cumVol > 0) pts.push(+(cumAmt / (cumVol * 100)).toFixed(3))
  }
  if (!pts.length) return
  ctx.strokeStyle = C.avg
  ctx.lineWidth = 1
  ctx.beginPath()
  pts.forEach((v, i) => {
    const x = idxToX(i, w, t.length)
    const y = priceToY(v, yMin, yMax, main)
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.stroke()
}

function drawTrendVolume(vol, t, prevClose) {
  if (!ctx || !t.length) return
  const w = canvasRef.value.clientWidth
  const maxVol = Math.max(...t.map(p => p.vol), 1)
  const barW = Math.max(1, w / t.length * 0.6)
  for (let i = 0; i < t.length; i++) {
    const p = t[i]
    const x = idxToX(i, w, t.length) - barW / 2
    const h = (p.vol / maxVol) * (vol.height - 4)
    ctx.fillStyle = typeof prevClose === 'number' && p.price >= prevClose ? C.volUp : C.volDown
    ctx.fillRect(x, vol.y + vol.height - h - 2, barW, h)
  }
}

function drawTimeAxis(rects, ticks, isIntraday) {
  if (!ctx) return
  const bottom = rects.sub ? rects.sub.y + rects.sub.height : rects.vol.y + rects.vol.height
  ctx.font = FONT_SM
  ctx.fillStyle = C.timeText
  ctx.textBaseline = 'top'
  ctx.textAlign = 'center'
  for (const t of ticks) ctx.fillText(t.label, t.x, bottom + 3)
}

// ─────────────── K 线视图 (Task 3 补全) ───────────────
function drawKline() {
  const kl = props.kline
  if (!kl.length || !ctx) return
  const w = canvasRef.value.clientWidth, h = canvasRef.value.clientHeight
  const hasSub = props.subInd !== 'none'
  const rects = panelRects(w, h, hasSub, { leftGutter: 0, rightGutter: 40 })
  const { main, vol, sub } = rects
  const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
  const { window: win, offset: off } = klineWindow(kl, count, offset)
  const { yMin, yMax } = range(win)
  // 蜡烛
  drawCandles(main, win, yMin, yMax, w)
  // 叠加
  if (props.overlays.ma) drawMALines(main, win, yMin, yMax, w)
  if (props.overlays.boll) drawBoll(main, win, yMin, yMax, w)
  if (props.chan && props.view !== 'm60') drawChan(main, win, yMin, yMax, w)
  if (props.wave && props.view !== 'm60') drawWave(main, win, yMin, yMax, w)
  // 右轴
  drawKlineAxis(main, priceTicks(yMin, yMax, main))
  // 量能
  drawVolumePane(vol, win, w)
  // 副图
  if (sub && props.subInd !== 'none') drawSubPane(sub, win)
  // 时间轴
  drawTimeAxis(rects, timeTicks(win, w, false), false)
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

function drawMALines(main, win, yMin, yMax, w) {
  const ic = props.indCache
  if (!ic?.ma) return
  for (const n of [5, 10, 20, 60]) {
    const vals = ic.ma[n]
    if (!vals) continue
    drawValueLine(main, vals, win, yMin, yMax, w, MA_COLORS[n], 1)
  }
}

function drawBoll(main, win, yMin, yMax, w) {
  const ic = props.indCache
  if (!ic?.boll) return
  for (const key of ['up', 'mid', 'lo']) {
    const vals = ic.boll[key]
    if (!vals) continue
    drawValueLine(main, vals, win, yMin, yMax, w, BOLL_COLORS[key], 1, key === 'mid' ? 'dashed' : 'solid')
  }
}

function drawValueLine(main, vals, win, yMin, yMax, w, color, lw, style = 'solid') {
  if (!ctx) return
  ctx.strokeStyle = color
  ctx.lineWidth = lw
  ctx.setLineDash(style === 'dashed' ? [4, 3] : [])
  ctx.beginPath()
  let started = false
  for (let i = 0; i < win.length; i++) {
    const v = vals[i]
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

function drawKlineAxis(main, ticks) {
  if (!ctx) return
  ctx.font = FONT_SM
  ctx.fillStyle = C.axisText
  ctx.textBaseline = 'middle'
  ctx.textAlign = 'left'
  for (const t of ticks) ctx.fillText(String(t.label), main.x + main.width + 5, t.y)
}

function drawVolumePane(vol, win, w) {
  if (!ctx) return
  const maxVol = Math.max(...win.map(k => k.volume), 1)
  const barW = Math.max(1, w / win.length * 0.6)
  for (let i = 0; i < win.length; i++) {
    const k = win[i]
    const x = idxToX(i, w, win.length) - barW / 2
    const h = (k.volume / maxVol) * (vol.height - 4)
    ctx.fillStyle = k.close >= k.open ? C.volUp : C.volDown
    ctx.fillRect(x, vol.y + vol.height - h - 2, barW, h)
  }
  // volma 5/10
  const ic = props.indCache
  if (ic?.volma) {
    for (const n of [5, 10]) {
      const vals = ic.volma[n]
      if (!vals) continue
      drawVolLine(vol, vals, win, w, n === 5 ? C.avg : C.signal)
    }
  }
}

function drawVolLine(vol, vals, win, w, color) {
  if (!ctx) return
  const maxVol = Math.max(...win.map(k => k.volume), 1)
  ctx.strokeStyle = color
  ctx.lineWidth = 1
  ctx.setLineDash([])
  ctx.beginPath()
  let started = false
  for (let i = 0; i < win.length; i++) {
    const v = vals[i]
    if (v === null || v === undefined || !Number.isFinite(v)) continue
    const x = idxToX(i, w, win.length)
    const y = vol.y + vol.height - 2 - (v / maxVol) * (vol.height - 4)
    if (!started) { ctx.moveTo(x, y); started = true }
    else ctx.lineTo(x, y)
  }
  ctx.stroke()
}

// 副图指标 (Task 4 补全)
function drawSubPane(sub, win) {
  const s = props.subInd
  if (s === 'none' || !props.indCache) return
  const ic = props.indCache
  const w = canvasRef.value.clientWidth
  ctx.font = FONT_SM
  ctx.fillStyle = C.axisText
  ctx.textBaseline = 'top'
  ctx.textAlign = 'left'
  if (s === 'macd' && ic.macd) {
    drawSubMacd(sub, win, w)
    ctx.fillText('MACD', sub.x + 4, sub.y + 3)
  } else if (s === 'kdj' && ic.kdj) {
    drawSubOsc(sub, win, w, { k: ic.kdj.k, d: ic.kdj.d, j: ic.kdj.j })
    ctx.fillText('KDJ', sub.x + 4, sub.y + 3)
  } else if (s === 'rsi' && ic.rsi) {
    drawSubOsc(sub, win, w, { 6: ic.rsi[6], 12: ic.rsi[12], 24: ic.rsi[24] })
    ctx.fillText('RSI', sub.x + 4, sub.y + 3)
  } else if (s === 'wr' && ic.wr) {
    drawSubOsc(sub, win, w, { 10: ic.wr[10], 6: ic.wr[6] })
    ctx.fillText('WR', sub.x + 4, sub.y + 3)
  }
}

function drawSubMacd(sub, win, w) {
  const m = props.indCache.macd
  const histMax = Math.max(...m.hist.map(Math.abs).filter(Number.isFinite), 1)
  const barW = Math.max(1, w / win.length * 0.6)
  const midY = sub.y + sub.height / 2
  for (let i = 0; i < win.length; i++) {
    const v = m.hist[i]
    if (!Number.isFinite(v)) continue
    const x = idxToX(i, w, win.length) - barW / 2
    const h = (Math.abs(v) / histMax) * (sub.height / 2 - 2)
    ctx.fillStyle = v >= 0 ? C.histUp : C.histDown
    ctx.fillRect(x, v >= 0 ? midY - h : midY, barW, h)
  }
  // DIF/DEA 线
  const lc = (vals, color) => {
    ctx.strokeStyle = color
    ctx.lineWidth = 1
    ctx.setLineDash([])
    ctx.beginPath()
    let started = false
    for (let i = 0; i < win.length; i++) {
      const v = vals[i]
      if (!Number.isFinite(v)) continue
      const x = idxToX(i, w, win.length)
      const y = midY - (v / histMax) * (sub.height / 2 - 2)
      if (!started) { ctx.moveTo(x, y); started = true }
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }
  lc(m.dif, C.avg)
  lc(m.dea, C.signal)
}

function drawSubOsc(sub, win, w, lines) {
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
      const v = vals[i]
      if (v === null || v === undefined || !Number.isFinite(v)) continue
      const x = idxToX(i, w, win.length)
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
  if (isTrend()) return
  drag = { startX: e.clientX, startOffset: offset }
}

function onPointerMove(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  const x = e.clientX - rect.left, y = e.clientY - rect.top
  if (drag) {
    const kl = props.kline
    const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
    const { window: win } = klineWindow(kl, count, 0)
    const barW = canvasRef.value.clientWidth / win.length
    const dOff = Math.round((drag.startX - e.clientX) / barW)
    offset = clamp(drag.startOffset + dOff, 0, Math.max(0, kl.length - win.length))
    draw()
    return
  }
  hover = { x, y }
  draw()
  emitCrosshair(x)
}

function onPointerUp() { drag = null }
function onPointerLeave() { hover = null; draw(); emit('crossinfo', null) }

function onWheel(e) {
  if (isTrend()) return
  e.preventDefault()
  const kl = props.kline
  const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
  const maxOff = Math.max(0, kl.length - count)
  offset = clamp(offset + (e.deltaY > 0 ? 3 : -3), 0, maxOff)
  draw()
}

function emitCrosshair(x) {
  if (isTrend()) { emit('crossinfo', null); return }
  const kl = props.kline
  if (!kl.length) return
  const count = Math.min(VIEW_MAX_BARS[props.view] || 60, kl.length)
  const { window: win, offset: off } = klineWindow(kl, count, offset)
  const w = canvasRef.value.clientWidth
  const i = clamp(Math.floor((x - 0) / w * win.length), 0, win.length - 1)
  const k = win[i]
  const gI = kl.indexOf(k)
  const prevClose = gI > 0 ? kl[gI - 1].close : k.open
  const chg = k.close - prevClose
  emit('crossinfo', {
    time: k.time, open: k.open, high: k.high, low: k.low, close: k.close,
    prevClose, amount: k.volume * 100 * k.close,
    chg, chgPct: prevClose ? chg / prevClose * 100 : 0,
    point: { x, y: hover?.y ?? 0 },
  })
}

watch(() => [props.view, props.trend, props.kline, props.quote, props.subInd, props.chan, props.wave, props.overlays.ma, props.overlays.boll, props.indCache], () => {
  draw()
}, { deep: true })

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
      @pointerup="onPointerUp" @pointerleave="onPointerLeave"
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
}
.scc-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  cursor: crosshair;
}
@media (min-width: 481px) {
  .scc-wrap { min-height: 360px; }
}
</style>
