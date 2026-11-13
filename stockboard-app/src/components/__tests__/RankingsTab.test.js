// @vitest-environment happy-dom
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { computed, ref, defineComponent, h } from 'vue'
import RankingsTab from '../RankingsTab.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  onBeforeRouteLeave: vi.fn(),
}))

const P1 = {
  zh_id: '900456476', name: '狼之行一', watched: true,
  total_return: 100, daily_return: 1.5, monthly_return: -0.24,
  yearly_return: 215.77, net_value: 2,
}
const P2 = {
  zh_id: '900111111', name: '未关注', watched: false,
  total_return: 10, daily_return: null, monthly_return: null,
  yearly_return: null, net_value: 1.1,
}

// 虚拟滚动库在单测里换成渲染全部行的 stub；保留 scrollToPosition / $el.scrollTop 契约。
const ScrollerStub = defineComponent({
  name: 'RecycleScroller',
  props: { items: { type: Array, default: () => [] } },
  methods: { scrollToPosition() {} },
  setup(props, { slots }) {
    return () => h('div', { class: 'rank-vscroll' },
      (props.items || []).map(item => (slots.default ? slots.default({ item }) : null)))
  },
})

function makeStockData(overrides = {}) {
  return {
    sortedPlayers: computed(() => ({ pinned: [P1], rest: [P2] })),
    tradedPlayerIds: computed(() => new Set()),
    watchedIds: computed(() => new Set(['900456476'])),
    loading: ref({ players: false, today: false }),
    sliceErrors: ref({}),
    sliceLoaded: computed(() => ({ players: true, today: true })),
    ensureSlices: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

function mountRank(stockData) {
  return mount(RankingsTab, {
    global: {
      provide: { stockData },
      stubs: { RecycleScroller: ScrollerStub },
    },
  })
}

describe('RankingsTab · 三态显式化', () => {
  it('有数据：正常渲染榜单，缺失收益显示 —，周收益列已下线', async () => {
    const w = mountRank(makeStockData())
    await flushPromises()
    expect(w.find('[data-state]').exists()).toBe(false)
    expect(w.findAll('.rank-row')).toHaveLength(2)
    expect(w.text()).toContain('狼之行一')
    expect(w.text()).toContain('+100.00%')
    expect(w.text()).not.toContain('周收益')
    expect(w.text()).not.toContain('+0.00%')
    // 默认排序周期是「总」→ 表头只显示总收益 + 净值；切到「月」才能看到月收益真实值
    const monthBtn = w.findAll('.filter-btn').find(b => b.text() === '月')
    await monthBtn.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('-0.24%')
    // P2 的月/年/日为 null：不得用 +0.00% 冒充，必须显示 —
    expect(w.text()).toContain('—')
    expect(w.text()).not.toContain('+0.00%')
  })

  it('加载中：只显示 loading 态，不露出空表头/空表格', async () => {
    const w = mountRank(makeStockData({ loading: ref({ players: true, today: false }) }))
    await flushPromises()
    expect(w.find('[data-state="loading"]').exists()).toBe(true)
    expect(w.text()).toContain('正在加载选手榜单')
    expect(w.find('.rank-head').exists()).toBe(false)
    expect(w.findAll('.rank-row')).toHaveLength(0)
  })

  it('分片失败：显示失败原因与重试按钮；点击重试拉取 players + today', async () => {
    const data = makeStockData({ sliceErrors: ref({ players: true }) })
    const w = mountRank(data)
    await flushPromises()
    expect(w.find('[data-state="error"]').exists()).toBe(true)
    expect(w.text()).toContain('榜单数据加载失败')
    expect(w.text()).toContain('未用 0 填充收益')
    expect(w.find('.rank-head').exists()).toBe(false)

    await w.find('.rank-retry').trigger('click')
    expect(data.ensureSlices).toHaveBeenCalledWith(['players', 'today'])
    await flushPromises()
    // 失败标记未清 → 仍停在错误态，不会假装成功
    expect(w.find('[data-state="error"]').exists()).toBe(true)
  })

  it('重试成功后自动回到榜单', async () => {
    const sliceErrors = ref({ players: true })
    const data = makeStockData({
      sliceErrors,
      ensureSlices: vi.fn().mockImplementation(async () => { sliceErrors.value = {} }),
    })
    const w = mountRank(data)
    await flushPromises()
    expect(w.find('[data-state="error"]').exists()).toBe(true)

    await w.find('.rank-retry').trigger('click')
    await flushPromises()
    expect(w.find('[data-state]').exists()).toBe(false)
    expect(w.findAll('.rank-row')).toHaveLength(2)
  })

  it('数据为空：显示空态而不是 0 人的空榜单', async () => {
    const data = makeStockData({
      sortedPlayers: computed(() => ({ pinned: [], rest: [] })),
    })
    const w = mountRank(data)
    await flushPromises()
    expect(w.find('[data-state="empty"]').exists()).toBe(true)
    expect(w.text()).toContain('暂无选手数据')
    expect(w.find('.rank-retry').exists()).toBe(true)
    expect(w.find('.rank-head').exists()).toBe(false)
  })

  it('today 分片失败也算榜单失败（今日操作来源缺失）', async () => {
    const w = mountRank(makeStockData({ sliceErrors: ref({ today: true }) }))
    await flushPromises()
    expect(w.find('[data-state="error"]').exists()).toBe(true)
  })
})

describe('RankingsTab · 三态改造不破坏排序/筛选', () => {
  it('切换收益周期仍正常渲染，且不出现周/回撤/风格等无来源列', async () => {
    const w = mountRank(makeStockData())
    await flushPromises()
    const monthBtn = w.findAll('.filter-btn').find(b => b.text() === '月')
    await monthBtn.trigger('click')
    await flushPromises()
    expect(w.findAll('.rank-row')).toHaveLength(2)
    expect(w.find('.rank-head').text()).toContain('月收益')
    const t = w.find('.rank-head').text()
    for (const gone of ['周收益', '回撤', '风格', '仓位', '运行', '关注']) {
      expect(t).not.toContain(gone)
    }
  })

  it('有当日调仓时默认只看今日操作选手', async () => {
    // 置顶选手独立渲染且不受「今日操作」过滤 → 用无置顶数据验证筛选本身
    const w = mountRank(makeStockData({
      watchedIds: computed(() => new Set()),
      tradedPlayerIds: computed(() => new Set(['900111111'])),
    }))
    await flushPromises()
    expect(w.findAll('.rank-row')).toHaveLength(1)
    expect(w.text()).toContain('未关注')
  })
})
