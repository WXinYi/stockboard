import { describe, it, expect } from 'vitest'
import { panelRects, priceToY, klineWindow, idxToX, timeTicks, priceTicksTrend, priceTicks } from '../chartDraw.js'

describe('panelRects', () => {
  it('三区 3:1:1 高度比, 区间距 2', () => {
    const r = panelRects(400, 300, true, { leftGutter: 0, rightGutter: 0 })
    expect(r.main.height).toBeGreaterThan(r.vol.height * 2.8)
    expect(r.vol.height).toBeGreaterThan(r.sub.height * 0.9)
    expect(r.main.y).toBe(0)
    expect(r.vol.y - (r.main.y + r.main.height)).toBe(2)
    expect(r.sub.y - (r.vol.y + r.vol.height)).toBe(2)
  })
  it('无副图时主图:量=2.2:1, sub=null', () => {
    const r = panelRects(400, 200, false, { leftGutter: 0, rightGutter: 0 })
    expect(r.sub).toBeNull()
    expect(r.main.height / r.vol.height).toBeCloseTo(2.2, 1)
  })
  it('轴带从主区扣除', () => {
    const r = panelRects(400, 300, true, { leftGutter: 32, rightGutter: 40 })
    expect(r.main.x).toBe(32)
    expect(r.main.width).toBeLessThan(400 - 32)
  })
})

describe('priceToY', () => {
  it('min→下缘, max→上缘, 线性', () => {
    const rect = { y: 10, height: 100 }
    expect(priceToY(0, 0, 100, rect)).toBe(110)
    expect(priceToY(100, 0, 100, rect)).toBe(10)
    expect(priceToY(50, 0, 100, rect)).toBe(60)
  })
})

describe('klineWindow', () => {
  const kl = [1, 2, 3, 4, 5].map(v => ({ time: v, open: v, close: v, high: v, low: v, volume: v }))
  it('offset=0 取末尾 count 根', () => {
    const { window, offset } = klineWindow(kl, 3, 0)
    expect(window.map(k => k.time)).toEqual([3, 4, 5])
    expect(offset).toBe(0)
  })
  it('offset clamp 到最左', () => {
    const { window, offset } = klineWindow(kl, 3, 99)
    expect(offset).toBe(2)
    expect(window.map(k => k.time)).toEqual([1, 2, 3])
  })
  it('count≥len 全量', () => {
    const { window } = klineWindow(kl, 10, 0)
    expect(window.length).toBe(5)
  })
})

describe('idxToX', () => {
  it('首末位置', () => {
    expect(idxToX(0, 200, 5)).toBeCloseTo(20, 5)
    expect(idxToX(4, 200, 5)).toBeCloseTo(180, 5)
  })
})

describe('timeTicks', () => {
  it('分时固定 5 刻度', () => {
    const ticks = timeTicks([], 400, true)
    expect(ticks.map(t => t.label)).toEqual(['09:30', '10:30', '11:30/13:00', '14:00', '15:00'])
    expect(ticks.map(t => t.x)).toEqual([40, 120, 200, 280, 360])  // 5 等分中心
  })
})

describe('priceTicksTrend', () => {
  it('左右双轴 5 等分, 同 y, 幅度按涨跌停', () => {
    const { left, right } = priceTicksTrend(11, 9, 10, { y: 0, height: 100 })
    // 昨收 10, 涨停 11 (+10%), 跌停 9 (-10%)
    expect(left.map(t => t.label)).toEqual(['+10%', '+5%', '0%', '-5%', '-10%'])
    expect(right[0].label).toBe(11)      // 上界 = 涨停价
    expect(right[4].label).toBe(9)       // 下界 = 跌停价
    expect(left.map(t => t.y)).toEqual(right.map(t => t.y))
  })
})

describe('priceTicks (K线)', () => {
  it('5 等分含边界', () => {
    const ticks = priceTicks(10, 20, { y: 0, height: 100 })
    expect(ticks.length).toBe(5)
    expect(ticks[0].label).toBe(20)
    expect(ticks[4].label).toBe(10)
  })
})
