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

  it('K线带完整缠论+波浪数据挂载不抛错', () => {
    const kline = [
      { time: '2026-08-03', open: 10, close: 10.2, high: 10.5, low: 9.8, volume: 5000 },
      { time: '2026-08-04', open: 10.2, close: 9.9, high: 10.3, low: 9.7, volume: 6000 },
      { time: '2026-08-05', open: 9.9, close: 10.1, high: 10.6, low: 9.6, volume: 7000 },
      { time: '2026-08-06', open: 10.1, close: 10.4, high: 10.8, low: 9.9, volume: 8000 },
      { time: '2026-08-07', open: 10.4, close: 10.2, high: 10.7, low: 10.0, volume: 6000 },
      { time: '2026-08-08', open: 10.2, close: 9.8, high: 10.4, low: 9.5, volume: 9000 },
      { time: '2026-08-09', open: 9.8, close: 10.0, high: 10.3, low: 9.4, volume: 7000 },
    ]
    const indCache = {
      ma: { 5: [10, 10.1, 10.2, 10.3, 10.4], 10: [10, 10.1, 10.2, 10.3, 10.4], 20: [10, 10.1, 10.2, 10.3, 10.4], 60: [10, 10.1, 10.2, 10.3, 10.4] },
      boll: { up: [10.6, 10.6, 10.6, 10.6, 10.6], mid: [10.2, 10.2, 10.2, 10.2, 10.2], lo: [9.8, 9.8, 9.8, 9.8, 9.8] },
      volma: { 5: [5000, 6000, 7000, 8000, 9000], 10: [5000, 6000, 7000, 8000, 9000] },
      macd: { dif: [0.1, 0.2, 0.3, 0.2, 0.1], dea: [0.1, 0.2, 0.2, 0.2, 0.1], hist: [0, 0.1, 0.2, -0.1, 0] },
      kdj: { k: [50, 55, 60, 58, 52], d: [50, 52, 55, 56, 55], j: [50, 60, 70, 62, 50] },
      rsi: { 6: [45, 50, 55, 50, 45], 12: [48, 50, 52, 50, 48], 24: [49, 50, 51, 50, 49] },
      wr: { 10: [40, 50, 60, 50, 40], 6: [45, 50, 55, 50, 45] },
      fractals: [{ i: 2, type: 1 }, { i: 5, type: -1 }],
      bis: [
        { from: { i: 0, type: -1 }, to: { i: 2, type: 1 } },
        { from: { i: 2, type: 1 }, to: { i: 5, type: -1 } },
      ],
      zhongshu: [{ zg: 10.3, zd: 10.1, from: 1, to: 4 }],
      chanSignals: [{ i: 5, type: '2buy' }],
      divergences: [{ i: 2, type: 'top' }],
      waves: { status: 'ok', waves: [
        { i: 0, type: -1, label: '起' }, { i: 1, type: 1, label: '1' },
        { i: 2, type: -1, label: '2' }, { i: 3, type: 1, label: '3' },
        { i: 4, type: -1, label: '4' }, { i: 5, type: 1, label: '5' },
        { i: 6, type: -1, label: 'A' },
      ], dir: 1 },
    }
    const wrapper = mount(StockChartCanvas, {
      props: {
        view: 'day', kline, trend: [],
        quote: { prevClose: 10, upPx: 11, downPx: 9, price: 9.8 },
        overlays: { ma: true, boll: true }, chan: true, wave: true,
        subInd: 'rsi', indCache,
      },
      attachTo: document.body,
    })
    mockCanvas(wrapper)
    expect(wrapper.find('canvas').exists()).toBe(true)
    wrapper.unmount()
  })

  it('分时滑动触发 crossinfo emit(午休跳过, 按交易分钟反查)', async () => {
    // 240 个交易分钟点: 09:30~11:30 + 13:00~15:00 (UTC-naive 秒)
    const trend = []
    const day = [2026, 7, 10]
    for (let m = 570; m <= 690; m++) trend.push({ time: Date.UTC(...day, Math.floor(m / 60), m % 60) / 1000, price: 10 + (m - 570) / 240, vol: 100, amount: 100000 })
    for (let m = 780; m <= 900; m++) trend.push({ time: Date.UTC(...day, Math.floor(m / 60), m % 60) / 1000, price: 10 + (m - 570) / 240, vol: 100, amount: 100000 })
    const wrapper = mount(StockChartCanvas, {
      props: {
        view: 'trend', kline: [], trend,
        quote: { prevClose: 10, upPx: 11, downPx: 9 },
        overlays: { ma: false, boll: false }, chan: false, wave: false,
        subInd: 'none', indCache: null,
      },
      attachTo: document.body,
    })
    mockCanvas(wrapper)
    const canvas = wrapper.find('canvas')
    // 光标滑到内容区中部偏右 (x=250 → 交易分钟 ~150 → 14:00 附近)
    await canvas.trigger('pointermove', { clientX: 250, clientY: 100 })
    const emitted = wrapper.emitted('crossinfo')
    expect(emitted).toBeTruthy()
    const info = emitted.at(-1)[0]
    expect(info).toBeTruthy()
    expect(info.close).toBeTypeOf('number')
    expect(info.time).toBeTypeOf('number')
    // 午休区 (x → 12:00) 也应吸附到最近交易点, 而非 null
    await canvas.trigger('pointermove', { clientX: 200, clientY: 100 })
    const info2 = wrapper.emitted('crossinfo').at(-1)[0]
    expect(info2).toBeTruthy()
    expect(info2.close).toBeTypeOf('number')
    wrapper.unmount()
  })

  it('K线 hover 触发 crossinfo emit', async () => {
    const kline = [
      { time: '2026-08-07', open: 10, close: 10.2, high: 10.4, low: 9.8, volume: 5000 },
      { time: '2026-08-08', open: 10.2, close: 9.9, high: 10.3, low: 9.7, volume: 6000 },
    ]
    const wrapper = mount(StockChartCanvas, {
      props: {
        view: 'day', kline, trend: [], quote: { prevClose: 10 },
        overlays: { ma: false, boll: false }, chan: false, wave: false,
        subInd: 'none', indCache: null,
      },
      attachTo: document.body,
    })
    mockCanvas(wrapper)
    const canvas = wrapper.find('canvas')
    await canvas.trigger('pointermove', { clientX: 50, clientY: 50 })
    const emitted = wrapper.emitted('crossinfo')
    expect(emitted).toBeTruthy()
    expect(emitted[0][0]).toMatchObject({ close: expect.any(Number), chgPct: expect.any(Number) })
    wrapper.unmount()
  })
})
