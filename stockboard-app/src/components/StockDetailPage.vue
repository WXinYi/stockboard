<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useStockDetail } from '../composables/useStockDetail.js'
import { calcMA, calcBOLL, calcMACD, calcKDJ, calcRSI, calcWR, calcVOLMA, calcFractals, calcBis, calcZhongshu, calcChanSignals, calcWaves } from '../utils/indicators.js'

defineOptions({ name: 'StockDetailPage' })

const route = useRoute()
const router = useRouter()
const code = computed(() => route.params.code)
const qname = computed(() => route.query.name || '')

const { quote, kline, trend, loading, error, loadQuote, loadKline, loadTrend } = useStockDetail(code)

// 视图: trend 分时 / m60 60分 / day 日K / week 周K / month 月K
const views = [
  { key: 'trend', label: '分时' },
  { key: 'm60', label: '60分' },
  { key: 'day', label: '日K' },
  { key: 'week', label: '周K' },
  { key: 'month', label: '月K' },
]
const view = ref('trend')
const adjust = ref('qfq')   // 'qfq' 前复权 | '' 不复权(仅日/周/月)
const isIntraday = computed(() => view.value === 'trend' || view.value === 'm60')
const showAdjust = computed(() => view.value !== 'trend' && view.value !== 'm60')
const isKline = computed(() => view.value !== 'trend')

// 主图叠加(多选) + 副屏指标(单选); BOLL 默认不选
const overlays = reactive({ ma: true, boll: false })
const subInd = ref('none')   // 'none'|'macd'|'kdj'|'rsi'|'wr'
const subInds = [
  { key: 'none', label: '无' }, { key: 'macd', label: 'MACD' }, { key: 'kdj', label: 'KDJ' },
  { key: 'rsi', label: 'RSI' }, { key: 'wr', label: 'WR' },
]
const chan = ref(false)   // 缠论标记开关(分型/笔/中枢/三类买卖点)
const wave = ref(false)   // 波浪理论标记开关(1-2-3-4-5-A-B-C)
const waveNote = ref('')  // 波浪判定结果提示(无法判定时明示)
const SIGNAL_LABELS = { '1buy': '1买', '2buy': '2买', '3buy': '3买', '1sell': '1卖', '2sell': '2卖', '3sell': '3卖' }
const CHAN_MAX_ZHONGSHU = 10   // 最多画最近 10 个中枢(每个中枢2条线, 防止 series 过多)

const chartEl = ref(null)
const legendEl = ref(null)
let chart = null
let series = []          // 当前所有 series(切视图/指标时整体移除重建)
let markerPlugins = []   // 已挂载的 marker 插件(移除 series 前先 detach)
let indCache = null      // 当前 kline 的指标缓存(图例用, 避免每次 hover 重算)

// lightweight-charts v5: 便捷方法已移除, 统一 addSeries(def, opts, paneIndex)
let AreaSeriesDef = null
let CandlestickSeriesDef = null
let HistogramSeriesDef = null
let LineSeriesDef = null
let LineStyleDef = null
let createSeriesMarkersFn = null

// 各K线视图默认显示最近 N 根, 留出缩放/拖动空间
const VIEW_MAX_BARS = { m60: 120, day: 90, week: 60, month: 40 }
const MA_COLORS = { 5: '#f2a900', 10: '#9b59b6', 20: '#27ae60', 60: '#2980b9' }
const BOLL_COLORS = { up: '#e74c3c', mid: '#f39c12', lo: '#27ae60' }

function fmt(v, digits = 2) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(digits) : '—' }
function pct(v) { return typeof v === 'number' ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }
function wan(v) { return (typeof v === 'number' && isFinite(v)) ? v.toLocaleString() : '—' }
function fmtVol(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return String(Math.round(v))
}

const isUp = computed(() => (typeof quote.value?.changePct === 'number' ? quote.value.changePct >= 0 : false))
const upColor = computed(() => (isUp.value ? '#e74c3c' : '#27ae60'))

function goH5() { router.push('/stock/' + code.value + '/h5') }

function onResize() { if (chart && chartEl.value) chart.applyOptions({ width: chartEl.value.clientWidth }) }

// ── 指标缓存 ──
function computeIndicators() {
  const kl = kline.value
  const macd = calcMACD(kl)
  const fractals = calcFractals(kl)
  const bis = calcBis(kl)
  const zhongshu = calcZhongshu(kl, bis)
  const chanSignals = calcChanSignals(kl, macd, bis, zhongshu)
  indCache = {
    ma: calcMA(kl), boll: calcBOLL(kl), volma: calcVOLMA(kl),
    macd, kdj: calcKDJ(kl), rsi: calcRSI(kl), wr: calcWR(kl),
    fractals, bis, zhongshu, chanSignals,
    waves: calcWaves(kl),
    // 图例速查: bar 索引 → 分型类型 / 买卖点类型
    fractalAt: new Map(fractals.map(f => [f.i, f.type])),
    signalAt: new Map(chanSignals.map(s => [s.i, s.type])),
  }
}

// ── 图表悬浮图例(十字光标联动) ──
function fmtCandleTime(t) {
  if (typeof t === 'number') {
    const d = new Date(t * 1000)
    const p = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  }
  return String(t)
}
function setLegend(html, color) {
  const el = legendEl.value
  if (!el) return
  el.innerHTML = html
  el.style.color = color || '#666'
  el.style.display = 'block'
}
function hideLegend() {
  const el = legendEl.value
  if (el) el.style.display = 'none'
}
function renderKlineLegend(idx) {
  const arr = kline.value
  if (!arr.length || idx === undefined || idx === null || idx < 0) return hideLegend()
  const i = Math.min(Math.round(idx), arr.length - 1)
  const k = arr[i]
  if (!k) return hideLegend()
  const prevClose = i > 0 ? arr[i - 1].close : k.open
  const chg = k.close - prevClose
  const chgPct = prevClose ? (chg / prevClose * 100) : 0
  const sign = chg >= 0 ? '+' : ''
  const color = chg >= 0 ? '#e74c3c' : '#27ae60'
  let html = `<b>${fmtCandleTime(k.time)}</b>　开 <b>${k.open.toFixed(2)}</b>　高 <b>${k.high.toFixed(2)}</b>　低 <b>${k.low.toFixed(2)}</b>　收 <b>${k.close.toFixed(2)}</b>　<span style="color:${color}">${sign}${chg.toFixed(2)} (${sign}${chgPct.toFixed(2)}%)</span>`
  const extra = [`量 <b>${fmtVol(k.volume)}</b>`]
  if (indCache) {
    if (overlays.ma) {
      const parts = []
      for (const n of [5, 10, 20, 60]) {
        const v = indCache.ma[n][i]
        if (v !== null && v !== undefined) parts.push(`MA${n} ${v.toFixed(2)}`)
      }
      if (parts.length) extra.push(parts.join(' '))
    }
    const s = subInd.value
    if (s === 'macd') { const m = indCache.macd; extra.push(`DIF <b>${m.dif[i].toFixed(3)}</b> DEA <b>${m.dea[i].toFixed(3)}</b> 柱 <b>${m.hist[i].toFixed(3)}</b>`) }
    else if (s === 'kdj') { const m = indCache.kdj; extra.push(`K <b>${m.k[i].toFixed(2)}</b> D <b>${m.d[i].toFixed(2)}</b> J <b>${m.j[i].toFixed(2)}</b>`) }
    else if (s === 'rsi') { const m = indCache.rsi; extra.push(`RSI6 <b>${m[6][i].toFixed(1)}</b> RSI12 <b>${m[12][i].toFixed(1)}</b> RSI24 <b>${m[24][i].toFixed(1)}</b>`) }
    else if (s === 'wr') { const m = indCache.wr; extra.push(`WR10 <b>${m[10][i].toFixed(1)}</b> WR6 <b>${m[6][i].toFixed(1)}</b>`) }
    if (chan.value) {
      const parts2 = []
      const ft = indCache.fractalAt.get(i)
      if (ft) parts2.push(ft === 1 ? '顶分型' : '底分型')
      const st = indCache.signalAt.get(i)
      if (st) parts2.push(SIGNAL_LABELS[st])
      if (parts2.length) extra.push(`缠 <b>${parts2.join(' ')}</b>`)
    }
  }
  html += '<br>' + extra.join('　')
  setLegend(html, color)
}
function renderLastLegend() {
  if (view.value === 'trend') {
    const last = trend.value[trend.value.length - 1]
    if (last) setLegend(`${fmtCandleTime(last.time)}　价格 <b>${last.price.toFixed(2)}</b>`, upColor.value)
    else hideLegend()
  } else {
    renderKlineLegend(kline.value.length - 1)
  }
}
function onCrosshair(param) {
  if (!param || param.time === undefined) { renderLastLegend(); return }  // 移出图表 → 恢复最后一根
  if (view.value === 'trend') {
    const item = param.seriesData.get(series[0])
    if (item) setLegend(`${fmtCandleTime(param.time)}　价格 <b>${item.value.toFixed(2)}</b>`, upColor.value)
  } else {
    renderKlineLegend(param.logical)
  }
}

// ── 图表创建/series 管理 ──
async function ensureChart() {
  if (chart || !chartEl.value) return
  const m = await import('lightweight-charts')
  AreaSeriesDef = m.AreaSeries
  CandlestickSeriesDef = m.CandlestickSeries
  HistogramSeriesDef = m.HistogramSeries
  LineSeriesDef = m.LineSeries
  LineStyleDef = m.LineStyle
  createSeriesMarkersFn = m.createSeriesMarkers
  chart = m.createChart(chartEl.value, {
    width: chartEl.value.clientWidth || 360,
    height: view.value === 'trend' ? 320 : 480,
    layout: { background: { type: m.ColorType.Solid, color: '#ffffff' }, textColor: '#666', fontSize: 11 },
    grid: { vertLines: { color: '#f3f3f3' }, horzLines: { color: '#f3f3f3' } },
    crosshair: {
      mode: m.CrosshairMode.Normal,
      vertLine: { color: 'rgba(41,128,185,.45)', width: 1, style: 3, labelBackgroundColor: '#2980b9' },
      horzLine: { color: 'rgba(41,128,185,.45)', width: 1, style: 3, labelBackgroundColor: '#2980b9' },
    },
    rightPriceScale: { borderColor: '#e5e5e5', scaleMargins: { top: 0.08, bottom: 0.12 } },
    timeScale: { borderColor: '#e5e5e5', timeVisible: isIntraday.value, rightBarStaysOnScroll: true, minBarSpacing: 1.5 },
    // 手势: 滚轮/捏合缩放, 拖动平移(按住), 单指左右平移; 关掉单指上下拖动, 避免与页面滚动冲突
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
  })
  chart.subscribeCrosshairMove(onCrosshair)
  window.addEventListener('resize', onResize)
}

function track(s) { series.push(s) }

function removeAllSeries() {
  for (const s of series) { try { chart.removeSeries(s) } catch {} }
  series = []
  for (const p of markerPlugins) { try { p.detach() } catch {} }
  markerPlugins = []
}

function setPaneStretch() {
  const panes = chart.panes()
  if (panes.length >= 3) { panes[0].setStretchFactor(2.2); panes[1].setStretchFactor(0.6); panes[2].setStretchFactor(1.0) }
  else if (panes.length === 2) { panes[0].setStretchFactor(2.2); panes[1].setStretchFactor(0.7) }
}

// 折线数据(过滤 null)
function linePoints(vals) {
  const kl = kline.value
  const pts = []
  for (let i = 0; i < kl.length; i++) if (vals[i] !== null && vals[i] !== undefined) pts.push({ time: kl[i].time, value: vals[i] })
  return pts
}

function addMaLines() {
  for (const n of [5, 10, 20, 60]) {
    const pts = linePoints(indCache.ma[n])
    if (!pts.length) continue
    const line = chart.addSeries(LineSeriesDef, { color: MA_COLORS[n], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    line.setData(pts)
    track(line)
  }
}

function addBollLines() {
  for (const key of ['up', 'mid', 'lo']) {
    const pts = linePoints(indCache.boll[key])
    if (!pts.length) continue
    const line = chart.addSeries(LineSeriesDef, { color: BOLL_COLORS[key], lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
    line.setData(pts)
    track(line)
  }
}

// ── marker 统一收集: 一个 series 只能挂一个 markers 插件, 缠论与波浪标记合并进同一数组 ──
// 缠论标记: 分型箭头(顶/底) + 三类买卖点(圆点+文字, 红卖绿买)
function addChanMarkers(out) {
  const kl = kline.value
  const fs = indCache.fractals
  const sigAt = indCache.signalAt
  for (const f of fs) {
    const isTop = f.type === 1
    const s = sigAt.get(f.i)
    out.push(s
      ? { time: kl[f.i].time, position: isTop ? 'aboveBar' : 'belowBar', shape: 'circle', color: isTop ? '#c0392b' : '#16a085', size: 1.2, text: SIGNAL_LABELS[s] }
      : { time: kl[f.i].time, position: isTop ? 'aboveBar' : 'belowBar', shape: isTop ? 'arrowDown' : 'arrowUp', color: isTop ? '#c0392b' : '#16a085', size: 1 })
  }
}

// 波浪标记: 1-2-3-4-5-A-B-C 数字标签(顶红底绿), 无法判定则不画并在 waveNote 明示
function addWaveMarkers(out) {
  const w = indCache.waves
  if (w.status !== 'ok') return
  const kl = kline.value
  for (const p of w.waves) {
    out.push({ time: kl[p.i].time, position: p.type === 1 ? 'aboveBar' : 'belowBar', shape: 'circle', color: p.type === 1 ? '#c0392b' : '#16a085', size: 1.1, text: p.label })
  }
}

// 笔: 一条虚线折线连接分型极值(相邻笔共享端点, 顺序连接即为笔链)
function addChanLines() {
  const kl = kline.value
  const bis = indCache.bis
  if (!bis.length) return
  const pts = []
  let lastI = -1
  for (const b of bis) {
    if (b.from.i !== lastI) pts.push({ time: kl[b.from.i].time, value: b.from.type === 1 ? kl[b.from.i].high : kl[b.from.i].low })
    pts.push({ time: kl[b.to.i].time, value: b.to.type === 1 ? kl[b.to.i].high : kl[b.to.i].low })
    lastI = b.to.i
  }
  const line = chart.addSeries(LineSeriesDef, {
    color: 'rgba(142,68,173,.9)', lineWidth: 1, lineStyle: LineStyleDef.Dashed,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  })
  line.setData(pts)
  track(line)
}

// 中枢: 上沿 ZG / 下沿 ZD 各一条虚线水平线段(setData 不支持断点, 每中枢两条 2 点线)
function addZhongshuLines() {
  const kl = kline.value
  const zs = indCache.zhongshu.slice(-CHAN_MAX_ZHONGSHU)
  for (const z of zs) {
    const t1 = kl[z.from].time, t2 = kl[z.to].time
    for (const v of [z.zg, z.zd]) {
      const line = chart.addSeries(LineSeriesDef, {
        color: 'rgba(142,68,173,.7)', lineWidth: 1, lineStyle: LineStyleDef.Dashed,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      })
      line.setData([{ time: t1, value: v }, { time: t2, value: v }])
      track(line)
    }
  }
}

// 成交量副屏(pane 1): 柱状图(红涨绿跌) + VOL5/10 均量线
function addVolumePane() {
  const kl = kline.value
  const vol = chart.addSeries(HistogramSeriesDef, { priceFormat: { type: 'volume' } }, 1)
  vol.setData(kl.map(k => ({
    time: k.time, value: k.volume,
    color: k.close >= k.open ? 'rgba(231,76,60,.55)' : 'rgba(39,174,96,.55)',
  })))
  track(vol)
  for (const n of [5, 10]) {
    const pts = linePoints(indCache.volma[n])
    if (!pts.length) continue
    const line = chart.addSeries(LineSeriesDef, { color: n === 5 ? '#f2a900' : '#9b59b6', lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 1)
    line.setData(pts)
    track(line)
  }
}

// 副屏指标(pane 2)
function addSubLine(vals, color) {
  const pts = linePoints(vals)
  if (!pts.length) return
  const line = chart.addSeries(LineSeriesDef, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 2)
  line.setData(pts)
  track(line)
}

function addSubIndicator() {
  const s = subInd.value
  if (s === 'none') return
  const kl = kline.value
  if (s === 'macd') {
    const m = indCache.macd
    addSubLine(m.dif, '#f2a900')
    addSubLine(m.dea, '#9b59b6')
    const hist = chart.addSeries(HistogramSeriesDef, {}, 2)
    hist.setData(kl.map((k, i) => ({ time: k.time, value: m.hist[i], color: m.hist[i] >= 0 ? 'rgba(231,76,60,.55)' : 'rgba(39,174,96,.55)' })))
    track(hist)
  } else if (s === 'kdj') { addSubLine(indCache.kdj.k, '#f2a900'); addSubLine(indCache.kdj.d, '#9b59b6'); addSubLine(indCache.kdj.j, '#27ae60') }
  else if (s === 'rsi') { addSubLine(indCache.rsi[6], '#f2a900'); addSubLine(indCache.rsi[12], '#9b59b6'); addSubLine(indCache.rsi[24], '#27ae60') }
  else if (s === 'cci') { addSubLine(indCache.cci, '#f2a900') }
  else if (s === 'wr') { addSubLine(indCache.wr[10], '#f2a900'); addSubLine(indCache.wr[6], '#9b59b6') }
  else if (s === 'obv') { addSubLine(indCache.obv, '#f2a900') }
}

function renderSeries() {
  if (!chart) return
  removeAllSeries()
  chart.applyOptions({ height: isKline.value ? 480 : 320, timeScale: { timeVisible: isIntraday.value } })
  if (view.value === 'trend') {
    const color = upColor.value
    const area = chart.addSeries(AreaSeriesDef, {
      lineColor: color, topColor: color + '33', bottomColor: color + '00',
      lineWidth: 2, priceLineVisible: false,
    })
    area.setData(trend.value.map(p => ({ time: p.time, value: p.price })))
    track(area)
    chart.timeScale().fitContent()
  } else {
    if (!kline.value.length) return
    computeIndicators()
    // pane0 蜡烛
    const candle = chart.addSeries(CandlestickSeriesDef, {
      upColor: '#e74c3c', downColor: '#27ae60',
      borderVisible: false, wickUpColor: '#e74c3c', wickDownColor: '#27ae60',
    })
    candle.setData(kline.value.map(k => ({ time: k.time, open: k.open, high: k.high, low: k.low, close: k.close })))
    track(candle)
    // 主图叠加
    if (overlays.ma) addMaLines()
    if (overlays.boll) addBollLines()
    // 缠论: 笔 + 中枢(先画线, 再画 marker, 保证标记盖在最上层)
    if (chan.value) { addChanLines(); addZhongshuLines() }
    // marker 统一收集: 缠论分型/买卖点 + 波浪标签 → 单个 markers 插件
    const markersAll = []
    if (chan.value) addChanMarkers(markersAll)
    if (wave.value) addWaveMarkers(markersAll)
    if (markersAll.length) markerPlugins.push(createSeriesMarkersFn(candle, markersAll))
    // 波浪判定提示(无法判定时明示, 始终标注参考)
    waveNote.value = indCache.waves.status === 'ok'
      ? `波浪:${indCache.waves.dir === 1 ? '上升' : '下跌'}推动 5浪+ABC 已识别(参考)`
      : '波浪:无法判定(参考)'
    // 成交量 + 副屏指标
    addVolumePane()
    addSubIndicator()
    setPaneStretch()
    // K线默认显示最近 N 根, 可缩放/拖动看更早数据
    const n = kline.value.length
    const max = VIEW_MAX_BARS[view.value] || 60
    const vis = Math.min(n, max)
    if (vis > 0) chart.timeScale().setVisibleLogicalRange({ from: n - vis, to: n - 1 })
  }
  renderLastLegend()
}

async function loadChart() {
  if (view.value === 'trend') await loadTrend()
  else await loadKline(view.value, adjust.value)
  await nextTick()
  await ensureChart()
  renderSeries()
}

watch([view, adjust], () => { loadChart() })
// 指标/叠加/缠论/波浪切换只重绘, 不重新拉数据
watch([subInd, chan, wave, () => overlays.ma, () => overlays.boll], () => {
  if (chart) renderSeries()
})
watch(code, () => {
  // 同一路由记录复用组件实例: 切 code 先清空旧数据并销毁旧图表, 否则残留上一只股票的行情/图表
  quote.value = null
  kline.value = []
  trend.value = []
  indCache = null
  if (chart) { chart.remove(); chart = null; series = []; markerPlugins = [] }
  loadQuote()
  loadChart()
})

onMounted(() => { loadQuote(); loadChart() })

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  if (chart) { chart.remove(); chart = null }
})
</script>

<template>
  <div class="sd-page">
    <!-- 顶部: 名称 + 代码 + H5 入口 -->
    <div class="sd-head">
      <div class="sd-name">
        <strong class="sd-title">{{ quote?.name || qname || code }}</strong>
        <span class="sd-code">{{ quote?.code || code }}</span>
      </div>
      <button class="sd-h5" @click="goH5">H5 详情 ↗</button>
    </div>

    <!-- 基本信息(无卡片, 扁平区块) -->
    <div class="sd-info">
      <div v-if="loading.quote" class="sd-loading">行情加载中…</div>
      <template v-else-if="quote">
        <div class="sd-price-row">
          <span class="sd-price" :style="{ color: upColor }">{{ fmt(quote.price) }}</span>
          <span class="sd-chg" :style="{ color: upColor }">{{ pct(quote.changePct) }}</span>
          <span class="sd-chg" :style="{ color: upColor }">{{ quote.change !== undefined && quote.change !== null ? (quote.change >= 0 ? '+' : '') + fmt(quote.change) : '' }}</span>
        </div>
        <div class="sd-grid">
          <div class="sd-cell"><span class="lbl">今开</span><span class="val">{{ fmt(quote.open) }}</span></div>
          <div class="sd-cell"><span class="lbl">最高</span><span class="val">{{ fmt(quote.high) }}</span></div>
          <div class="sd-cell"><span class="lbl">最低</span><span class="val">{{ fmt(quote.low) }}</span></div>
          <div class="sd-cell"><span class="lbl">昨收</span><span class="val">{{ fmt(quote.prevClose) }}</span></div>
          <div class="sd-cell"><span class="lbl">成交量</span><span class="val">{{ wan(quote.volume) }}手</span></div>
          <div class="sd-cell"><span class="lbl">成交额</span><span class="val">{{ wan(quote.amount) }}</span></div>
          <div class="sd-cell"><span class="lbl">换手率</span><span class="val">{{ quote.turnover !== undefined && quote.turnover !== null ? quote.turnover + '%' : '—' }}</span></div>
          <div class="sd-cell"><span class="lbl">PE(TTM)</span><span class="val">{{ fmt(quote.pe) }}</span></div>
          <div class="sd-cell"><span class="lbl">PB</span><span class="val">{{ fmt(quote.pb) }}</span></div>
          <div class="sd-cell"><span class="lbl">总市值</span><span class="val">{{ wan(quote.totalCap) }}</span></div>
          <div class="sd-cell"><span class="lbl">流通市值</span><span class="val">{{ wan(quote.floatCap) }}</span></div>
          <div class="sd-cell">
            <span class="lbl">主力净流入</span>
            <span class="val" :style="{ color: quote.mainFlowYi !== null && quote.mainFlowYi >= 0 ? '#e74c3c' : '#27ae60' }">{{ quote.mainFlowYi !== null ? quote.mainFlowYi + '亿' : '—' }}</span>
          </div>
        </div>
      </template>
      <div v-else class="sd-error">
        {{ error || '行情数据加载失败' }}
        <button class="sd-retry" @click="loadQuote">重试</button>
      </div>
    </div>

    <!-- 图表(无卡片, 全宽) -->
    <div class="sd-chart-block">
      <div class="sd-chart-head">
        <div class="sd-tabs">
          <button v-for="t in views" :key="t.key" :class="['sd-tab', { on: view === t.key }]" @click="view = t.key">{{ t.label }}</button>
        </div>
        <div v-if="showAdjust" class="sd-adjust">
          <button :class="['sd-tab', 'small', { on: adjust === 'qfq' }]" @click="adjust = 'qfq'">前复权</button>
          <button :class="['sd-tab', 'small', { on: adjust === '' }]" @click="adjust = ''">不复权</button>
        </div>
      </div>
      <!-- 缠论/波浪前置, 然后主图叠加 + 副屏指标切换(K线视图) -->
      <div v-if="isKline" class="sd-inds">
        <button :class="['sd-ibtn', 'chan', { on: chan }]" @click="chan = !chan">缠</button>
        <button :class="['sd-ibtn', 'wave', { on: wave }]" @click="wave = !wave">波</button>
        <span class="sd-isep"></span>
        <button :class="['sd-ibtn', { on: overlays.ma }]" @click="overlays.ma = !overlays.ma">MA</button>
        <button :class="['sd-ibtn', { on: overlays.boll }]" @click="overlays.boll = !overlays.boll">BOLL</button>
        <span class="sd-isep"></span>
        <button v-for="s in subInds" :key="s.key" :class="['sd-ibtn', { on: subInd === s.key }]" @click="subInd = s.key">{{ s.label }}</button>
      </div>
      <div v-if="wave" class="sd-wave-note">{{ waveNote || '波浪:计算中…' }}</div>
      <div v-if="loading.chart && !kline.length && !trend.length" class="sd-loading">图表加载中…</div>
      <div v-else-if="!loading.chart && !kline.length && !trend.length" class="sd-error">
        {{ error || '图表数据加载失败' }}
        <button class="sd-retry" @click="loadChart">重试</button>
      </div>
      <div v-else ref="chartEl" class="sd-chart" :class="{ kline: isKline }">
        <div ref="legendEl" class="sd-legend"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 全宽页面: main-content.stock-page 已去左右留白, 内部区块自行控制 padding */
.sd-page { padding: 4px 0 12px; }
.sd-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; padding: 0 14px; flex-wrap: wrap; }
.sd-info { margin-bottom: 14px; padding: 0 14px; }
.sd-chart-block { margin-bottom: 14px; }
.sd-name { display: flex; align-items: baseline; gap: 8px; }
.sd-title { font-size: 20px; font-weight: 600; color: #111; }
.sd-code { color: #999; font-size: 13px; }
.sd-h5 { margin-left: auto; border: 1px solid #2980b9; color: #2980b9; background: #fff; font-size: 12px; padding: 5px 12px; border-radius: 8px; cursor: pointer; }

.sd-price-row { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.sd-price { font-size: 30px; font-weight: 700; }
.sd-chg { font-size: 16px; font-weight: 600; }

.sd-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px 12px; }
.sd-cell { display: flex; flex-direction: column; gap: 2px; }
.sd-cell .lbl { font-size: 11px; color: #999; }
.sd-cell .val { font-size: 13px; color: #333; font-weight: 500; }

.sd-chart-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; padding: 0 14px; flex-wrap: wrap; }
.sd-tabs { display: flex; gap: 4px; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.sd-adjust { margin-left: auto; display: flex; gap: 4px; }
.sd-tab { border: none; background: #f0f2f5; font-size: 12px; padding: 5px 12px; border-radius: 8px; color: #666; cursor: pointer; white-space: nowrap; }
.sd-tab.small { padding: 4px 9px; }
.sd-tab.on { background: #2980b9; color: #fff; }

/* 主图叠加 + 副屏指标切换 */
.sd-inds { display: flex; gap: 4px; margin-bottom: 8px; padding: 0 14px; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.sd-ibtn { border: none; background: #f0f2f5; font-size: 11px; padding: 4px 9px; border-radius: 7px; color: #666; cursor: pointer; white-space: nowrap; }
.sd-ibtn.on { background: #2980b9; color: #fff; }
.sd-ibtn.chan.on { background: #8e44ad; }   /* 缠论按钮用紫色, 与笔/中枢线同色系 */
.sd-ibtn.wave.on { background: #d35400; }   /* 波浪按钮用橙色区分 */
.sd-wave-note { font-size: 11px; color: #d35400; margin-bottom: 6px; padding: 0 14px; }
.sd-isep { width: 1px; background: #e5e5e5; margin: 0 2px; flex: none; }

.sd-chart { position: relative; width: 100%; height: 320px; }
.sd-chart.kline { height: 480px; }
.sd-legend { position: absolute; top: 4px; left: 8px; z-index: 10; font-size: 11px; color: #666; pointer-events: none; white-space: nowrap; max-width: 96%; overflow: hidden; text-overflow: ellipsis; background: rgba(255,255,255,.85); border-radius: 6px; padding: 3px 8px; line-height: 1.8; }
.sd-loading { padding: 40px 0; text-align: center; color: #999; font-size: 13px; }
.sd-error { padding: 40px 0; text-align: center; color: #c0392b; font-size: 13px; }
.sd-retry { margin-left: 8px; border: 1px solid #2980b9; background: #fff; color: #2980b9; font-size: 12px; padding: 4px 12px; border-radius: 8px; cursor: pointer; }

@media (max-width: 480px) {
  .sd-grid { grid-template-columns: repeat(3, 1fr); }
}
/* 桌面端: 内层留白与其它页 main-content 水平 padding(28px) 对齐 */
@media (min-width: 768px) {
  .sd-page { padding: 8px 0 20px; }
  .sd-head, .sd-chart-head, .sd-inds, .sd-wave-note, .sd-info { padding-left: 28px; padding-right: 28px; }
  .sd-info { margin-bottom: 18px; }
}
</style>
