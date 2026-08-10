// 通用数值格式化(详情页卡片/表格): 金额/手数/百分比, 非法值统一 '—'
export function fmtWan(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const a = Math.abs(v)
  if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (a >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return v.toLocaleString()
}

export function fmtVol(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(1) + '万'
  return String(Math.round(v))
}

export function fmtPct(v, digits = 2) {
  return typeof v === 'number' ? (v >= 0 ? '+' : '') + v.toFixed(digits) + '%' : '—'
}
