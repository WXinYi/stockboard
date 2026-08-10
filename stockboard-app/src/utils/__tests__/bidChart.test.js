import { describe, it, expect } from 'vitest'
import { bidToPoints } from '../bidChart.js'

describe('bidToPoints 竞价序列→SVG点', () => {
  it('线点映射到 viewBox 坐标, 量柱红涨绿跌按累计量缩放', () => {
    const { line, bars } = bidToPoints(
      [
        { time: '0915', price: 101, side: '1', cumVol: 100 },
        { time: '0925', price: 99, side: '0', cumVol: 200 },
      ],
      100, 220, 64,
    )
    expect(line).toHaveLength(2)
    expect(line[0].x).toBe(0)
    expect(line[1].x).toBe(220)
    expect(bars).toHaveLength(2)
    expect(bars[0].color).toBe('#e74c3c')   // 101 > 昨收100 → 红
    expect(bars[1].color).toBe('#27ae60')   // 99 < 昨收100 → 绿
    expect(bars[1].h).toBeCloseTo(bars[0].h * 2)   // 累计量 200 vs 100 → 柱高翻倍
  })
  it('空序列返回空', () => {
    expect(bidToPoints([], 100)).toEqual({ line: [], bars: [] })
  })
})
