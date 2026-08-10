import { describe, it, expect } from 'vitest'
import { fmtWan, fmtVol, fmtPct } from '../fmt.js'

describe('fmtWan 金额格式化', () => {
  it('亿/万带单位, 小量千分位', () => {
    expect(fmtWan(186000000)).toBe('1.86亿')
    expect(fmtWan(12345)).toBe('1.2万')
    expect(fmtWan(800)).toBe('800')
  })
  it('非法值返回 —', () => {
    expect(fmtWan(null)).toBe('—')
    expect(fmtWan(undefined)).toBe('—')
  })
})

describe('fmtVol 手数/股数格式化', () => {
  it('亿/万带单位, 小量取整', () => {
    expect(fmtVol(2e8)).toBe('2.00亿')
    expect(fmtVol(12345)).toBe('1.2万')
    expect(fmtVol(800)).toBe('800')
  })
})

describe('fmtPct 百分比', () => {
  it('正负号保留两位', () => {
    expect(fmtPct(10.02)).toBe('+10.02%')
    expect(fmtPct(-3.5)).toBe('-3.50%')
  })
  it('非法值返回 —', () => {
    expect(fmtPct(null)).toBe('—')
  })
})
