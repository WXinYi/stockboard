import { describe, it, expect } from 'vitest'
import { isTradingDay, COVERAGE_YEAR } from '../tradingCalendar.js'

describe('交易日历(上交所2026休市安排)', () => {
  it('周末休市', () => {
    expect(isTradingDay('2026-09-12')).toBe(false)  // 周六
    expect(isTradingDay('2026-09-13')).toBe(false)  // 周日
    expect(isTradingDay('2026-10-10')).toBe(false)  // 周六(通知中列明的周末休市)
  })
  it('法定节假日(周一~周五)休市', () => {
    expect(isTradingDay('2026-09-25')).toBe(false)  // 中秋(周五)
    expect(isTradingDay('2026-10-01')).toBe(false)  // 国庆(周四)
    expect(isTradingDay('2026-10-07')).toBe(false)  // 国庆(周三)
    expect(isTradingDay('2026-02-23')).toBe(false)  // 春节末尾(周一)
    expect(isTradingDay('2026-05-05')).toBe(false)  // 劳动节(周二)
  })
  it('正常交易日', () => {
    expect(isTradingDay('2026-09-11')).toBe(true)   // 周五
    expect(isTradingDay('2026-09-28')).toBe(true)   // 中秋后首个开市日(周一)
    expect(isTradingDay('2026-10-08')).toBe(true)   // 国庆后首个开市日(周四)
  })
  it('未收录年份只按周末近似(不猜节假日)', () => {
    expect(COVERAGE_YEAR).toBe(2026)
    expect(isTradingDay('2027-01-01')).toBe(true)   // 2027表未录入 → 周五近似为交易日, 需年底更新表
  })
})
