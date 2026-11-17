export function formatBeijingShort(value) {
  const s = String(value || '')
  const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(s)
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : ''
}

export function snapshotLabel(snapshot) {
  if (snapshot === '1130') return '11:30 午间快照'
  if (snapshot === 'close') return '15:15 收盘真值'
  return ''
}
