import { describe, it, expect } from 'vitest'
import { pctHtml, rankCellHtml, drawdownColor } from '../format.js'

describe('pctHtml', () => {
  it('正数 → positive class + 加号', () => {
    expect(pctHtml(12.345)).toBe('<span class="positive">+12.35%</span>')
  })
  it('负数 → negative class', () => {
    expect(pctHtml(-5.67)).toBe('<span class="negative">-5.67%</span>')
  })
  it('字符串数字也可解析', () => {
    expect(pctHtml('3.14')).toBe('<span class="positive">+3.14%</span>')
  })
  it('0 → positive', () => {
    expect(pctHtml(0)).toBe('<span class="positive">+0.00%</span>')
  })
  it('NaN/undefined → 破折号(无 class)', () => {
    expect(pctHtml(NaN)).toBe('—')
    expect(pctHtml(undefined)).toBe('—')
    expect(pctHtml('abc')).toBe('—')
  })
})

describe('rankCellHtml', () => {
  it('收益类 key → 百分比着色', () => {
    expect(rankCellHtml('weekly_return', 5.5)).toBe('<span class="positive">+5.50%</span>')
    expect(rankCellHtml('total_return', -2)).toBe('<span class="negative">-2.00%</span>')
  })
  it('net_value → 原样 3 位小数, ≥1 红 <1 绿', () => {
    expect(rankCellHtml('net_value', 1.2346)).toBe('<span class="positive">1.235</span>')
    expect(rankCellHtml('net_value', 0.812)).toBe('<span class="negative">0.812</span>')
  })
  it('followers → 千分位(无颜色)', () => {
    expect(rankCellHtml('followers', 12345)).toBe('<span class="dim">12,345</span>')
  })
  it('非法 → 破折号', () => {
    expect(rankCellHtml('weekly_return', undefined)).toBe('—')
    expect(rankCellHtml('net_value', NaN)).toBe('—')
  })
})

describe('drawdownColor', () => {
  it('>15% 红(危险)', () => {
    expect(drawdownColor(20)).toBe('#c0392b')
  })
  it('5~15% 橙(警示)', () => {
    expect(drawdownColor(8)).toBe('#e67e22')
    expect(drawdownColor(15)).toBe('#e67e22')
  })
  it('0<v≤5% 绿(安全)', () => {
    expect(drawdownColor(3)).toBe('#27ae60')
    expect(drawdownColor(5)).toBe('#27ae60')
  })
  it('0/缺失 → 灰', () => {
    expect(drawdownColor(0)).toBe('#999')
    expect(drawdownColor(undefined)).toBe('#999')
    expect(drawdownColor(NaN)).toBe('#999')
  })
})
