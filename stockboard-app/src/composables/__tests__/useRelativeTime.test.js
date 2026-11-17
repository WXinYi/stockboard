import { describe, it, expect } from 'vitest'
import { useRelativeTime } from '../useRelativeTime.js'
import { formatBeijingShort, snapshotLabel } from '../../utils/formatBeijing.js'

describe('时点格式化', () => {
  const { relativeTime } = useRelativeTime()

  it('ISO 带时区字符串不再拼出非法日期', () => {
    const value = '2026-11-17T11:30:54+08:00'
    expect(relativeTime(value)).toBeTruthy()
    expect(relativeTime(value)).not.toContain('Invalid')
  })

  it('formatBeijingShort 统一为 MM-DD HH:mm', () => {
    expect(formatBeijingShort('2026-11-17T11:30:54+08:00')).toBe('11-17 11:30')
    expect(formatBeijingShort('2026-11-17 15:15:00')).toBe('11-17 15:15')
    expect(formatBeijingShort('')).toBe('')
  })

  it('snapshotLabel 区分午间与收盘', () => {
    expect(snapshotLabel('1130')).toBe('11:30 午间快照')
    expect(snapshotLabel('close')).toBe('15:15 收盘真值')
    expect(snapshotLabel('')).toBe('')
  })
})
