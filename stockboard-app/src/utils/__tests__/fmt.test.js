import { describe, it, expect } from 'vitest'
import { fmtWan, fmtVol, fmtPct, fmtYiShort } from '../fmt.js'

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

  // 2026-11-17 复核 P0-3: NaN 也是 typeof 'number'，曾会渲染成 "+NaN%"
  it('NaN / ±Infinity 返回 — 而不是 +NaN%', () => {
    expect(fmtPct(NaN)).toBe('—')
    expect(fmtPct(Infinity)).toBe('—')
    expect(fmtPct(-Infinity)).toBe('—')
    expect(fmtPct(undefined)).toBe('—')
  })

  it('真值 0 是有效读数', () => {
    expect(fmtPct(0)).toBe('+0.00%')
  })
})

// 2026-11-17 复核 P0-3: 原 MarketDetail 内联 yiTxt 用 `!v ? '0'`，把缺失显示成 0
describe('fmtYiShort 金额亿/万简写', () => {
  it('亿/万/整数三档', () => {
    expect(fmtYiShort(1.5e8)).toBe('1.5亿')
    expect(fmtYiShort(34000)).toBe('3万')
    expect(fmtYiShort(8800)).toBe('8800')
  })

  it('缺失一律 — ，绝不用 0 冒充', () => {
    expect(fmtYiShort(null)).toBe('—')
    expect(fmtYiShort(undefined)).toBe('—')
    expect(fmtYiShort(NaN)).toBe('—')
    expect(fmtYiShort(Infinity)).toBe('—')
    expect(fmtYiShort('123')).toBe('—')   // 字符串不是合法读数
  })

  it('真值 0 显示 0(竞价封单合计为 0 是真实读数)', () => {
    expect(fmtYiShort(0)).toBe('0')
  })
})
