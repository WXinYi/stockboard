<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useStockDetail } from '../composables/useStockDetail.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import { calcMA, calcBOLL, calcMACD, calcKDJ, calcRSI, calcWR, calcVOLMA, calcFractals, calcBis, calcZhongshu, calcChanSignals, calcWaves, calcDivergence } from '../utils/indicators.js'
import { loadTencentPankou } from '../utils/pankou.js'
import PankouPanel from './PankouPanel.vue'
import BidAuctionCard from './BidAuctionCard.vue'
import LimitGeneCard from './LimitGeneCard.vue'
import {
  fetchBoards, fetchLimitReason, fetchMainFlow, isTradingTime,
  fetchZhangTingGene, fetchStockBid,
  fetchInfoList,
  fetchF10Company, fetchF10Finance, fetchF10Shareholders, fetchF10Valuation,
} from '../composables/useKplApi.js'

defineOptions({ name: 'StockDetailPage' })

const route = useRoute()
const router = useRouter()
const code = computed(() => route.params.code)
const qname = computed(() => route.query.name || '')

const { quote, kline, trend, loading, error, loadQuote, loadKline, loadTrend } = useStockDetail(code)

// ── 功能卡数据(涨停基因/竞价分时; 低频, 激活时加载一次) ──
const gene = ref(null)   // fetchZhangTingGene 六维; null=未加载/失败(空态)
const bid = ref(null)    // fetchStockBid 竞价序列; null=未加载/非竞价时段(空态)

// ── 板块胶囊 / 涨停原因(开盘啦) ──
const boards = ref(null)          // null=未加载失败, []或数组=成功
const boardsLoading = ref(false)
const boardsMore = ref(false)
const limitReason = ref(null)     // {zsCodes, reason} 或 null(接口无数据)
const limitMore = ref(false)
// 仅当日涨停才展示涨停原因: GetDayZhangTing 对非涨停股也返回"最近一次涨停原因"(如茅台返回7月17日) →
// 用行情涨停价口径判断: 现价 >= 涨停价; KPL 主源缺失(降级源)时退用涨幅 ≥9.8%
const isLimitUp = computed(() => {
  const q = quote.value
  if (!q) return false
  if (q.upPx != null && +q.upPx > 0) return +q.price >= +q.upPx
  return typeof q.changePct === 'number' && q.changePct >= 9.8
})
const boardNameById = computed(() => {
  const m = {}
  for (const b of boards.value || []) m[b.code] = b.name
  return m
})

// ── 底部 资讯|基本面 tab 区(默认展开资讯+新闻, 用户要求) ──
const gridMore = ref(false)       // 基础信息 L3 次要 8 格折叠
const moreTab = ref('info')       // 'info' / 'f10'
const infoTypes = [{ type: 1, label: '新闻' }, { type: 2, label: '研报' }, { type: 3, label: '公告' }]
const infoType = ref(1)
const infoList = ref([])
const infoLoading = ref(false)
const infoError = ref(false)
const infoPage = ref(0)
const infoHasMore = ref(false)
const f10Types = [{ key: 'company', label: '公司' }, { key: 'finance', label: '财务' }, { key: 'holders', label: '股东' }, { key: 'valuation', label: '估值' }]
const f10Type = ref('company')
const f10Company = ref(null)
const f10Finance = ref(null)
const f10Holders = ref(null)
const f10Valuation = ref([])
const f10Loading = ref(false)
const f10Error = ref('')

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
const subInd = ref('macd')   // 副屏指标: 默认展示 MACD(用户要求); 'none'|'macd'|'kdj'|'rsi'|'wr'
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
let chart = null

// 图表高度: 方案B = 图表区宽×0.62 clamp(桌面 220~420, 移动 220~280); 与 CSS 内联高度联动
const chartH = ref(360)
function recomputeChartHeight() {
  const w = chartEl.value ? chartEl.value.clientWidth : (window.innerWidth || 360)
  const mobile = window.innerWidth <= 480
  const raw = w * 0.62
  chartH.value = Math.round(mobile ? Math.min(Math.max(raw, 220), 280) : Math.min(Math.max(raw, 220), 420))
  return chartH.value
}
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
// 金额格式化(元 → 亿/万 带单位): 成交额/总市值/流通市值/主力净流入
function wan(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const a = Math.abs(v)
  if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return v.toLocaleString()
}
function fmtVol(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return String(Math.round(v))
}

const isUp = computed(() => (typeof quote.value?.changePct === 'number' ? quote.value.changePct >= 0 : false))
const upColor = computed(() => (isUp.value ? '#e74c3c' : '#27ae60'))

function goH5() { router.push('/stock/' + code.value + '/h5') }

function onResize() {
  if (!chart || !chartEl.value) return
  const h = recomputeChartHeight()
  chart.applyOptions({ width: chartEl.value.clientWidth, height: h })
}

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
    waves: calcWaves(kl), divergences: calcDivergence(kl, macd),
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
// 光标处 K线数据 → 顶部 16 格联动(今开/最高/最低/昨收/成交额); null = 无光标, 显示当日实时
const crossInfo = ref(null)
function onCrosshair(param) {
  // 分时视图/移出图表 → 恢复当日实时; K线视图 → 16 格显示光标处该日数据(图内不悬浮, 不遮挡 K线)
  if (view.value === 'trend' || !param || param.time === undefined) { crossInfo.value = null; return }
  const arr = kline.value
  const i = Math.min(Math.round(param.logical), arr.length - 1)
  const k = arr[i]
  crossInfo.value = k ? (() => {
    const prevClose = i > 0 ? arr[i - 1].close : k.open
    const chg = k.close - prevClose
    return {
      open: k.open, high: k.high, low: k.low, close: k.close, prevClose,
      amount: k.volume * 100 * k.close,   // 额 = 手×100股×收盘价 近似(腾讯K线无成交额字段)
      chg, chgPct: prevClose ? (chg / prevClose * 100) : 0,   // 头部大价格联动(颜色随光标K线涨跌)
    }
  })() : null
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
  recomputeChartHeight()
  chart = m.createChart(chartEl.value, {
    width: chartEl.value.clientWidth || 360,
    height: chartH.value,
    layout: { background: { type: m.ColorType.Solid, color: '#ffffff' }, textColor: '#666', fontSize: 11 },
    grid: { vertLines: { color: '#f3f3f3' }, horzLines: { color: '#f3f3f3' } },
    crosshair: {
      mode: m.CrosshairMode.Normal,
      vertLine: { color: 'rgba(41,128,185,.45)', width: 1, style: 3, labelBackgroundColor: '#2980b9' },
      horzLine: { color: 'rgba(41,128,185,.45)', width: 1, style: 3, labelBackgroundColor: '#2980b9' },
    },
    rightPriceScale: { borderColor: '#e5e5e5', scaleMargins: { top: 0.08, bottom: 0.12 } },
    timeScale: { borderColor: '#e5e5e5', timeVisible: isIntraday.value, rightBarStaysOnScroll: false, minBarSpacing: 0.5 },
    // 手势: 滚轮/捏合缩放, 按住平移(水平); 关闭 Y 轴拖动(上下滑动手势)
    // 垂直方向交还页面滚动: 容器 touch-action: pan-y(见 .sd-chart), 单指上下滑=滚页面
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: false },
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

// 主图:量:副图 = 3:1:1(spec §3 方案B); 无副图时主图:量 = 2.2:1
function setPaneStretch() {
  const panes = chart.panes()
  if (panes.length >= 3) { panes[0].setStretchFactor(3); panes[1].setStretchFactor(1); panes[2].setStretchFactor(1) }
  else if (panes.length === 2) { panes[0].setStretchFactor(2.2); panes[1].setStretchFactor(1) }
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
// 缠论标记: 分型箭头(顶/底) + 三类买卖点(圆点+文字, 红卖绿买) + 背离(顶背离橙/底背离蓝紫)
function addChanMarkers(out) {
  const kl = kline.value
  const fs = indCache.fractals
  const sigAt = indCache.signalAt
  const divMap = new Map(indCache.divergences.map(d => [d.i, d.type]))
  for (const f of fs) {
    const isTop = f.type === 1
    const s = sigAt.get(f.i)
    const dv = divMap.get(f.i)
    if (dv) {
      // 背离优先: 圆点 + 文字(顶背离/底背离), 比普通分型更醒目
      out.push({ time: kl[f.i].time, position: isTop ? 'aboveBar' : 'belowBar', shape: 'circle', color: dv === 'top' ? '#e67e22' : '#5b2c8f', size: 1.7, text: dv === 'top' ? '顶背离' : '底背离' })
    } else {
      out.push(s
        ? { time: kl[f.i].time, position: isTop ? 'aboveBar' : 'belowBar', shape: 'circle', color: isTop ? '#c0392b' : '#16a085', size: 1.3, text: SIGNAL_LABELS[s] }
        : { time: kl[f.i].time, position: isTop ? 'aboveBar' : 'belowBar', shape: isTop ? 'arrowDown' : 'arrowUp', color: isTop ? '#c0392b' : '#16a085', size: 1 })
    }
  }
}

// 波浪标记: 1-2-3-4-5-A-B-C 数字标签(顶红底绿, 大圆点+文字更清晰), 无法判定则不画并在 waveNote 明示
function addWaveMarkers(out) {
  const w = indCache.waves
  if (w.status !== 'ok' && w.status !== 'ok5') return
  const kl = kline.value
  for (const p of w.waves) {
    out.push({ time: kl[p.i].time, position: p.type === 1 ? 'aboveBar' : 'belowBar', shape: 'circle', color: p.type === 1 ? '#c0392b' : '#16a085', size: 1.5, text: p.label })
  }
}

// 笔: 一条实线折线连接分型极值(相邻笔共享端点, 顺序连接即为笔链); 2px 深紫保证可见
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
    color: '#7d3c98', lineWidth: 2, lineStyle: LineStyleDef.Solid,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
  })
  line.setData(pts)
  track(line)
}

// 中枢: 上沿 ZG / 下沿 ZD 各一条水平线段(setData 不支持断点, 每中枢两条 2 点线); 1.5px 实线
function addZhongshuLines() {
  const kl = kline.value
  const zs = indCache.zhongshu.slice(-CHAN_MAX_ZHONGSHU)
  for (const z of zs) {
    const t1 = kl[z.from].time, t2 = kl[z.to].time
    for (const v of [z.zg, z.zd]) {
      const line = chart.addSeries(LineSeriesDef, {
        color: '#7d3c98', lineWidth: 1.5, lineStyle: LineStyleDef.Solid,
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

// 分时副屏(pane 2): 按 subInd 渲染(macd/kdj/rsi/wr), 与K线共用指标选择器
// 每分钟为一个价格点, 构造高=低=收盘的伪K线喂给指标纯函数
function addTrendSubIndicator() {
  const s = subInd.value
  if (s === 'none') return
  const t = trend.value
  if (!t.length) return
  const tkl = t.map(p => ({ open: p.price, high: p.price, low: p.price, close: p.price, volume: p.vol }))
  const line = (vals, color) => {
    const pts = []
    for (let i = 0; i < vals.length; i++) {
      if (Number.isFinite(vals[i])) pts.push({ time: t[i].time, value: vals[i] })
    }
    if (!pts.length) return
    const l = chart.addSeries(LineSeriesDef, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 2)
    l.setData(pts)
    track(l)
  }
  const hist = (vals) => {
    const h = chart.addSeries(HistogramSeriesDef, {}, 2)
    h.setData(t.map((p, i) => ({
      time: p.time, value: Number.isFinite(vals[i]) ? vals[i] : 0,
      color: vals[i] >= 0 ? 'rgba(231,76,60,.55)' : 'rgba(39,174,96,.55)',
    })))
    track(h)
  }
  if (s === 'macd') {
    const m = calcMACD(tkl)
    line(m.dif, '#f2a900'); line(m.dea, '#9b59b6'); hist(m.hist)
  } else if (s === 'kdj') {
    const k = calcKDJ(tkl)
    line(k.k, '#f2a900'); line(k.d, '#9b59b6'); line(k.j, '#27ae60')
  } else if (s === 'rsi') {
    const r = calcRSI(tkl)
    line(r[6], '#f2a900'); line(r[12], '#9b59b6'); line(r[24], '#27ae60')
  } else if (s === 'wr') {
    const w = calcWR(tkl)
    line(w[10], '#f2a900'); line(w[6], '#9b59b6')
  }
}

function renderSeries() {
  if (!chart) return
  removeAllSeries()
  // 分时/K线同高 = 方案B(clamp 图表区宽×0.62)
  recomputeChartHeight()
  chart.applyOptions({ height: chartH.value, timeScale: { timeVisible: isIntraday.value } })
  if (view.value === 'trend') {
    // 分时图: 横轴固定全天 09:30~15:00(线只画到当前时间), 右边缘不锁数据(fixRightEdge 会把范围拉回最后数据点, 令固定范围失效)
    chart.timeScale().applyOptions({ rightOffset: 0, fixLeftEdge: true, fixRightEdge: false })
    const upP = quote.value?.upPx, downP = quote.value?.downPx
    // 左轴涨跌%坐标系: 承载 series 绑 left scale(百分比是价格的线性变换 → 与价格线视觉重合),
    // 刻度 custom formatter 显示 +X.X%; 左轴范围锁定 跌停%→涨停%(涨跌停坐标系)
    // v5 要点: leftPriceScale.visible 在 chart 级; 刻度渲染依赖 scale 上有可见 series(透明色即可)
    chart.applyOptions({ leftPriceScale: { visible: true, minimumWidth: 48 } })
    const prevClose = quote.value?.prevClose
    if (typeof prevClose === 'number' && prevClose > 0) {
      const pctLine = chart.addSeries(LineSeriesDef, {
        priceScaleId: 'left',
        color: 'rgba(0,0,0,0)', lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        priceFormat: { type: 'custom', formatter: v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%' },
      })
      pctLine.setData(trend.value.map(p => ({ time: p.time, value: +(((p.price - prevClose) / prevClose) * 100).toFixed(2) })))
    }
    const color = upColor.value
    const area = chart.addSeries(AreaSeriesDef, {
      lineColor: color, topColor: color + '33', bottomColor: color + '00',
      lineWidth: 2, priceLineVisible: false,
    })
    // 横轴固定全天: 主 line 追加 whitespace bar(只有 time 无 value, 不画线) 到 15:00,
    // 让 baseIndex/时间轴锚点延伸到收盘 —— setVisibleRange 的 to 才不会被 v5 clamp 到最后一个真实数据点
    const areaData = trend.value.map(p => ({ time: p.time, value: p.price }))
    const lastT = areaData[areaData.length - 1]?.time
    if (lastT) {
      const d0 = new Date(lastT * 1000)
      const closeTs = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), d0.getUTCDate(), 15, 0) / 1000
      if (lastT < closeTs) {
        for (let t = lastT + 60; t <= closeTs; t += 60) areaData.push({ time: t })
      }
    }
    area.setData(areaData)
    track(area)
    // 均价线: 累计成交额/累计成交量(vol=手×100), 黄色实线(用户要求不用虚线)
    let cumAmt = 0, cumVol = 0
    const avgPoints = []
    for (const p of trend.value) {
      cumAmt += p.amount
      cumVol += p.vol
      if (cumVol > 0) avgPoints.push({ time: p.time, value: +(cumAmt / (cumVol * 100)).toFixed(3) })
    }
    if (avgPoints.length) {
      const avg = chart.addSeries(LineSeriesDef, {
        color: '#f2a900', lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
      })
      avg.setData(avgPoints)
    }
    // 分时成交量副屏(pane 1): 每分钟柱状, 红涨绿跌(相对昨收)
    const volSer = chart.addSeries(HistogramSeriesDef, { priceFormat: { type: 'volume' } }, 1)
    volSer.setData(trend.value.map(p => ({
      time: p.time, value: p.vol,
      color: typeof prevClose === 'number' && p.price >= prevClose ? 'rgba(231,76,60,.55)' : 'rgba(39,174,96,.55)',
    })))
    track(volSer)
    // 分时副屏(pane 2): 按 subInd 渲染(macd/kdj/rsi/wr, 与K线共用指标选择器)
    addTrendSubIndicator()
    setPaneStretch()
    // 横轴固定全天 09:30~15:00(线只画到当前时间): whitespace 锚点延伸到收盘后,
    // setVisibleRange 的 rightOffset = 15:00 - baseIndex, 视图恰好 [09:30, 15:00] 全宽
    const first = trend.value[0]
    if (first && first.time) {
      const d = new Date(first.time * 1000)
      const openTs = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 9, 30) / 1000
      const closeTs = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 15, 0) / 1000
      chart.timeScale().applyOptions({ rightOffset: 0, fixLeftEdge: true, fixRightEdge: false })
      chart.timeScale().setVisibleRange({ from: openTs, to: closeTs })
    } else {
      chart.timeScale().fitContent()
    }
    // 价格轴范围锁定放最后(series 就位后 setVisibleRange 才生效, 否则被 addSeries 数据范围覆盖):
    // 右轴 跌停价~涨停价(价格) / 左轴 跌停%~涨停%(百分比) — 分时最高最低固定为当日涨跌停
    if (typeof upP === 'number' && typeof downP === 'number' && upP > downP) {
      const rs = chart.priceScale('right')
      rs.applyOptions({ autoScale: false })
      rs.setVisibleRange({ from: downP, to: upP })
      if (typeof prevClose === 'number' && prevClose > 0) {
        const ls = chart.priceScale('left')
        ls.applyOptions({ autoScale: false })
        ls.setVisibleRange({
          from: +(((downP - prevClose) / prevClose) * 100).toFixed(2),
          to: +(((upP - prevClose) / prevClose) * 100).toFixed(2),
        })
      }
    }
  } else {
    if (!kline.value.length) return
    // K 线: 恢复时间轴可移动/缩放 + Y 轴自动 + 隐藏左轴(分时锁定设置不影响 K 线)
    chart.timeScale().applyOptions({ fixLeftEdge: false, fixRightEdge: false })
    chart.priceScale('right').applyOptions({ autoScale: true })
    chart.applyOptions({ leftPriceScale: { visible: false } })
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
    if (markersAll.length) {
      // v5: createSeriesMarkers 创建即自动挂载到 series(detach 用于移除), 只需记录插件供清理
      markerPlugins.push(createSeriesMarkersFn(candle, markersAll))
    }
    // 波浪判定提示(无法判定时明示, 始终标注参考): ok=完整5+3, ok5=5浪推动(调整未确认)
    const ws = indCache.waves.status
    waveNote.value = ws === 'ok'
      ? `波浪:${indCache.waves.dir === 1 ? '上升' : '下跌'}推动 5浪+ABC 已识别(参考)`
      : ws === 'ok5'
        ? `波浪:${indCache.waves.dir === 1 ? '上升' : '下跌'}推动 5浪已识别, 调整浪未确认(参考)`
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
  crossInfo.value = null   // 重绘后无光标 → 16 格恢复当日实时
}

async function loadChart() {
  if (view.value === 'trend') await loadTrend()
  else await loadKline(view.value, adjust.value)
  await nextTick()
  await ensureChart()
  renderSeries()
}

watch([view, adjust], () => { loadChart() })
// 行情异步到达后重绘: 左轴%/分时Y轴锁定依赖 prevClose/upPx/downPx, 首帧 renderSeries 时 quote 可能未就绪
watch(quote, (q) => {
  if (chart) renderSeries()
  // 盘口未就绪时补拉(首次加载/失败后); 已加载则 useStockDetail 刷新保留, 不重复触发
  if (q && !q.pankou) loadPankou(true)
})
// 指标/叠加/缠论/波浪切换只重绘, 不重新拉数据
watch([subInd, chan, wave, () => overlays.ma, () => overlays.boll], () => {
  if (chart) renderSeries()
})
// 下拉刷新: 仅当前激活页面响应(usePullRefresh 按激活态过滤)
usePullRefresh(() => {
  loadQuote(true); loadChart(); loadBoards(true); loadLimit(true); loadMainFlow(true); loadPankou(true); loadGene(true); loadBid(true); loadInfo(true)
})

watch(code, () => {
  // 路由离开详情页(code 变 undefined)时不重载, 否则跳板块/盘面页会发一堆无效请求
  if (!code.value) return
  // 同一路由记录复用组件实例: 切 code 先清空旧数据并销毁旧图表, 否则残留上一只股票的行情/图表
  quote.value = null
  kline.value = []
  trend.value = []
  indCache = null
  boards.value = null
  limitReason.value = null
  gene.value = null
  bid.value = null
  moreTab.value = 'info'
  infoList.value = []
  f10Company.value = null
  f10Finance.value = null
  f10Holders.value = null
  f10Valuation.value = []
  if (chart) { chart.remove(); chart = null; series = []; markerPlugins = [] }
  loadQuote()
  loadChart()
  loadBoards()
  loadLimit()
  loadMainFlow()
  loadPankou()
  loadGene()
  loadBid()
  loadInfo()
})

// ── 板块胶囊(开盘啦 GetFeaturedSection) ──
async function loadBoards(silent = false) {
  try {
    const res = await fetchBoards(code.value, silent)
    if (res) boards.value = res
  } catch (e) { /* 板块失败不阻塞 */ }
  boardsLoading.value = false
}

// ── 涨停原因(GetDayZhangTing, 仅当日涨停显示) ──
async function loadLimit(silent = false) {
  try {
    const res = await fetchLimitReason(code.value, silent)
    if (res !== undefined) limitReason.value = res
  } catch (e) { /* 非涨停或失败: 隐藏 */ }
}

// ── 主力净流入(精确主力口径 StockDPRealData, 15s 轮询) ──
async function loadMainFlow(silent = false) {
  try {
    const res = await fetchMainFlow(code.value, silent)
    if (res && quote.value) quote.value.mainFlowYi = res.zlJe / 1e8
  } catch (e) { /* 失败保留旧值 */ }
}

// ── 右盘口(腾讯五档, 5s 轮询; 数据并入 quote.pankou, useStockDetail 刷新 quote 时保留) ──
let pankouLoading = false
async function loadPankou(silent = false) {
  if (pankouLoading) return
  pankouLoading = true
  try {
    const r = await loadTencentPankou(code.value, silent)
    if (r && quote.value) quote.value.pankou = r
  } catch (e) { /* 失败: 面板显示空态, 等下一个 tick */ }
  finally { pankouLoading = false }
}

// ── 涨停基因 + 竞价分时(低频: 激活加载一次, 不轮询; 失败留空态) ──
async function loadGene(silent = false) {
  try { gene.value = await fetchZhangTingGene(code.value, silent) }
  catch (e) { gene.value = null }
}
async function loadBid(silent = false) {
  try { bid.value = await fetchStockBid(code.value, silent) }
  catch (e) { bid.value = null }
}

// ── 资讯 tab: 新闻|研报|公告 列表 + 正文懒加载 ──
async function loadInfo(reset = true) {
  if (reset) { infoList.value = []; infoPage.value = 0; infoHasMore.value = false }
  infoLoading.value = true
  infoError.value = false
  try {
    const rows = await fetchInfoList(code.value, infoType.value, infoPage.value)
    if (!rows) throw new Error('empty')
    infoList.value = reset ? rows : [...infoList.value, ...rows]
    infoHasMore.value = rows.length >= 34
  } catch (e) {
    if (!infoList.value.length) infoError.value = true
  } finally { infoLoading.value = false }
}
function switchInfo(t) { infoType.value = t; loadInfo(true) }
async function loadMoreInfo() { infoPage.value += 1; loadInfo(false) }
// 资讯行 → 跳转独立详情页(新闻/研报/公告共用 /info/:code/:iid); 标题经 sessionStorage 传递
function goInfo(it) {
  try { sessionStorage.setItem('info_title_' + it.iid, it.title) } catch (e) { /* 隐私模式忽略 */ }
  router.push({ path: `/info/${code.value}/${it.iid}`, query: { type: infoType.value, name: qname.value } })
}

// ── 基本面 tab: 公司|财务|股东|估值(每 code 缓存一次) ──
async function loadF10() {
  f10Loading.value = true
  f10Error.value = ''
  try {
    const c = code.value
    if (f10Type.value === 'company') {
      if (!f10Company.value) f10Company.value = await fetchF10Company(c)
    } else if (f10Type.value === 'finance') {
      if (!f10Finance.value) f10Finance.value = await fetchF10Finance(c)
    } else if (f10Type.value === 'holders') {
      if (!f10Holders.value) f10Holders.value = await fetchF10Shareholders(c)
    } else {
      if (!f10Valuation.value.length) f10Valuation.value = (await fetchF10Valuation(c)) || []
    }
  } catch (e) { f10Error.value = '加载失败' }
  finally { f10Loading.value = false }
}
function switchF10(t) { f10Type.value = t; loadF10() }
function toggleMore(tab) {
  moreTab.value = moreTab.value === tab ? null : tab
  if (moreTab.value === 'info') loadInfo(true)
  else if (moreTab.value === 'f10') loadF10()
}

// ── F10 派生数据 ──
const mainFlowText = computed(() => {
  const v = quote.value?.mainFlowYi
  return (typeof v === 'number' && isFinite(v)) ? v.toFixed(2) + '亿' : '—'
})
const shownBoards = computed(() => (boardsMore.value ? boards.value : (boards.value || []).slice(0, 12)))
const f10CpRows = computed(() => {
  const biz = f10Company.value?.biz
  if (!biz || !Array.isArray(biz.cp) || !biz.cp.length) return { date: '', rows: [] }
  const rows = (biz.cp[0] || []).map(r => ({ name: r[0], amount: r[1], ratio: r[2], margin: r[3] }))
  return { date: (biz.cpDate || [])[0] || '', rows }
})
const f10FinanceRows = computed(() => {
  const s = f10Finance.value
  if (!s) return []
  return Object.entries(s).map(([label, arr]) => {
    const y = arr[arr.length - 1] || {}
    const q = (y.quarter || []).slice(-1)[0] || {}
    return { label, yname: y.name || '', yval: y.value, ytb: y.tb, qname: q.name || '', qval: q.value, qtb: q.tb }
  })
})
const MONEY_KEYS = ['营业收入', '归母净利润', '扣非净利润', '经营现金流']
function fmtFin(v, label) {
  if (v === null || v === undefined || v === '') return '—'
  const n = parseFloat(v)
  if (!isFinite(n)) return String(v)
  if (MONEY_KEYS.includes(label)) {
    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿'
    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万'
    return String(Math.round(n))
  }
  return n.toFixed(2) + '%'
}
function fmtTb(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = parseFloat(v)
  return isFinite(n) ? (n >= 0 ? '+' : '') + n.toFixed(2) + '%' : String(v)
}
// 估值 PE 内联 SVG 折线(最近 ~120 点, 340×120 视口)
const pePoints = computed(() => {
  const pts = (f10Valuation.value || []).slice(-120)
  if (pts.length < 2) return ''
  const vals = pts.map(p => p.pe).filter(v => typeof v === 'number' && isFinite(v))
  if (!vals.length) return ''
  const min = Math.min(...vals), max = Math.max(...vals)
  const range = (max - min) || 1
  return pts.map((p, i) => `${(i / (pts.length - 1) * 340).toFixed(1)},${(110 - (p.pe - min) / range * 100).toFixed(1)}`).join(' ')
})
const peStats = computed(() => {
  const pts = (f10Valuation.value || []).slice(-120).filter(p => typeof p.pe === 'number' && isFinite(p.pe))
  if (!pts.length) return { last: '—', max: '—', min: '—' }
  return { last: pts[pts.length - 1].pe.toFixed(2), max: Math.max(...pts.map(p => p.pe)).toFixed(2), min: Math.min(...pts.map(p => p.pe)).toFixed(2) }
})

// ── 导航/工具 ──
function goBoard(bkCode, name) { router.push({ path: '/board/' + bkCode, query: { name } }) }
function firstLine(s) { if (!s) return ''; const i = s.indexOf('\n'); return i > 0 ? s.slice(0, i) : s }
function shortDate(s) { return s ? String(s).slice(0, 10) : '' }

// ── 盘中轮询: 5s 报价 / 15s 主力+图表 / 30s 板块+涨停原因; 全部 silent, 仅交易时段 ──
let timers = {}
function startTimers() {
  if (Object.keys(timers).length) return
  if (!isTradingTime()) return
  timers.quote = setInterval(() => tick(() => loadQuote(true)), 5000)
  timers.pankou = setInterval(() => tick(() => loadPankou(true)), 5000)
  timers.mainFlow = setInterval(() => tick(() => loadMainFlow(true)), 15000)
  timers.chart = setInterval(() => tick(refreshChartSilent), 15000)
  timers.board = setInterval(() => tick(() => loadBoards(true)), 30000)
  timers.limit = setInterval(() => tick(() => loadLimit(true)), 30000)
}
function stopTimers() { for (const k in timers) clearInterval(timers[k]); timers = {} }
function tick(fn) { if (!isTradingTime()) { stopTimers(); return } fn() }
async function refreshChartSilent() {
  if (view.value === 'trend') await loadTrend(true)
  else await loadKline(view.value, adjust.value, true)
  if (chart) renderSeries()
}
function onVisibility() {
  if (document.hidden) { stopTimers(); return }
  if (!isTradingTime()) return
  startTimers()
  loadQuote(true); refreshChartSilent(); loadBoards(true); loadLimit(true); loadMainFlow(true); loadPankou(true)
}

// KeepAlive 组件首次挂载时 onMounted+onActivated 双触发 → 加载只在 onActivated 做(inited 分支)
let inited = false
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
})

// KeepAlive: 详情→资讯详情→返回时组件复用, 滚动位置保留; 切走停轮询, 切回恢复+静默刷新
onActivated(() => {
  if (!inited) {
    inited = true
    loadQuote(); loadChart(); loadBoards(); loadLimit(); loadMainFlow(); loadPankou(); loadGene(); loadBid(); loadInfo()
  } else if (isTradingTime()) {
    loadQuote(true); refreshChartSilent(); loadBoards(true); loadLimit(true); loadMainFlow(true); loadPankou(true)
    if (view.value === 'trend') loadTrend(true)
    if (chart) renderSeries()
  }
  startTimers()
})
onDeactivated(() => { stopTimers() })

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  document.removeEventListener('visibilitychange', onVisibility)
  stopTimers()
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
          <span class="sd-price" :style="{ color: crossInfo ? (crossInfo.chg >= 0 ? '#e74c3c' : '#27ae60') : upColor }">{{ fmt(crossInfo ? crossInfo.close : quote.price) }}</span>
          <span class="sd-chg" :style="{ color: crossInfo ? (crossInfo.chg >= 0 ? '#e74c3c' : '#27ae60') : upColor }">{{ pct(crossInfo ? crossInfo.chgPct : quote.changePct) }}</span>
          <span class="sd-chg" :style="{ color: crossInfo ? (crossInfo.chg >= 0 ? '#e74c3c' : '#27ae60') : upColor }">{{ crossInfo ? (crossInfo.chg >= 0 ? '+' : '') + fmt(crossInfo.chg) : (quote.change !== undefined && quote.change !== null ? (quote.change >= 0 ? '+' : '') + fmt(quote.change) : '') }}</span>
        </div>
        <div class="sd-grid">
          <!-- L2 短线核心(正常字号): 开盘四价 + 量能四指标; crossInfo=光标处 K线数据(切日期时联动), null=当日实时 -->
          <div class="sd-cell"><span class="lbl">今开</span><span class="val">{{ fmt(crossInfo ? crossInfo.open : quote.open) }}</span></div>
          <div class="sd-cell"><span class="lbl">最高</span><span class="val">{{ fmt(crossInfo ? crossInfo.high : quote.high) }}</span></div>
          <div class="sd-cell"><span class="lbl">最低</span><span class="val">{{ fmt(crossInfo ? crossInfo.low : quote.low) }}</span></div>
          <div class="sd-cell"><span class="lbl">昨收</span><span class="val">{{ fmt(crossInfo ? crossInfo.prevClose : quote.prevClose) }}</span></div>
          <div class="sd-cell"><span class="lbl">量比</span><span class="val">{{ crossInfo ? '—' : fmt(quote.volumeRatio) }}</span></div>
          <div class="sd-cell"><span class="lbl">换手率</span><span class="val">{{ crossInfo ? '—' : (quote.turnover !== undefined && quote.turnover !== null ? fmt(quote.turnover) + '%' : '—') }}</span></div>
          <div class="sd-cell"><span class="lbl">成交额</span><span class="val">{{ wan(crossInfo ? crossInfo.amount : quote.amount) }}</span></div>
          <div class="sd-cell">
            <span class="lbl">主力净流入</span>
            <span class="val" :style="{ color: crossInfo || quote.mainFlowYi === null ? '#999' : (quote.mainFlowYi >= 0 ? '#e74c3c' : '#27ae60') }">{{ crossInfo ? '—' : mainFlowText }}</span>
          </div>
        </div>
        <!-- L3 背景参考(小字 .minor)折叠: 估值市值 + 涨跌停价/均价 -->
        <button class="sd-grid-more" @click="gridMore = !gridMore">更多指标 {{ gridMore ? '▴' : '▾' }}</button>
        <div v-if="gridMore" class="sd-grid">
          <div class="sd-cell minor"><span class="lbl">振幅</span><span class="val">{{ crossInfo ? '—' : (quote.amplitude !== undefined && quote.amplitude !== null ? fmt(quote.amplitude) + '%' : '—') }}</span></div>
          <div class="sd-cell minor"><span class="lbl">PE(TTM)</span><span class="val">{{ fmt(quote.pe) }}</span></div>
          <div class="sd-cell minor"><span class="lbl">PB</span><span class="val">{{ fmt(quote.pb) }}</span></div>
          <div class="sd-cell minor"><span class="lbl">总市值</span><span class="val">{{ wan(quote.totalCap) }}</span></div>
          <div class="sd-cell minor"><span class="lbl">流通市值</span><span class="val">{{ wan(quote.floatCap) }}</span></div>
          <div class="sd-cell minor"><span class="lbl">涨停价</span><span class="val">{{ fmt(quote.upPx) }}</span></div>
          <div class="sd-cell minor"><span class="lbl">跌停价</span><span class="val">{{ fmt(quote.downPx) }}</span></div>
          <div class="sd-cell minor"><span class="lbl">均价</span><span class="val">{{ fmt(quote.avgPx) }}</span></div>
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
      </div>
      <!-- 指标切换(K线视图): MA/BOLL/缠/波 常显, 复权按钮同行 -->
      <div v-if="isKline" class="sd-inds">
        <button :class="['sd-ibtn', { on: overlays.ma }]" @click="overlays.ma = !overlays.ma">MA</button>
        <button :class="['sd-ibtn', { on: overlays.boll }]" @click="overlays.boll = !overlays.boll">BOLL</button>
        <button :class="['sd-ibtn', 'chan', { on: chan }]" @click="chan = !chan">缠</button>
        <button :class="['sd-ibtn', 'wave', { on: wave }]" @click="wave = !wave">波</button>
        <span v-if="showAdjust" class="sd-isep"></span>
        <button v-if="showAdjust" :class="['sd-ibtn', { on: adjust === 'qfq' }]" @click="adjust = 'qfq'">前复权</button>
        <button v-if="showAdjust" :class="['sd-ibtn', { on: adjust === '' }]" @click="adjust = ''">不复权</button>
      </div>
      <div v-if="wave" class="sd-wave-note">{{ waveNote || '波浪:计算中…' }}</div>
      <!-- 图表 + 右盘口 flex 行(桌面盘口 180 / 移动 116) -->
      <div class="sd-chart-row">
        <div class="sd-chart-wrap">
          <div v-if="loading.chart && !kline.length && !trend.length" class="sd-loading">图表加载中…</div>
          <div v-else-if="!loading.chart && !kline.length && !trend.length" class="sd-error">
            {{ error || '图表数据加载失败' }}
            <button class="sd-retry" @click="loadChart">重试</button>
          </div>
          <div v-else ref="chartEl" class="sd-chart" :class="{ kline: isKline }" :style="{ height: chartH + 'px' }">
            <!-- 副图指标切换: 定位在副图(pane 2)区域左上角的下拉按钮; 分时视图同样可用 -->
            <select v-model="subInd" class="sd-subind-select" title="切换副图指标">
              <option v-for="s in subInds" :key="s.key" :value="s.key">{{ s.label }}</option>
            </select>
          </div>
        </div>
        <PankouPanel class="sd-pankou" :code="code" :quote="quote" />
      </div>
    </div>

    <!-- 涨停原因(仅当日涨停显示: 现价≥涨停价; 开盘啦 GetDayZhangTing) -->
    <div v-if="isLimitUp && limitReason" class="sd-limit">
      <div class="sd-limit-head">
        <strong class="sd-limit-title">📌 涨停原因</strong>
        <button v-if="limitReason.reason && limitReason.reason.length > 60" class="sd-limit-toggle" @click="limitMore = !limitMore">{{ limitMore ? '收起 ▴' : '更多 ▾' }}</button>
      </div>
      <p class="sd-limit-text">{{ limitMore ? limitReason.reason : firstLine(limitReason.reason) }}</p>
      <div v-if="limitReason.zsCodes && limitReason.zsCodes.length" class="sd-limit-codes">
        <span v-for="c in limitReason.zsCodes" :key="c" class="sd-mini-chip" @click="goBoard(c, boardNameById[c] || c)">{{ boardNameById[c] || c }}</span>
      </div>
    </div>

    <!-- 所属板块胶囊(开盘啦 GetFeaturedSection, 强度%红涨绿跌) -->
    <div v-if="boards && boards.length" class="sd-boards">
      <div class="sd-boards-head">
        <strong class="sd-boards-title">所属板块 {{ boards.length }} 个</strong>
        <button v-if="boards.length > 12" class="sd-boards-toggle" @click="boardsMore = !boardsMore">{{ boardsMore ? '收起 ▴' : '全部 ▾' }}</button>
      </div>
      <div class="sd-chips">
        <div v-for="b in shownBoards" :key="b.code" class="sd-chip" :style="{ color: (typeof b.strength === 'number' ? b.strength : 0) >= 0 ? '#e74c3c' : '#27ae60' }" @click="goBoard(b.code, b.name)">
          <span class="sd-chip-name">{{ b.name }}</span>
          <span class="sd-chip-str">{{ typeof b.strength === 'number' ? (b.strength >= 0 ? '+' : '') + b.strength.toFixed(2) + '%' : '' }}</span>
        </div>
      </div>
    </div>
    <div v-else-if="boardsLoading" class="sd-boards-loading">板块加载中…</div>

    <!-- 功能卡区(2 列; Task 6 补全 4 列): 竞价分时 / 涨停基因 -->
    <div class="sd-cards">
      <BidAuctionCard :bid="bid" :prev-close="quote?.prevClose" />
      <LimitGeneCard :gene="gene" />
    </div>

    <!-- 底部 tab 区: 📰 资讯 | 🏢 基本面(默认收起, 点开才请求) -->
    <div class="sd-more">
      <div class="sd-more-tabs sd-tabs">
        <button :class="['sd-tab', { on: moreTab === 'info' }]" @click="toggleMore('info')">📰 资讯</button>
        <button :class="['sd-tab', { on: moreTab === 'f10' }]" @click="toggleMore('f10')">🏢 基本面</button>
      </div>

      <!-- 资讯: 新闻|研报|公告 + 正文展开 -->
      <div v-if="moreTab === 'info'" class="sd-more-body">
        <div class="sd-tabs sd-subtabs">
          <button v-for="t in infoTypes" :key="t.type" :class="['sd-tab', 'small', { on: infoType === t.type }]" @click="switchInfo(t.type)">{{ t.label }}</button>
        </div>
        <div v-if="infoLoading && !infoList.length" class="sd-tip">资讯加载中…</div>
        <div v-else-if="infoError && !infoList.length" class="sd-tip sd-tip-err">加载失败 <button class="sd-retry" @click="loadInfo(true)">重试</button></div>
        <ul v-else-if="infoList.length" class="sd-info-list">
          <li v-for="it in infoList" :key="it.iid" class="sd-info-item">
            <div class="sd-info-line" @click="goInfo(it)">
              <span class="sd-info-title">{{ it.title }}</span>
              <span class="sd-info-date">{{ shortDate(it.date) }}</span>
              <span class="sd-info-go">›</span>
            </div>
          </li>
        </ul>
        <button v-if="infoHasMore && infoList.length" class="sd-more-btn" @click="loadMoreInfo">加载更多</button>
      </div>

      <!-- 基本面: 公司|财务|股东|估值 -->
      <div v-else-if="moreTab === 'f10'" class="sd-more-body">
        <div class="sd-tabs sd-subtabs">
          <button v-for="t in f10Types" :key="t.key" :class="['sd-tab', 'small', { on: f10Type === t.key }]" @click="switchF10(t.key)">{{ t.label }}</button>
        </div>
        <div v-if="f10Loading && !f10Company && !f10Finance && !f10Holders && !f10Valuation.length" class="sd-tip">加载中…</div>
        <div v-else-if="f10Error && !f10Company && !f10Finance && !f10Holders && !f10Valuation.length" class="sd-tip sd-tip-err">{{ f10Error }} <button class="sd-retry" @click="loadF10">重试</button></div>
        <template v-else-if="f10Type === 'company' && f10Company">
          <div class="sd-f10">
            <dl class="sd-f10-dl">
              <div><dt>公司全称</dt><dd>{{ f10Company.info.name }}</dd></div>
              <div><dt>董事长</dt><dd>{{ f10Company.info.chairman }}</dd></div>
              <div><dt>董秘</dt><dd>{{ f10Company.info.secretary }}</dd></div>
              <div><dt>主营</dt><dd>{{ f10Company.info.mainSale }}</dd></div>
              <div><dt>控股股东</dt><dd>{{ f10Company.info.troHold }}</dd></div>
              <div><dt>实控人</dt><dd>{{ f10Company.info.actHold }}</dd></div>
              <div><dt>地址</dt><dd>{{ f10Company.info.address }}</dd></div>
            </dl>
            <div v-if="f10CpRows.rows.length" class="sd-f10-sec">主营构成 <span class="sd-f10-date">{{ f10CpRows.date }}</span></div>
            <table v-if="f10CpRows.rows.length" class="sd-f10-tb">
              <tr><th>产品</th><th>收入</th><th>占比</th><th>毛利率</th></tr>
              <tr v-for="(r, i) in f10CpRows.rows" :key="i">
                <td>{{ r.name }}</td>
                <td>{{ r.amount }}</td>
                <td>{{ r.ratio }}</td>
                <td>{{ r.margin }}</td>
              </tr>
            </table>
          </div>
        </template>
        <template v-else-if="f10Type === 'finance' && f10FinanceRows.length">
          <table class="sd-f10-tb">
            <tr><th>指标</th><th>{{ f10FinanceRows[0].yname }}</th><th>同比</th><th>{{ f10FinanceRows[0].qname }}</th></tr>
            <tr v-for="r in f10FinanceRows" :key="r.label">
              <td>{{ r.label }}</td>
              <td>{{ fmtFin(r.yval, r.label) }}</td>
              <td class="sd-num">{{ fmtTb(r.ytb) }}</td>
              <td>{{ fmtFin(r.qval, r.label) }}</td>
            </tr>
          </table>
        </template>
        <template v-else-if="f10Type === 'holders' && f10Holders">
          <div class="sd-f10">
            <div class="sd-f10-sec">股东户数 <span class="sd-f10-date">{{ f10Holders.counts[0].day }}</span>：{{ f10Holders.counts[0].count }} 户
              <span class="sd-f10-change">{{ f10Holders.countChange }}</span>
            </div>
            <div class="sd-f10-sec">十大股东 <span class="sd-f10-date">{{ f10Holders.date }}</span></div>
            <table class="sd-f10-tb">
              <tr><th>股东</th><th>比例</th><th>持股(万)</th><th>增减</th></tr>
              <tr v-for="(h, i) in f10Holders.top10" :key="i">
                <td>{{ h.name }}</td>
                <td>{{ h.ratio }}</td>
                <td>{{ h.shares }}</td>
                <td>{{ h.change }}</td>
              </tr>
            </table>
          </div>
        </template>
        <template v-else-if="f10Type === 'valuation' && f10Valuation.length">
          <div class="sd-f10">
            <div class="sd-f10-sec">PE(TTM) 近一年走势</div>
            <svg viewBox="0 0 340 120" preserveAspectRatio="none" class="sd-svg">
              <line v-for="y in [30, 60, 90]" :key="y" x1="0" :x2="340" :y1="y" :y2="y" stroke="#f0f0f0" stroke-width="1"/>
              <polyline :points="pePoints" fill="none" stroke="#2980b9" stroke-width="1.5"/>
            </svg>
            <div class="sd-svg-scale"><span>最新 {{ peStats.last }}</span><span>高 {{ peStats.max }} / 低 {{ peStats.min }}</span></div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 全宽页面: main-content.stock-page 已去左右留白, 内部区块自行控制 padding */
.sd-page { padding: 4px 0 12px; font-variant-numeric: tabular-nums; }  /* 数字等宽对齐(spec §7.5) */
/* 基本信息: 总高控制在约 1/4 屏(用户要求) — 名称行+价格行+16格两段式紧凑排布 */
.sd-head { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; padding: 0 12px; flex-wrap: wrap; }
.sd-info { margin-bottom: 8px; padding: 0 12px; }
.sd-chart-block { margin-bottom: 14px; }
/* 图表 + 右盘口 flex 行: 盘口固定宽, 图表区自适应; 内边距与图表头部对齐 */
.sd-chart-row { display: flex; align-items: stretch; gap: 10px; padding: 0 14px; }
.sd-chart-wrap { flex: 1; min-width: 0; }
.sd-pankou { flex: none; width: 180px; border-left: 1px solid #f0f0f0; padding-left: 10px; }
.sd-name { display: flex; align-items: baseline; gap: 6px; }
.sd-title { font-size: 16px; font-weight: 600; color: #111; }
.sd-code { color: #999; font-size: 11px; }
.sd-h5 { margin-left: auto; border: 1px solid #2980b9; color: #2980b9; background: #fff; font-size: 11px; padding: 3px 9px; border-radius: 7px; cursor: pointer; }

.sd-price-row { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
.sd-price { font-size: 20px; font-weight: 700; }
.sd-chg { font-size: 13px; font-weight: 600; }

.sd-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 2px 10px; }
.sd-cell { display: flex; flex-direction: column; gap: 1px; }
.sd-cell .lbl { font-size: 9px; color: #999; }
.sd-cell .val { font-size: 11px; color: #333; font-weight: 500; }
.sd-grid-more { border: 1px solid #eceff3; background: #fff; color: #2980b9; font-size: 11px; padding: 4px 0; border-radius: 6px; cursor: pointer; margin-top: 6px; width: 100%; }

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

/* touch-action: pan-y !important — 覆盖 lightweight-charts 内联 none: 垂直手势交还页面滚动(上下滑=滚页面), 水平拖动/双指缩放仍归图表
   ⚠️ 外层高度由 inline style 绑定 chartH(与 createChart/applyOptions 高度一致), 保证内层 canvas 不溢出遮挡下方内容 */
.sd-chart { position: relative; width: 100%; touch-action: pan-y !important; }
/* 副图指标下拉: 定位在副图(pane 2)区域左上角, 半透明底避免挡K线 */
.sd-subind-select {
  position: absolute;
  left: 8px;
  bottom: 30px;
  z-index: 9;
  font-size: 11px;
  color: #555;
  background: rgba(255, 255, 255, .92);
  border: 1px solid #e0e3e8;
  border-radius: 6px;
  padding: 2px 4px;
  max-width: 96px;
}
.sd-loading { padding: 40px 0; text-align: center; color: #999; font-size: 13px; }
.sd-error { padding: 40px 0; text-align: center; color: #c0392b; font-size: 13px; }
.sd-retry { margin-left: 8px; border: 1px solid #2980b9; background: #fff; color: #2980b9; font-size: 12px; padding: 4px 12px; border-radius: 8px; cursor: pointer; }

/* L3 背景参考格: 小字弱化(估值市值/涨跌停价/均价) */
.sd-cell.minor .lbl { font-size: 9px; }
.sd-cell.minor .val { font-size: 10px; color: #666; }

/* ── 涨停原因块 ── */
.sd-limit { margin: 0 14px 14px; background: #fff7f5; border: 1px solid #f5dcd5; border-radius: 10px; padding: 10px 12px; }
.sd-limit-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.sd-limit-title { font-size: 13px; color: #c0392b; }
.sd-limit-toggle { margin-left: auto; border: none; background: none; color: #2980b9; font-size: 12px; cursor: pointer; flex: none; }
.sd-limit-text { font-size: 12px; color: #555; line-height: 1.7; margin: 0 0 8px; }
.sd-limit-codes { display: flex; flex-wrap: wrap; gap: 6px; }
.sd-mini-chip { background: #fdecea; color: #c0392b; font-size: 11px; padding: 2px 8px; border-radius: 20px; cursor: pointer; }

/* ── 所属板块胶囊 ── */
.sd-boards { margin: 0 14px 14px; }
.sd-boards-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.sd-boards-title { font-size: 13px; color: #333; }
.sd-boards-toggle { margin-left: auto; border: none; background: none; color: #2980b9; font-size: 12px; cursor: pointer; flex: none; }
.sd-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.sd-chip { display: flex; align-items: center; gap: 5px; background: #f5f7fa; border: 1px solid #eceff3; border-radius: 16px; padding: 4px 10px; cursor: pointer; }
.sd-chip-name { font-size: 12px; }
.sd-chip-str { font-size: 11px; font-weight: 600; }
.sd-boards-loading { padding: 10px 0; text-align: center; color: #999; font-size: 12px; }

/* 功能卡区: 桌面多列 / 移动堆叠; 卡内样式见 src/styles/cards.css(.feat-card) */
.sd-cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 0 14px 14px; }

/* ── 底部 资讯|基本面 tab 区 ── */
.sd-more { margin: 0 14px; }
.sd-more-tabs { padding: 8px 0; }
.sd-subtabs { margin-bottom: 10px; }
/* min-height: tab/子tab 切换时内容高度突变导致页面跳动, 保底高度吸收加载态 */
.sd-more-body { padding: 0 2px 10px; min-height: 200px; }
.sd-tip { padding: 20px 0; text-align: center; color: #999; font-size: 12px; }
.sd-tip-err { color: #c0392b; }
.sd-info-list { list-style: none; margin: 0; padding: 0; }
.sd-info-item { border-bottom: 1px solid #f0f0f0; }
.sd-info-item:last-child { border-bottom: none; }
.sd-info-line { display: flex; align-items: baseline; gap: 8px; padding: 9px 2px; cursor: pointer; }
.sd-info-title { font-size: 13px; color: #333; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sd-info-date { font-size: 11px; color: #999; flex: none; }
.sd-info-go { color: #ccc; font-size: 14px; flex: none; }
.sd-more-btn { display: block; width: 100%; border: 1px solid #2980b9; background: #fff; color: #2980b9; font-size: 12px; padding: 7px 0; border-radius: 8px; cursor: pointer; margin-top: 10px; }

/* ── 基本面 ── */
.sd-f10 { }
.sd-f10-dl { margin: 0; }
.sd-f10-dl > div { display: flex; gap: 8px; padding: 6px 0; border-bottom: 1px solid #f5f5f5; font-size: 12px; }
.sd-f10-dl dt { color: #999; flex: none; width: 64px; }
.sd-f10-dl dd { margin: 0; color: #333; flex: 1; }
.sd-f10-sec { font-size: 12px; color: #333; font-weight: 600; margin: 12px 0 6px; }
.sd-f10-date { color: #999; font-weight: 400; }
.sd-f10-change { color: #c0392b; font-weight: 400; }
.sd-f10-tb { width: 100%; border-collapse: collapse; font-size: 12px; }
.sd-f10-tb th { text-align: left; color: #999; font-weight: 400; padding: 5px 4px; border-bottom: 1px solid #eceff3; white-space: nowrap; }
.sd-f10-tb td { padding: 6px 4px; color: #333; border-bottom: 1px solid #f5f5f5; }
.sd-f10-tb td.sd-num { color: #555; }
.sd-svg { width: 100%; height: 120px; display: block; }
.sd-svg-scale { display: flex; justify-content: space-between; font-size: 11px; color: #999; margin-top: 4px; }

@media (max-width: 480px) {
  .sd-limit, .sd-boards, .sd-more { margin-left: 10px; margin-right: 10px; }
  .sd-chart-row { padding: 0 10px; gap: 8px; }
  .sd-pankou { width: 116px; padding-left: 8px; }
  .sd-cards { grid-template-columns: 1fr; margin: 0 10px 14px; }
}
/* 桌面端: 内层留白与其它页 main-content 水平 padding(28px) 对齐 */
@media (min-width: 768px) {
  .sd-page { padding: 8px 0 20px; }
  .sd-head, .sd-chart-head, .sd-inds, .sd-wave-note, .sd-info { padding-left: 28px; padding-right: 28px; }
  .sd-limit, .sd-boards, .sd-more { margin-left: 28px; margin-right: 28px; }
  .sd-info { margin-bottom: 18px; }
  .sd-chart-row { padding: 0 28px; }
  .sd-cards { margin: 0 28px 14px; }
}
</style>
