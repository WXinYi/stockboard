import { describe, it, expect } from 'vitest'
import { isAStock, isHKStock, onlyAStock } from '../stockMarket.js'

describe('股票市场过滤（共识页只看 A 股，2026-11-16）', () => {
  it('按代码长度精确区分 A 股与港股', () => {
    // 沪深/创业板/科创板/北交所/可转债/ETF 全是 6 位
    for (const code of ['600715', '002086', '300502', '688585', '920045', '123290', '513050']) {
      expect(isAStock(code)).toBe(true)
      expect(isHKStock(code)).toBe(false)
    }
    // 港股全是 5 位
    for (const code of ['01810', '03690', '09988', '00100']) {
      expect(isHKStock(code)).toBe(true)
      expect(isAStock(code)).toBe(false)
    }
  })

  it('空值/非数字代码不误判为 A 股', () => {
    for (const code of ['', null, undefined, 'ABC', '01810.HK', '600715.SH']) {
      expect(isAStock(code)).toBe(false)
    }
  })

  it('onlyAStock 滤掉港股、保留顺序、不影响原数组', () => {
    const input = [
      { code: '01810', name: '小米集团' },
      { code: '002086', name: '东方海洋' },
      { code: '03690', name: '美团' },
      { code: '600715', name: '文投控股' },
    ]
    const out = onlyAStock(input)
    expect(out.map(s => s.name)).toEqual(['东方海洋', '文投控股'])
    expect(input).toHaveLength(4)   // 不改原数组
  })

  it('非数组输入安全返回空数组', () => {
    expect(onlyAStock(null)).toEqual([])
    expect(onlyAStock(undefined)).toEqual([])
  })
})
