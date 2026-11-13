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

function mountDetail() {
  return mount(PlayerDetail, {
    global: {
      provide: { stockData: provideStockData(), refreshTick: ref(0) },
    },
  })
}

/** 按 method 返回真实形状；yieldFail=true 时复现实测的 -1 登录失效。 */
function stubFetch(yieldFail = false) {
  return vi.fn().mockImplementation((_url, opts) => {
    const method = JSON.parse(opts.body).method
    let body
    if (method === 'CombinationHoldPositionPermitHandler') body = { code: 0, message: 'Success', data: POS }
    else if (method === 'CombinationRelocatePositionHandler') body = { code: 0, message: 'Success', data: TRADES }
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
    expect(w.findAll('.pd-table')).toHaveLength(2)
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
    expect(w.findAll('.pd-table')).toHaveLength(2) // 持仓 + 调仓表格仍在
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
