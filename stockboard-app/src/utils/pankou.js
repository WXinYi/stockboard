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

// 委比/委差: 委差 = Σ买量 - Σ卖量; 委比 = 委差 / 总挂单量 × 100(无量返回 null)
export function calcWeiBi(pk) {
  if (!pk) return { weiCha: 0, weiBi: null }
  const bv = (pk.buy || []).reduce((s, b) => s + (b.vol || 0), 0)
  const sv = (pk.sell || []).reduce((s, b) => s + (b.vol || 0), 0)
  const weiCha = bv - sv
  const total = bv + sv
  return { weiCha, weiBi: total === 0 ? null : +(weiCha / total * 100).toFixed(2) }
}

// 手数格式化(盘口挂单量): 亿/万带单位, 小量原样取整
export function fmtHand(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const a = Math.abs(v)
  if (a >= 1e8) return (v / 1e8).toFixed(1) + '亿'
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return String(Math.round(v))
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
