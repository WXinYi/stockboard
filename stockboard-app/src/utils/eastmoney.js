// 东方财富 secid 市场标识: 0=深市 1=沪市 2=北交所 116=港股
export function emMarket(code) {
  if (!code) return '0'
  if (/^\d{5}$/.test(code)) return '116'
  if (/^(4|8|92)/.test(code)) return '2'
  if (/^[679]/.test(code)) return '1'
  return '0'
}
export function secid(code) {
  return `${emMarket(code)}.${code}`
}
