import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { parsePankouTencent, calcWeiBi, fmtHand } from '../pankou.js'

// 真实返回 fixture(腾讯 qt.gtimg.cn GBK→UTF8, 字段 88 个)
const RAW = readFileSync(fileURLToPath(new URL('./fixtures/pankou-sz000938.txt', import.meta.url)), 'utf-8')

describe('parsePankouTencent', () => {
  it('解析五档价量', () => {
    const r = parsePankouTencent(RAW)
    expect(r).not.toBeNull()
    // 卖1在 sell[0], 买1在 buy[0](真实: 卖1=37.61, 买1=37.59)
    expect(r.sell[0].px).toBe(37.61)
    expect(r.sell[0].vol).toBe(30)
    expect(r.sell[4].px).toBe(37.65)
    expect(r.buy[0].px).toBe(37.59)
    expect(r.buy[0].vol).toBe(29)
    expect(r.buy[4].px).toBe(37.55)
  })
  it('解析现价/昨收/涨跌停/量比/内外盘', () => {
    const r = parsePankouTencent(RAW)
    expect(r.price).toBe(37.59)
    expect(r.prevClose).toBe(38.00)
    expect(r.upPx).toBe(41.80)
    expect(r.downPx).toBe(34.20)
    expect(r.turnover).toBe('5.73')
    expect(r.volumeRatio).toBe(0.59)
    expect(r.outer).toBe(715551)
    expect(r.inner).toBe(921309)
  })
  it('字段不足返回 null', () => {
    expect(parsePankouTencent('short~string')).toBeNull()
    expect(parsePankouTencent(null)).toBeNull()
    expect(parsePankouTencent(undefined)).toBeNull()
  })
})

describe('calcWeiBi 委比委差', () => {
  it('委差=买量-卖量, 委比=委差/总量', () => {
    const r = calcWeiBi({ buy: [{ px: 10, vol: 100 }, { px: 9, vol: 50 }], sell: [{ px: 11, vol: 80 }, { px: 12, vol: 20 }] })
    expect(r.weiCha).toBe(50)
    expect(r.weiBi).toBeCloseTo(20)
  })
  it('无挂单/空入参返回 null', () => {
    expect(calcWeiBi(null).weiBi).toBeNull()
    expect(calcWeiBi({ buy: [], sell: [] }).weiBi).toBeNull()
  })
})

describe('fmtHand 手数格式化', () => {
  it('万/亿带单位, 小量原样', () => {
    expect(fmtHand(12345)).toBe('1.2万')
    expect(fmtHand(800)).toBe('800')
    expect(fmtHand(2e8)).toBe('2.0亿')
  })
})
