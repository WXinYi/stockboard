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
  // NaN 也是 typeof 'number'，必须显式挡掉，否则会渲染成 "+NaN%"
  return (typeof v === 'number' && isFinite(v)) ? (v >= 0 ? '+' : '') + v.toFixed(digits) + '%' : '—'
}

// 金额"亿/万"简写(原 MarketDetail 内联 yiTxt 抽出, 2026-11-17 复核 P0-3)。
// 注意与 MarketDetail 局部 fmtYi(龙虎榜净买额, 带 +/- 符号、固定两位亿) 用途不同, 故不复用其名。
// 关键点: 缺失(null/undefined/NaN/Infinity)返回 '—' 而不是 '0' —— 项目红线"缺失≠零";
// 真值 0 仍显示 '0'(竞价封单合计为 0 是真实读数)。
export function fmtYiShort(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  if (v >= 1e8) return (v / 1e8).toFixed(1) + '亿'
  if (v >= 1e4) return (v / 1e4).toFixed(0) + '万'
  return String(Math.round(v))
}
