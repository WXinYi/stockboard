export function useRelativeTime() {
  function relativeTime(dateStr) {
    if (!dateStr) return ''
    const raw = String(dateStr).trim().replace(' ', 'T')
    const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(raw)
    const hasSeconds = /\d{2}:\d{2}:\d{2}/.test(raw)
    const d = new Date(hasZone || hasSeconds ? raw : `${raw}:00`)
    if (isNaN(d.getTime())) return dateStr
    const now = Date.now()
    const diff = now - d.getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return '刚刚'
    if (mins < 60) return `${mins}分钟前`
    const hours = Math.floor(mins / 60)
    if (hours < 24) return `${hours}小时前`
    const days = Math.floor(hours / 24)
    if (days < 30) return `${days}天前`
    return dateStr.slice(0, 16)
  }
  return { relativeTime }
}
