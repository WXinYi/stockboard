import { describe, it, expect } from 'vitest'
import { freshnessText } from '../timeText.js'
describe('freshnessText 数据时效', () => {
  it('交易时段返回时间', () => {
    expect(freshnessText('15:00', true)).toBe('15:00')
  })
  it('非交易时段返回已收盘', () => {
    expect(freshnessText('15:00', false)).toBe('已收盘 15:00')
  })
  it('HHMMSS 时间戳转 HH:MM', () => {
    expect(freshnessText('145930', true)).toBe('14:59')
  })
  it('YYYYMMDDHHMMSS 时间戳取 HH:MM', () => {
    expect(freshnessText('20260810145930', true)).toBe('14:59')
  })
  it('空值返回空串', () => {
    expect(freshnessText('')).toBe('')
    expect(freshnessText(null)).toBe('')
  })
})
