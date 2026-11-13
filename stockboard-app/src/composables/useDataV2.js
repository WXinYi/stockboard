import { ref, computed } from 'vue'
import { fetchPlayers, fetchStocks, fetchToday } from '../data/loader.js'

/**
 * P3 / AQ3=B 新数据层：只消费 4 个契约（players / stocks / today / market）。
 *
 * 设计要点：
 * 1. **零兼容代码** —— 不双读 `stockStats`、不读 core/copy/name_map/players_index。
 * 2. **同名派生接口** —— 导出名与 useData.js 对齐（sortedPlayers / stockStats /
 *    tradedPlayerIds / watchedIds / playerLookup / currentDate / crawlTime），
 *    使页面改造可以逐文件收敛而不出现中间态白屏。
 * 3. `loadData()` 只等 `today.json`（最小分片，最早拿到 `emContract`，R5#5）。
 * 4. 收益字段来自 `players[].prevClose`（盘中不变），今日操作来自 `today.tradedPlayerIds`。
 */

// 仅当 players.json 的 watched 全缺失时兜底（正常路径永远是数据里的 watched=true）
const FALLBACK_WATCHED = new Set([
  '900456476', '900351276', '900240956', '900438148', '900376763',
  '900439290', '900461598', '900293400', '900458047', '900455448',
])

const SLICE_LOADER = { players: fetchPlayers, stocks: fetchStocks, today: fetchToday }

export function useDataV2() {
  const loading = ref({ players: false, stocks: false, today: false })
  const slices = {
    players: ref(null), stocks: ref(null), today: ref(null),
  }
  const sliceErrors = ref({})
  const sliceLoaded = computed(() => ({
    players: slices.players.value != null,
    stocks: slices.stocks.value != null,
    today: slices.today.value != null,
  }))

  const sortKey = ref('total_return')
  const qualityOnly = ref(false)

  // 优质口径在 P3 已废除（stocks.json 不再有 qh/qtp/qap；players 无 quality 字段）
  function isQuality() { return false }

  // 新契约 player → 页面既有字段名（total_return 等），不引入第二套命名
  function normalize(p) {
    const pc = p.prevClose || {}
    return {
      id: p.id,
      zh_id: p.id,
      name: p.name,
      userId: p.userId || '',      // EM rtV2 直连唯一入参（AQ5=A）
      watched: !!p.watched,
      today: p.today || null,
      total_return: pc.totalReturn ?? null,
      daily_return: pc.dailyReturn ?? null,
      monthly_return: pc.monthlyReturn ?? null,
      yearly_return: pc.yearlyReturn ?? null,
      net_value: pc.netValue ?? null,
      // 无来源字段一律 null：Failure ≠ Fact，禁止用 0 冒充缺失（页面显示 —）。
      // 周收益随 P3 契约下线，不再产出该字段。
      followers: null,
      max_drawdown: null,
      win_rate: null,
      days: null,
      labels: null,
      ranks: [],
      stocks: [],
      total_position: null,
      _total_position: null,
    }
  }

  const allPlayers = computed(() => (slices.players.value?.players || []).map(normalize))

  const currentDate = computed(() => slices.today.value?.date || slices.players.value?.date || '')
  // R5#1：采集时点改为 today.updatedAt（index.json.crawl_time 已随 P3 删除）
  const crawlTime = computed(() => slices.today.value?.updatedAt || '')
  const emContract = computed(() => slices.today.value?.emContract || { ok: false, checkedAt: '' })
  const tradedPlayerIds = computed(() => new Set(slices.today.value?.tradedPlayerIds || []))

  const watchedIds = computed(() => {
    const fromData = allPlayers.value.filter(p => p.watched).map(p => p.zh_id)
    return fromData.length ? new Set(fromData) : FALLBACK_WATCHED
  })

  const stockStats = computed(() => slices.stocks.value?.stats || [])
  const stocksSnapshot = computed(() => slices.stocks.value?.snapshot || '')
  const stocksUpdatedAt = computed(() => slices.stocks.value?.updatedAt || '')

  const sortedPlayers = computed(() => {
    const list = [...allPlayers.value]
    list.sort((a, b) => (b[sortKey.value] || 0) - (a[sortKey.value] || 0))
    const pinned = list.filter(p => watchedIds.value.has(p.zh_id))
    const rest = list.filter(p => !watchedIds.value.has(p.zh_id))
      .filter(p => !qualityOnly.value || isQuality(p))
    const rankMap = {}
    list.forEach((p, i) => { rankMap[p.zh_id] = i + 1 })
    return { pinned, rest, rankMap }
  })

  const fullRankPlayers = computed(() => allPlayers.value)

  const playerStyles = computed(() => {
    const map = {}
    for (const p of allPlayers.value) {
      map[p.zh_id] = { emoji: '⛵', label: '低频分散', tradeCount: 0, posCount: 1 }
    }
    return map
  })

  const playerLookup = computed(() => {
    const map = {}
    for (const p of allPlayers.value) map[p.zh_id] = p
    return map
  })

  async function ensureSlices(names) {
    await Promise.all([...new Set(names)].map(async (name) => {
      const r = slices[name]
      if (!r || r.value) return
      loading.value[name] = true
      try {
        r.value = await SLICE_LOADER[name]()
        sliceErrors.value = { ...sliceErrors.value, [name]: false }
      } catch (e) {
        sliceErrors.value = { ...sliceErrors.value, [name]: true }
        throw e
      } finally { loading.value[name] = false }
    }))
  }

  // R5#5：只等 today（最小分片，最早拿 emContract）
  async function loadData() {
    await ensureSlices(['today'])
  }

  function clearSlices() {
    for (const k in slices) slices[k].value = null
    sliceErrors.value = {}
  }

  return {
    loading, slices, sliceErrors, sliceLoaded,
    sortKey, qualityOnly, isQuality,
    allPlayers, sortedPlayers, fullRankPlayers, playerStyles, playerLookup,
    currentDate, crawlTime, emContract, tradedPlayerIds, watchedIds,
    stockStats, stocksSnapshot, stocksUpdatedAt,
    ensureSlices, loadData, clearSlices,
  }
}
