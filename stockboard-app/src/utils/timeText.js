// 行情时间戳 → "HH:MM" 展示; 非交易时段加"已收盘"前缀
export function freshnessText(raw, trading = false) {
  if (!raw) return ''
  const s = String(raw)
  let t
  if (s.length >= 12) t = `${s.slice(8, 10)}:${s.slice(10, 12)}`
  else if (s.length >= 6) t = `${s.slice(0, 2)}:${s.slice(2, 4)}`
  else if (/^\d{1,2}:\d{2}$/.test(s)) t = s
  else return s
  return trading ? t : `已收盘 ${t}`
}
