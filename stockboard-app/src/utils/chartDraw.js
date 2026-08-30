// 图表几何/刻度纯函数: 无 DOM 无状态, 供 StockChartCanvas.vue 调用, 可单测
// 坐标系: 内容区左上角 (0,0), y 向下增大

// 三区矩形 (sub=true → main:vol:sub=3:1:1; sub=false → main:vol=2.2:1)
// opts: { leftGutter, rightGutter, axisH } 轴带(左右轴带 + 底部时间轴带)
// 每区含 1px 边框(几何按直角), 区间距 2px; 底部预留 axisH 给时间刻度, 否则刻度画在画布外不可见
export function panelRects(w, h, sub, opts = {}) {
  const gap = 2
  const lg = opts.leftGutter || 0
  const rg = opts.rightGutter || 0
  const axisH = opts.axisH ?? 16
  const avail = Math.max(0, h - gap * 2 - axisH)
  const innerW = Math.max(0, w - lg - rg)
  let main, vol, subR = null
  if (sub) {
    const m = Math.floor(avail * 3 / 5)
    const v = Math.floor(avail * 1 / 5)
    main = { x: lg, y: 0, width: innerW, height: m }
    vol = { x: lg, y: m + gap, width: innerW, height: v }
    subR = { x: lg, y: m + gap + v + gap, width: innerW, height: avail - m - v }
  } else {
    const m = Math.floor(avail * 2.2 / 3.2)
    main = { x: lg, y: 0, width: innerW, height: m }
    vol = { x: lg, y: m + gap, width: innerW, height: avail - m }
  }
  return { main, vol, sub: subR }
}

// 价格 → y(min 在下缘, max 在上缘, 纯线性映射)
// 注意: 调用方需传入已含 4% 上下 padding 的 min/max, 此处按 rect 全高线性映射
export function priceToY(price, min, max, rect) {
  const t = (price - min) / ((max - min) || 1)
  return rect.y + (1 - t) * rect.height
}

// K线可见窗口 (offset=0 → 最新 count 根; offset 增大回看; clamp 到 [0, len-count])
export function klineWindow(kline, count, offset) {
  const len = kline.length
  if (!len) return { window: [], offset: 0 }
  const maxOff = Math.max(0, len - count)
  const off = Math.max(0, Math.min(offset || 0, maxOff))
  return { window: kline.slice(len - count - off, len - off), offset: off }
}

// 窗口内第 i 根 x 中心
export function idxToX(i, w, count) {
  return (i + 0.5) / count * w
}

// 分时: 时间戳(UTC-naive 秒) → 当日交易分钟序号 [0,240]; 午休 11:30~13:00 跳过; 非交易时段返回 -1
export function trendMinute(time) {
  if (typeof time !== 'number' || !(time > 0)) return -1
  const d = new Date(time * 1000)
  const m = d.getUTCHours() * 60 + d.getUTCMinutes()
  if (m >= 570 && m <= 690) return m - 570   // 09:30~11:30 → 0~120
  if (m >= 780 && m <= 900) return m - 660   // 13:00~15:00 → 120~240 (13:00 紧接 11:30)
  return -1
}

// 分时 x: 交易分钟 → 像素; 午休/盘前/盘后(无效) → -1
export function trendX(time, w) {
  const m = trendMinute(time)
  return m < 0 ? -1 : m / 240 * w
}

// 底部时间刻度: 分时固定 5 刻度(09:30~15:00, 按交易分钟比例); 其他均匀 4~6 个(含首末)
export function timeTicks(items, w, isIntraday) {
  if (isIntraday) {
    // 窄内容区(<150px, 移动端扣掉盘口+双轴带后)放下 11 字符的 '11:30/13:00' 会与相邻刻度叠字 → 缩写
    const mid = w < 150 ? '11:30' : '11:30/13:00'
    const labels = ['09:30', '10:30', mid, '14:00', '15:00']
    const mins = [0, 60, 120, 180, 240]
    return labels.map((label, i) => ({ x: mins[i] / 240 * w, label }))
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
    // 分钟级(时间戳偏小, 秒数 < 1e11)显示 HH:MM; 否则按日期 MM-DD
    if (t < 1e11) return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
    return `${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}`
  }
  // 日/周/月线 time 为 'YYYY-MM-DD' → 东财式全日期 'YYYYMMDD'
  return typeof t === 'string' ? String(t).replaceAll('-', '') : ''
}

// 分时双轴(参考东财: 左轴=价格, 右轴=涨跌幅度%): 5 等分, 上界涨停 upPx / 下界跌停 downPx, 昨收居中, 两轴同 y
// 返回 up/down = 实际采用的上下界(upPx/downPx 缺失时 ±10% fallback), 绘制端 yMin/yMax 须与此同源, 否则刻度错位/NaN
export function priceTicksTrend(upPx, downPx, prevClose, rect) {
  const fallback = prevClose * 0.1
  const up = typeof upPx === 'number' && upPx > 0 ? upPx : prevClose + fallback
  const down = typeof downPx === 'number' && downPx > 0 ? downPx : prevClose - fallback
  const pctSpan = (up - prevClose) / prevClose * 100
  const left = [], right = []
  for (let k = 0; k < 5; k++) {
    const f = 1 - k / 2            // k=0→+pctSpan, k=2→0, k=4→-pctSpan (全跨度 5 等分)
    const pct = pctSpan * f
    const price = prevClose * (1 + pct / 100)
    const y = rect.y + k / 4 * rect.height
    left.push({ y, label: round2(price) })
    right.push({ y, label: `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` })
  }
  return { left, right, up, down }
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
