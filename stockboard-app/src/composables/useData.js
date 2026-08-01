import { ref, computed } from 'vue'
import {
  fetchCore, fetchCopy, fetchStocks, fetchTrades, fetchSectors,
  fetchCompare, fetchOverview, fetchNameMap, fetchPlayersIndex,
} from '../data/loader.js'

const WATCHED_IDS = new Set(['900240956', '900354116', '900438148', '900376763', '900013608', '900429191', '900369020', '900223455'])

// 分片 ref 表 + 加载器表（ensureSlices 用）
const SLICE_REF = {
  core: 'core', copy: 'copy', stocks: 'stocks', trades: 'trades',
  sectors: 'sectors', compare: 'compare', overview: 'overview',
  nameMap: 'nameMap', playersIndex: 'playersIndex',
}
const SLICE_LOADER = {
  core: fetchCore, copy: fetchCopy, stocks: fetchStocks, trades: fetchTrades,
  sectors: fetchSectors, compare: fetchCompare, overview: fetchOverview,
  nameMap: fetchNameMap, playersIndex: fetchPlayersIndex,
}

export function useData() {
  const loading = ref({
    core: false, copy: false, stocks: false, trades: false,
    sectors: false, compare: false, overview: false,
    nameMap: false, playersIndex: false,
  })
  const slices = {
    core: ref(null), copy: ref(null), stocks: ref(null), trades: ref(null),
    sectors: ref(null), compare: ref(null), overview: ref(null),
    nameMap: ref(null), playersIndex: ref(null),
  }

  // 筛选状态
  const sortKey = ref('total_return')
  const qualityOnly = ref(false)

  // ═══════════════════════════════════════
  // 高手判定
  // ═══════════════════════════════════════
  function isQuality(p) {
    if (p.quality !== undefined) return p.quality
    const days = p.days || 0
    if (days < 200) return false
    const daily = p.daily_return || 0
    const weekly = p.weekly_return || 0
    const monthly = p.monthly_return || 0
    const yearly = p.yearly_return || 0
    const recentScore = monthly * 0.5 + weekly * 0.3 + daily * 0.2
    const longTermScore = yearly * 0.6 + recentScore * 0.4
    const drawdown = Math.abs(p.max_drawdown || 0)
    if (drawdown < 0.01) return longTermScore > 0
    return (longTermScore / drawdown) >= 0.15
  }

  // ═══════════════════════════════════════
  // 字段归一化：id → zh_id, total_position → _total_position
  // ═══════════════════════════════════════
  function normalize(p) {
    const [i, n, f, T, d, w, m, y, v, dd, wr, dy, lb, rk, tp, q, ss] = p
    return {
      id: i, name: n, followers: f,
      total_return: T, daily_return: d,
      weekly_return: w, monthly_return: m, yearly_return: y,
      net_value: v, max_drawdown: dd, win_rate: wr,
      days: dy, labels: lb, ranks: rk,
      total_position: tp, quality: q, stocks: ss,
      zh_id: i,
      _total_position: tp ?? 0,
    }
  }

  // ═══════════════════════════════════════
  // 派生数据
  // ═══════════════════════════════════════

  // ══ 来自 core.json ══
  const currentDate = computed(() => slices.core.value?.date || '')
  const crawlTime = computed(() => slices.core.value?.crawl_time || '')
  const qualityPlayerCount = computed(() => slices.core.value?.qualityPlayerCount || 0)
  const tradedPlayerIds = computed(() => new Set(slices.core.value?.tradedPlayerIds || []))

  // ══ 来自 copy.json ══
  const copyTradeSignals = computed(() => slices.copy.value?.copyTradeSignals || { bs: [], ch: [], sw: [], hq: [] })
  const tradeAlerts = computed(() => slices.copy.value?.tradeAlerts || [])
  const suspectedClears = computed(() => slices.copy.value?.suspectedClears || [])

  // ══ 来自 stocks.json / trades.json / sectors.json ══
  const stockStats = computed(() => slices.stocks.value?.stockStats || [])
  const tradeConsensus = computed(() => slices.trades.value?.tradeConsensus || [])
  const sectorStats = computed(() => slices.sectors.value?.sectorStats || [])

  // ══ 来自 compare.json ══
  const stockCompare = computed(() => slices.compare.value?.stockCompare || { concentration: [], divergence: [], qualityCount: 0 })

  // ══ 来自 overview.json ══
  const positionDist = computed(() => slices.overview.value?.positionDist || {})
  const profitDist = computed(() => slices.overview.value?.profitDist || {})

  // ══ 来自 name_map.json + players_index 兜底 ══
  const playerNameMap = computed(() => {
    const map = { ...(slices.nameMap.value || {}) }
    if (slices.playersIndex.value) {
      for (const p of slices.playersIndex.value) map[p[0]] = p[0]
    }
    return map
  })

  // ═══════════════════════════════════════
  // 选手列表 + 排序
  // ═══════════════════════════════════════
  const allPlayers = computed(() => {
    if (!slices.playersIndex.value) return []
    return slices.playersIndex.value.map(normalize)
  })

  const sortedPlayers = computed(() => {
    const list = [...allPlayers.value]
    list.sort((a, b) => (b[sortKey.value] || 0) - (a[sortKey.value] || 0))
    const pinned = list.filter(p => WATCHED_IDS.has(p.zh_id))
    const rest = list.filter(p => !WATCHED_IDS.has(p.zh_id))
      .filter(p => !qualityOnly.value || isQuality(p))
    const rankMap = {}
    list.forEach((p, i) => { rankMap[p.zh_id] = i + 1 })
    return { pinned, rest, rankMap }
  })

  const fullRankPlayers = computed(() => {
    return allPlayers.value.filter(p => (p.ranks || []).length >= 5)
  })

  // ═══════════════════════════════════════
  // 操作风格（轻量计算）
  // ═══════════════════════════════════════
  const playerStyles = computed(() => {
    const map = {}
    for (const p of allPlayers.value) {
      const tradeCount = 0  // summary 不含调仓数，用 0
      const posCount = (p.labels?.length || 0) + 1
      const freq = tradeCount > 5 ? '高频' : '低频'
      const conc = posCount <= 2 ? '集中' : '分散'
      let emoji, label
      if (freq === '高频' && conc === '集中') { emoji = '🎯'; label = '高频集中' }
      else if (freq === '高频' && conc === '分散') { emoji = '🔄'; label = '高频分散' }
      else if (freq === '低频' && conc === '集中') { emoji = '🐢'; label = '低频集中' }
      else { emoji = '⛵'; label = '低频分散' }
      map[p.zh_id] = { emoji, label, tradeCount, posCount }
    }
    return map
  })

  // ═══════════════════════════════════════
  // 选手详情辅助：按 zh_id 快速查询基本信息
  // ═══════════════════════════════════════
  const playerLookup = computed(() => {
    const map = {}
    if (!slices.playersIndex.value) return map
    for (const p of allPlayers.value) {
      map[p.zh_id] = p
    }
    return map
  })

  // ═══════════════════════════════════════
  // 按需加载
  // ═══════════════════════════════════════

  async function ensureSlices(names) {
    await Promise.all([...new Set(names)].map(async (name) => {
      const r = slices[name]
      if (!r || r.value) return
      loading.value[name] = true
      try { r.value = await SLICE_LOADER[name]() }
      finally { loading.value[name] = false }
    }))
  }

  // App 挂载时只等 core（~20KB），其余分片由路由触发
  async function loadData() {
    await ensureSlices(['core'])
  }

  return {
    currentDate, loading, crawlTime,
    sortedPlayers, stockStats, tradeConsensus, positionDist, profitDist,
    sortKey, qualityOnly, isQuality,
    playerStyles, sectorStats, fullRankPlayers, copyTradeSignals, stockCompare,
    qualityPlayerCount, tradedPlayerIds, tradeAlerts, suspectedClears, playerNameMap,
    playerLookup,
    ensureSlices, loadData,
  }
}
