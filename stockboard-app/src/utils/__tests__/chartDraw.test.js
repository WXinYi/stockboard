import { describe, it, expect } from 'vitest'
import { panelRects, priceToY, klineWindow, idxToX, timeTicks, priceTicksTrend, priceTicks, trendMinute, trendX } from '../chartDraw.js'

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

describe('trendMinute / trendX', () => {
  const t = (h, mm) => Date.UTC(2026, 7, 10, h, mm) / 1000   // UTC-naive 分时时间戳
  it('开盘 09:30 → 0, 上午末 11:30 → 120', () => {
    expect(trendMinute(t(9, 30))).toBe(0)
    expect(trendMinute(t(10, 30))).toBe(60)
    expect(trendMinute(t(11, 30))).toBe(120)
  })
  it('午休 11:31~12:59 → -1(跳过, 不占横轴)', () => {
    expect(trendMinute(t(11, 31))).toBe(-1)
    expect(trendMinute(t(12, 0))).toBe(-1)
    expect(trendMinute(t(12, 59))).toBe(-1)
  })
  it('下午 13:00 → 120(紧接 11:30), 收盘 15:00 → 240', () => {
    expect(trendMinute(t(13, 0))).toBe(120)
    expect(trendMinute(t(14, 0))).toBe(180)
    expect(trendMinute(t(15, 0))).toBe(240)
  })
  it('盘前/盘后/非法输入 → -1', () => {
    expect(trendMinute(t(9, 29))).toBe(-1)
    expect(trendMinute(t(15, 1))).toBe(-1)
    expect(trendMinute(NaN)).toBe(-1)
    expect(trendMinute('x')).toBe(-1)
    expect(trendMinute(-5)).toBe(-1)
  })
  it('trendX: 交易分钟线性映射, 午休/非法 → -1', () => {
    expect(trendX(t(9, 30), 400)).toBe(0)
    expect(trendX(t(11, 30), 400)).toBe(200)
    expect(trendX(t(13, 0), 400)).toBe(200)     // 午休后紧接上午末
    expect(trendX(t(15, 0), 400)).toBe(400)
    expect(trendX(t(12, 0), 400)).toBe(-1)
  })
})

describe('timeTicks', () => {
  it('分时固定 5 刻度, 按交易分钟比例(午休不占位)', () => {
    const ticks = timeTicks([], 400, true)
    expect(ticks.map(t => t.label)).toEqual(['09:30', '10:30', '11:30/13:00', '14:00', '15:00'])
    expect(ticks.map(t => t.x)).toEqual([0, 100, 200, 300, 400])  // 09:30 在左缘, 15:00 在右缘
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
