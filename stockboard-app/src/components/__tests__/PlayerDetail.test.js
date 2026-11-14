// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { computed, ref } from 'vue'
import PlayerDetail from '../PlayerDetail.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { zh_id: '900438148' } }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}))

// 字段结构来自 2026-11-13 rtV2 实测返回（真实数据，非臆造）
const POS = [{
  blkRatio: '75', BlockName: 'IT服务Ⅱ',
  data: [{ market: '0', stkMktCode: 'SZ000938', __code: '000938', __name: '紫光股份',
           cbj: '28.494', holdPos: '75', __zxjg: '25.150', webYkRate: '-11.74' }],
}]
const YIELD = {
  totalYieldRateView: '304.15', dayYieldRateView: '0.12',
  monthYieldRateView: '-0.24', yearYieldRateView: '215.77',
  netWorth: '4.041', hitBoardName: '近1年', hitBoardPos: '7',
}
const TRADES = {
  pages: [{ bizDate: 20261113, relocateTime: '2026-11-13 10:59:48', stkCode: '000938',
            stkName: '紫光股份', bsMark: 'B', relocatePrice: '22.920', positionRatio: '1成以下' }],
  totalCount: 1, pageIndex: 1, totalPages: 1,
}

function provideStockData() {
  return {
    playerLookup: computed(() => ({
      '900438148': { zh_id: '900438148', id: '900438148', name: '我嘚财富',
                     userId: 'u-test', watched: true,
                     total_return: null, daily_return: null, net_value: null, today: null },
    })),
    emContract: computed(() => ({ ok: true, checkedAt: '2026-11-13T09:26:00+08:00' })),
    currentDate: computed(() => '2026-11-13'),
    tradedPlayerIds: computed(() => new Set()),
    sliceErrors: ref({}),
  }
}

function mountDetail(overrides = null) {
  const stockData = { ...provideStockData(), ...(overrides || {}) }
  return mount(PlayerDetail, {
    global: {
      provide: { stockData, refreshTick: ref(0) },
    },
  })
}

/** 按 method 返回真实形状；yieldFail=true 时复现实测的 -1 登录失效。 */
function stubFetch(yieldFail = false, trades = TRADES) {
  return vi.fn().mockImplementation((_url, opts) => {
    const method = JSON.parse(opts.body).method
    let body
    if (method === 'CombinationHoldPositionPermitHandler') body = { code: 0, message: 'Success', data: POS }
    else if (method === 'CombinationRelocatePositionHandler') body = { code: 0, message: 'Success', data: trades }
    else if (yieldFail) body = { code: -1, message: '登录失效，请重新登录', data: {} }
    else body = { code: 0, message: 'Success', data: YIELD }
    return Promise.resolve({ json: async () => body })
  })
}

function realDataRendered(w) {
  const t = w.text()
  return {
    stock: t.includes('紫光股份'),
    total: t.includes('+304.15%'),
    net: t.includes('4.041'),
    trade: t.includes('22.92') && t.includes('买入'),
    posCount: t.includes('共 1 条持仓'),
  }
}

describe('PlayerDetail · 真实数据必须渲染出来', () => {
  beforeEach(() => { vi.stubGlobal('fetch', stubFetch(false)) })
  afterEach(() => { vi.unstubAllGlobals() })

  it('三块全成功：持仓/收益/调仓都出现真实读数', async () => {
    const w = mountDetail()
    await flushPromises()
    const r = realDataRendered(w)
    expect(r.stock).toBe(true)       // 持仓 + 调仓都渲染股票名
    expect(r.total).toBe(true)       // 顶部总收益来自实时 yield
    expect(r.net).toBe(true)
    expect(r.trade).toBe(true)
    expect(r.posCount).toBe(true)
    expect(w.text()).toContain('实时接口：今日 1 笔调仓')  // 静态 today 为空时用实时当日记录
    expect(w.text()).not.toContain('已获取')
    expect(w.findAll('.pd-table')).toHaveLength(3)
  })

  it('顶部读数标明实时来源', async () => {
    const w = mountDetail()
    await flushPromises()
    expect(w.find('.pd-source').text()).toContain('实时接口')
  })
})

describe('PlayerDetail · 部分接口登录失效时其它区块照常展示', () => {
  beforeEach(() => { vi.stubGlobal('fetch', stubFetch(true)) })
  afterEach(() => { vi.unstubAllGlobals() })

  it('yield=-1：持仓/调仓仍渲染，收益区块明确不可用', async () => {
    const w = mountDetail()
    await flushPromises()
    const t = w.text()
    expect(t).toContain('紫光股份')
    expect(t).toContain('22.92')
    expect(t).toContain('该区块实时数据不可用')
    expect(t).toContain('2/3 区块实时可用')       // 整体不判死
    expect(t).toContain('登录态')                  // 原因可见
    expect(t).not.toContain('已回退至静态快照')     // 静态为空时不谎称有回退数据
    expect(w.findAll('.pd-table')).toHaveLength(3) // 持仓 + 调仓表格仍在
  })

  it('失败徽标可展开原始 code/message', async () => {
    const w = mountDetail()
    await flushPromises()
    const badges = w.findAll('.db-main')
    const errBadge = badges.find(b => b.text().includes('实时接口失败'))
    expect(errBadge).toBeTruthy()
    await errBadge.trigger('click')
    expect(w.text()).toContain('登录失效，请重新登录')
    expect(w.text()).toContain('code=-1')
  })
})

describe('PlayerDetail · 今日操作要显示操作了什么', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('当日买+卖两笔：明细列出股票/代码/方向/价格/仓位，而不只是笔数', async () => {
    const multi = { pages: [
      { relocateTime: '2026-11-13 10:59:48', stkCode: '000938', stkName: '紫光股份',
        bsMark: 'B', relocatePrice: '22.920', positionRatio: '1成以下' },
      { relocateTime: '2026-11-13 09:35:02', stkCode: '600519', stkName: '贵州茅台',
        bsMark: 'S', relocatePrice: '1500.000', positionRatio: '2成' },
    ], totalCount: 2, pageIndex: 1, totalPages: 1 }
    vi.stubGlobal('fetch', stubFetch(false, multi))

    const w = mountDetail()
    await flushPromises()

    expect(w.text()).toContain('实时接口：今日 2 笔调仓')
    const today = w.find('.pd-today-table')
    expect(today.exists()).toBe(true)
    const t = today.text()
    expect(t).toContain('紫光股份')
    expect(t).toContain('000938')
    expect(t).toContain('买入')
    expect(t).toContain('22.92')
    expect(t).toContain('1成以下')
    expect(t).toContain('贵州茅台')
    expect(t).toContain('600519')
    expect(t).toContain('卖出')
    expect(t).toContain('1500.00')
    expect(t).toContain('2成')
  })

  it('调仓只有 bizDate 无 relocateTime：仍能识别为当日操作并格式化日期', async () => {
    const compact = { pages: [
      { bizDate: 20261113, stkCode: '000938', stkName: '紫光股份',
        bsMark: 'B', relocatePrice: '22.920', positionRatio: '1成以下' },
    ], totalCount: 1 }
    vi.stubGlobal('fetch', stubFetch(false, compact))

    const w = mountDetail()
    await flushPromises()

    const today = w.find('.pd-today-table')
    expect(today.exists()).toBe(true)
    expect(today.text()).toContain('2026-11-13')
    expect(today.text()).toContain('紫光股份')
  })

  it('实时明细不可用时：静态快照只报笔数并说明无明细，不臆造股票', async () => {
    const withSnapshot = {
      playerLookup: computed(() => ({
        '900438148': { zh_id: '900438148', name: '我嘚财富', userId: 'u-test', watched: true,
                       total_return: null, daily_return: null, net_value: null,
                       today: { updatedAt: '15:15', tradeCount: 3 } },
      })),
    }
    vi.stubGlobal('fetch', stubFetch(false, { pages: [], totalCount: 0 }))

    const w = mountDetail(withSnapshot)
    await flushPromises()

    expect(w.text()).toContain('静态快照：今日 3 笔调仓')
    expect(w.text()).toContain('静态快照仅记录笔数')
    expect(w.find('.pd-today-table').exists()).toBe(false)
  })
})

/** 调仓块确定性失败(-1)：其余块正常，用来验证「失败 ≠ 今日没操作」。 */
function stubFetchTradesFail() {
  return vi.fn().mockImplementation((_url, opts) => {
    const { method } = JSON.parse(opts.body)
    let body
    if (method === 'CombinationHoldPositionPermitHandler') body = { code: 0, data: POS }
    else if (method === 'CombinationRelocatePositionHandler') body = { code: -1, message: '登录失效，请重新登录', data: {} }
    else body = { code: 0, data: YIELD }
    return Promise.resolve({ json: async () => body })
  })
}

/** 调仓两页，每页各 1 笔当日操作；验证跨页合并后的明细不丢。 */
function stubFetchPagedTrades() {
  return vi.fn().mockImplementation((_url, opts) => {
    const { method, args } = JSON.parse(opts.body)
    let body
    if (method === 'CombinationHoldPositionPermitHandler') body = { code: 0, data: POS }
    else if (method === 'CombinationRelocatePositionHandler') {
      const p = args.pageNum
      const row = p === 1
        ? { relocateTime: '2026-11-13 10:59:48', stkCode: '000938', stkName: '紫光股份', bsMark: 'B', relocatePrice: '22.920', positionRatio: '1成以下' }
        : { relocateTime: '2026-11-13 09:35:02', stkCode: '600519', stkName: '贵州茅台', bsMark: 'S', relocatePrice: '1500.000', positionRatio: '2成' }
      body = { code: 0, data: { pages: [row], totalPages: 2, pageIndex: p, totalCount: 2 } }
    } else body = { code: 0, data: YIELD }
    return Promise.resolve({ json: async () => body })
  })
}

describe('PlayerDetail · Failure ≠ Fact：今日操作状态语义', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('实时调仓失败：明确「无法确认」，不谎称今日无操作', async () => {
    vi.stubGlobal('fetch', stubFetchTradesFail())
    const w = mountDetail()
    await flushPromises()
    expect(w.find('[data-today="unavailable"]').exists()).toBe(true)
    expect(w.text()).toContain('今日是否有操作无法确认')
    expect(w.text()).not.toContain('今日暂无调仓记录')
    expect(w.text()).not.toContain('今日尚无调仓快照')
  })

  it('实时成功但返回空 pages：才可以说「今日暂无调仓记录」', async () => {
    vi.stubGlobal('fetch', stubFetch(false, { pages: [], totalCount: 0, totalPages: 1, pageIndex: 1 }))
    const w = mountDetail()
    await flushPromises()
    expect(w.find('[data-today="none"]').exists()).toBe(true)
    expect(w.text()).toContain('实时接口已确认：今日暂无调仓记录')
    expect(w.find('[data-today="unavailable"]').exists()).toBe(false)
  })

  it('静态快照有笔数但实时明细失败：保留笔数，同时说明明细不可用', async () => {
    const withSnapshot = {
      playerLookup: computed(() => ({
        '900438148': { zh_id: '900438148', name: '我嘚财富', userId: 'u-test', watched: true,
                       total_return: null, daily_return: null, net_value: null,
                       today: { updatedAt: '15:15', tradeCount: 3 } },
      })),
    }
    vi.stubGlobal('fetch', stubFetchTradesFail())
    const w = mountDetail(withSnapshot)
    await flushPromises()
    expect(w.text()).toContain('静态快照：今日 3 笔调仓')
    expect(w.text()).toContain('实时明细接口不可用')
    expect(w.text()).not.toContain('具体买卖明细见下方')
  })

  it('调仓跨两页：今日明细表包含两页记录，不再只显示第 1 页', async () => {
    vi.stubGlobal('fetch', stubFetchPagedTrades())
    const w = mountDetail()
    await flushPromises()
    expect(w.text()).toContain('实时接口：今日 2 笔调仓')
    const today = w.find('.pd-today-table')
    expect(today.text()).toContain('紫光股份')
    expect(today.text()).toContain('贵州茅台')
    expect(w.text()).not.toContain('第 1 页明细')
  })

  // MN-03a: 数据时点未知必须说「无法判定」，不能把 Failure 说成「今日无操作」
  it('currentDate 为空 → 显示 no-date，不谎称今日无操作', async () => {
    vi.stubGlobal('fetch', stubFetch(false, { pages: [], totalCount: 0, totalPages: 1, pageIndex: 1 }))
    const w = mountDetail({ currentDate: computed(() => '') })
    await flushPromises()
    expect(w.find('[data-today="no-date"]').exists()).toBe(true)
    expect(w.text()).toContain('数据时点未知，无法判定今日操作')
    expect(w.text()).not.toContain('今日暂无调仓记录')
    expect(w.text()).not.toContain('今日是否有操作无法确认')
  })

  // MN-03b: 实时请求未开始/进行中（blocks 仍 idle）应显示「请求中」而非「无操作」
  it('实时接口未返回（idle）→ 显示 pending，不谎称今日无操作', async () => {
    const data = provideStockData()
    data.playerLookup = computed(() => ({
      '900438148': { zh_id: '900438148', id: '900438148', name: '我嘚财富',
                     userId: '', watched: false,
                     total_return: null, daily_return: null, net_value: null, today: null },
    }))
    const w = mount(PlayerDetail, {
      global: { provide: { stockData: data, refreshTick: ref(0) } },
    })
    await flushPromises()
    expect(w.find('[data-today="pending"]').exists()).toBe(true)
    expect(w.text()).toContain('实时调仓请求中')
    expect(w.text()).not.toContain('今日暂无调仓记录')
  })
})
