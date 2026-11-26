// @vitest-environment happy-dom
// MarketTab 挂载冒烟测试（2026-11-17 死链清理的回归护栏）。
//
// 背景：本次删除了三条"数据源已下线但代码仍在跑"的死链——六情绪实时版、
// 今日竞价弱转强、昨日可买复核，以及被 SHOW_TOUCH 隐藏的持仓触价块。
// 模板里若残留对已删除绑定的引用，`vite build` 不会报错（编译成 _ctx.xxx），
// 只会在运行时渲染报错 → 必须用真实挂载拦住。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../../composables/usePullRefresh.js', () => ({ usePullRefresh: vi.fn() }))
vi.mock('../../data/loader.js', () => ({
  clearDataCache: vi.fn(),
  fetchAuction: vi.fn().mockResolvedValue(null),
  fetchToday: vi.fn().mockResolvedValue({ date: '2026-11-17' }),
  fetchLianbanBid: vi.fn().mockResolvedValue([]),
}))
// eastmoney.jsonp 在挂载期注入真实 script 拉行情: CI runner 上东财偶发 502 时用例挂到
// 25s 超时(2026-11-26 门禁首次失败) → 断网 stub, 行情分支走错误路径, 其余导出保持真实现。
vi.mock('../../utils/eastmoney.js', async (importOriginal) => ({
  ...await importOriginal(),
  jsonp: () => Promise.reject(new Error('测试环境无网络')),
}))
vi.mock('../../composables/useKplApi.js', () => ({
  fetchTianTi: vi.fn().mockResolvedValue([]),
  fetchGlobalIndexes: vi.fn().mockResolvedValue(null),
  fetchInstitutionIncrease: vi.fn().mockResolvedValue([]),
  fetchMarketMood: vi.fn().mockResolvedValue([{ strong: 55, df: 3 }]),
  fetchLhbList: vi.fn().mockResolvedValue(null),
  fetchLimitPool: vi.fn().mockResolvedValue([]),
  fetchRiseFall: vi.fn().mockResolvedValue({ today: { day: '2026-11-17', zt: 40, brokeRate: 20 }, series: [] }),
  fetchUnsealedPool: vi.fn().mockResolvedValue([]),
  getLatestTradingDay: vi.fn().mockResolvedValue('20261117'),
  getLatestReportDate: vi.fn().mockReturnValue('20261115'),
  isTradingTime: vi.fn().mockReturnValue(true),
}))
vi.mock('../../utils/emotionCycle.js', async (importOriginal) => {
  const real = await importOriginal()
  return { ...real, loadCycleData: vi.fn() }
})
vi.mock('../../utils/leaderBattle.js', async (importOriginal) => {
  const real = await importOriginal()
  return { ...real, loadBattleData: vi.fn() }
})

const { loadCycleData } = await import('../../utils/emotionCycle.js')
const { loadBattleData } = await import('../../utils/leaderBattle.js')
const { usePullRefresh } = await import('../../composables/usePullRefresh.js')
const MarketTab = (await import('../MarketTab.vue')).default

const CYCLE = {
  date: '2026-11-17', stage: '发酵', confidence: 7, reasons: ['高度 5B'],
  metrics: { height: 5, heightPrev: 4, zt: 40, brokeRate: 20, promo: { low: 0.5, mid: 0.5, high: null }, ladder: { low: 1, mid: 2, high: 0 }, hTrend: [3, 4, 5] },
  matrix: { high: '强', mid: '平衡' }, mainlines: [], leaders: [],
}
const BATTLE = {
  empty: false,
  strike: {
    gate: { cap: 100, stage: '发酵', matrix: { high: '强', mid: '平衡' }, banner: '可做 · 📐 主线' },
    relay: { txt: '接力顺' },
    candidates: [{
      code: '600000', name: '测试股', level: 3, platesTxt: '半导体',
      score: 88, status: 'ok', mode: '低吸', logic: '主线首板', risk: '',
    }],
    disclaimer: '纪律优先',
  },
  boardWars: { mainSwitch: { note: '主线切换：旧→新' } },
  risks: { brokenHighs: [] },
  premium: { total: 10, still: 5, open: 2, red: 8, green: 2, noq: 0, avgPct: 1.2, avgFirst: 1, avgLian: 2 },
}

function mountTab() {
  return mount(MarketTab, { global: { stubs: { Hint: true } } })
}

// 组件在 onActivated 里首屏加载(KeepAlive 语义)；裸挂载不触发该钩子,
// 故用 pull-refresh 回调驱动同一条加载链 —— 与用户下拉刷新走的是同一路径。
async function mountAndLoad() {
  const w = mountTab()
  const refresh = usePullRefresh.mock.calls.at(-1)?.[0]
  expect(typeof refresh).toBe('function')
  await refresh()
  await flushPromises()
  return w
}

describe('MarketTab 挂载（死链清理回归）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('空数据首屏不崩，显示"加载中"而不是假结论', async () => {
    loadCycleData.mockResolvedValue({ cycle: null, ladderRows: [], prevFull: [], riseFallToday: null })
    loadBattleData.mockResolvedValue({ empty: true, strike: { candidates: [] } })
    const w = await mountAndLoad()
    expect(w.text()).toContain('周期数据加载中')
  })

  it('有数据时正常渲染结论头与出击候选', async () => {
    loadCycleData.mockResolvedValue({ cycle: CYCLE, ladderRows: [], prevFull: [], riseFallToday: null })
    loadBattleData.mockResolvedValue(BATTLE)
    const w = await mountAndLoad()
    expect(w.text()).toContain('测试股')
    expect(w.text()).toContain('发酵')
  })

  it('已下线区块不再出现在 DOM 中（弱转强 / 昨日复核 / 持仓触价 / 大模型选股）', async () => {
    loadCycleData.mockResolvedValue({ cycle: CYCLE, ladderRows: [], prevFull: [], riseFallToday: null })
    loadBattleData.mockResolvedValue(BATTLE)
    const w = await mountAndLoad()
    const html = w.html()
    expect(html).not.toContain('今日竞价弱转强')
    expect(html).not.toContain('昨日可买复核')
    expect(html).not.toContain('持仓触价')
    expect(html).not.toContain('已持仓')          // holdOf 已删除，不应残留标记
    expect(html).not.toContain('弱转强·炸板回封')
    expect(html).not.toContain('大模型选股')      // 2026-11-19 拍板下线(llm-1000 删除后无新产出)
    expect(html).not.toContain('暂无模型输出')
  })

  it('渲染期无"未定义绑定"类告警（模板残留会在这里暴露）', async () => {
    loadCycleData.mockResolvedValue({ cycle: CYCLE, ladderRows: [], prevFull: [], riseFallToday: null })
    loadBattleData.mockResolvedValue(BATTLE)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const w = await mountAndLoad()
    // 死链清理时残留的 refreshSix() 就是被这条断言抓出来的：它只打 console.error，不抛错
    const msgs = [...warn.mock.calls, ...err.mock.calls].map(c => String(c[0])).join('\n')
    warn.mockRestore()
    err.mockRestore()
    expect(msgs).not.toMatch(/is not defined|Failed to resolve component|Property ".*" was accessed during render but is not defined/)
    expect(w.exists()).toBe(true)
  })

  it('周期接口失败时显示可重试的失败提示（不伪装成加载中）', async () => {
    loadCycleData.mockRejectedValue(new Error('kpl down'))
    loadBattleData.mockResolvedValue({ empty: true, strike: { candidates: [] } })
    const w = await mountAndLoad()
    expect(w.text()).toContain('周期数据加载失败')
  })
})
