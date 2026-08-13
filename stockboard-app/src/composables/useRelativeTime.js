export function useRelativeTime() {
  function relativeTime(dateStr) {
    if (!dateStr) return ''
    const d = new Date(dateStr.replace(' ', 'T') + ':00')
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
