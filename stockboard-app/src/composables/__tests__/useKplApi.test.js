import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchZhangTingGene, fetchMainMonitor, fetchStockBid,
  fetchStockPankou, fetchStockLhbHistory,
} from '../useKplApi.js'

// useKplApi 内部 postForm/getJson 是闭包变量, vi.mock 换导出无效 → 改为 mock 全局 fetch
// 不发起真实请求, 只测「返回 JSON → 映射对象」的纯逻辑
const originalFetch = globalThis.fetch
const calls = []   // [{ url, body }] 记录每次 fetch, 供断言参数

// json 可为对象, 或 (url, body) => 对象的函数(同测试多次调用时按需返回)
function mockFetch(json) {
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const body = {}
    if (opts.body) {
      const usp = new URLSearchParams(opts.body)
      for (const [k, v] of usp.entries()) body[k] = v
    }
    calls.push({ url: String(url), body })
    const data = typeof json === 'function' ? json(String(url), body) : json
    return { json: async () => data }
  })
}

describe('KPL 新增接口', () => {
  beforeEach(() => {
    calls.length = 0
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('fetchZhangTingGene 解析基因六维', async () => {
    mockFetch({ List: [['5', '3', '62.5', '78', '22', '40']] })
    const r = await fetchZhangTingGene('002594')
    expect(calls).toHaveLength(1)
    expect(calls[0].url).toContain('GetZhangTingGene')
    expect(r).toEqual({ ztCount: '5', premium5: '3', nextRedPct: '62.5', firstSealPct: '78', breakPct: '22', lianbanPct: '40' })
  })
  it('fetchZhangTingGene 空 List 返回 null', async () => {
    mockFetch({ List: [] })
    expect(await fetchZhangTingGene('002594')).toBeNull()
    expect(await fetchZhangTingGene('002594', true)).toBeNull()
  })

  it('fetchMainMonitor 解析逐笔列表', async () => {
    mockFetch({ List: [['14:02', '101.5', '0', '200', '1', '203000']] })
    const r = await fetchMainMonitor('002594')
    expect(calls).toHaveLength(1)
    expect(calls[0].body).toMatchObject({ a: 'GetMainMonitor_w30', StockID: '002594' })
    expect(r).toHaveLength(1)
    expect(r[0]).toMatchObject({ time: '14:02', price: 101.5, side: '买', vol: 200, amount: 203000 })
  })
  it('fetchMainMonitor 空/失败返回 null', async () => {
    mockFetch({})
    expect(await fetchMainMonitor('002594')).toBeNull()
  })

  it('fetchStockBid 解析竞价序列', async () => {
    mockFetch({ bid: [['0925', '101.2', '1', '12000']] })
    const r = await fetchStockBid('002594')
    expect(r).toHaveLength(1)
    expect(r[0]).toMatchObject({ time: '0925', price: 101.2, side: '1', cumVol: 12000 })
  })

  it('fetchStockLhbHistory 按 code 过滤 + dealer 数组', async () => {
    mockFetch({
      Time: '2026-08-07',
      list: [
        { ID: '002594', Name: '比亚迪', IncreaseAmount: '10.02%', BuyIn: 186000000, JoinNum: 2 },
        { ID: '000001', Name: '平安', IncreaseAmount: '3.5%', BuyIn: 5000000, JoinNum: 0 },
      ],
    })
    const r = await fetchStockLhbHistory('002594')
    expect(calls[0].body).toMatchObject({ a: 'GetStockList' })
    expect(r).toHaveLength(1)
    expect(r[0]).toMatchObject({ code: '002594', name: '比亚迪', chgPct: 10.02, buyIn: 186000000, joinNum: 2 })
    expect(r[0].dealer).toEqual([])
  })
  it('fetchStockLhbHistory 无上榜返回空数组', async () => {
    mockFetch({ Time: '2026-08-07', list: [] })
    expect(await fetchStockLhbHistory('000001')).toEqual([])
  })

  it('fetchStockPankou 返回原始对象或 null', async () => {
    let n = 0
    mockFetch(() => { n += 1; return n === 1 ? { errcode: '0', weituo: [] } : { errcode: '500' } })
    const r = await fetchStockPankou('002594')
    expect(r).toEqual({ errcode: '0', weituo: [] })
    expect(await fetchStockPankou('002594')).toBeNull()
  })
})
