// @vitest-environment happy-dom
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import StockChartCanvas from '../StockChartCanvas.vue'

// happy-dom 的 canvas.getContext 返回 null → 组件用 stub 的 2D context 跑绘制逻辑
function stubCtx() {
  const ctx = {
    setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(),
    lineTo: vi.fn(), closePath: vi.fn(), stroke: vi.fn(), fill: vi.fn(),
    fillRect: vi.fn(), strokeRect: vi.fn(), fillText: vi.fn(), setLineDash: vi.fn(),
    save: vi.fn(), restore: vi.fn(), arc: vi.fn(), measureText: () => ({ width: 10 }),
    canvas: { width: 0, height: 0 },
  }
  return ctx
}

function mockCanvas(wrap) {
  const proto = wrap.vm.$el.querySelector('canvas')
  vi.spyOn(proto, 'getContext').mockReturnValue(stubCtx())
  // clientWidth/Height 供 resize 用
  Object.defineProperty(proto, 'clientWidth', { value: 400, configurable: true })
  Object.defineProperty(proto, 'clientHeight', { value: 300, configurable: true })
  return proto
}

describe('StockChartCanvas', () => {
  it('分时 view 挂载不抛错, 且 canvas 存在', () => {
    const trend = [
      { time: 1723000000 + 60, price: 10, vol: 100, amount: 100000 },
      { time: 1723000000 + 120, price: 10.1, vol: 200, amount: 200000 },
    ]
    const wrapper = mount(StockChartCanvas, {
      props: {
        view: 'trend', kline: [], trend,
        quote: { prevClose: 10, upPx: 11, downPx: 9, price: 10.05 },
        overlays: { ma: false, boll: false }, chan: false, wave: false,
        subInd: 'none', indCache: null,
      },
      attachTo: document.body,
    })
    mockCanvas(wrapper)
    expect(wrapper.find('canvas').exists()).toBe(true)
    wrapper.unmount()
  })

  it('K线 view 挂载不抛错(含缠论+波浪开启)', () => {
    const kline = [
      { time: '2026-08-07', open: 10, close: 10.2, high: 10.4, low: 9.8, volume: 5000 },
      { time: '2026-08-08', open: 10.2, close: 9.9, high: 10.3, low: 9.7, volume: 6000 },
    ]
    const wrapper = mount(StockChartCanvas, {
      props: {
        view: 'day', kline, trend: [],
        quote: { prevClose: 10, upPx: 11, downPx: 9, price: 9.9 },
        overlays: { ma: true, boll: true }, chan: true, wave: true,
        subInd: 'macd',
        indCache: {
          ma: { 5: [10], 10: [10], 20: [10], 60: [10] },
          boll: { up: [10.5], mid: [10], lo: [9.5] },
          volma: { 5: [5000], 10: [5000] },
          macd: { dif: [0], dea: [0], hist: [0] },
          kdj: { k: [50], d: [50], j: [50] },
          rsi: { 6: [50], 12: [50], 24: [50] },
          wr: { 10: [50], 6: [50] },
          fractals: [], bis: [], zhongshu: [], chanSignals: [], divergences: [],
          waves: { status: 'unknown', waves: [] },
        },
      },
      attachTo: document.body,
    })
    mockCanvas(wrapper)
    expect(wrapper.find('canvas').exists()).toBe(true)
    wrapper.unmount()
  })
})
