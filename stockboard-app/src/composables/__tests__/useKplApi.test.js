import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchZhangTingGene, fetchMainMonitor, fetchStockBid,
  fetchStockPankou, fetchStockLhbHistory, getLatestTradingDay,
} from '../useKplApi.js'

// useKplApi 内部 postForm/getJson 是闭包变量, vi.mock 换导出无效 → 改为 mock 全局 fetch
// 不发起真实请求, 只测「返回 JSON → 映射对象」的纯逻辑
const originalFetch = globalThis.fetch
const calls = []   // [{ url, body, opts }] 记录每次 fetch, 供断言参数

// json 可为对象, 或 (url, body) => 对象的函数(同测试多次调用时按需返回)
function mockFetch(json) {
  globalThis.fetch = vi.fn(async (url, opts = {}) => {
    const body = {}
    if (opts.body) {
      const usp = new URLSearchParams(opts.body)
      for (const [k, v] of usp.entries()) body[k] = v
    }
    calls.push({ url: String(url), body, opts })
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

  it('fetchMainMonitor 按文档行序解析: [买卖方向,时间戳,量,金额,均价,时间]', async () => {
    // 行序与方向图例: kpl-api.md [48][49] — 1 被动卖/2 主动买/3 被动买/4 主动卖
    mockFetch({ List: [
      ['2', '1778651941', '1075', '1011575', '9.41', '2026-05-13 13:59:01'],
      ['4', '1778651942', '50', '50000', '9.40', '2026-05-13 13:59:02'],
    ] })
    const r = await fetchMainMonitor('002594')
    expect(calls).toHaveLength(1)
    expect(calls[0].body).toMatchObject({ a: 'GetMainMonitor_w30', StockID: '002594' })
    expect(r).toHaveLength(2)
    expect(r[0]).toMatchObject({ time: '13:59:01', price: 9.41, side: '买', vol: 1075, amount: 1011575, type: '超大' })
    expect(r[1]).toMatchObject({ time: '13:59:02', side: '卖', vol: 50, type: '大单' })
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

  it('getLatestTradingDay 采集日落在国庆节假日 → 回退到节前最后交易日', async () => {
    mockFetch({ date: '2026-10-03' })   // 周六且在国庆休市区间(10/1-10/7)
    expect(await getLatestTradingDay()).toBe('20260930')
  })
  it('getLatestTradingDay 采集日为正常交易日 → 原样返回', async () => {
    mockFetch({ date: '2026-09-11' })
    expect(await getLatestTradingDay()).toBe('20260911')
  })
  it('getLatestTradingDay 采集日为中秋(周五休市) → 回退到周四', async () => {
    mockFetch({ date: '2026-09-25' })
    expect(await getLatestTradingDay()).toBe('20260924')
  })

  it('getLatestTradingDay 读 core.json 必须绕开 HTTP 缓存(no-store)', async () => {
    // 回归(2026-09-15 实测): 此处曾用默认缓存策略, 浏览器缓存里是旧 core.json(采集日 09-12 周六)
    // → 页脚"数据截至"算成 09-11, 与其它区块(loader 走 no-store)显示的 09-15 自相矛盾。
    // 时点披露只能有一个数据源, 故锁定 no-store。
    mockFetch({ date: '2026-09-15' })
    await getLatestTradingDay()
    const c = calls.find(x => x.url.includes('data/latest/core.json'))
    expect(c).toBeTruthy()
    expect(c.opts.cache).toBe('no-store')
  })
})
