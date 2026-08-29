import { ref, computed } from 'vue'
import {
  fetchCore, fetchCopy, fetchStocks, fetchNameMap, fetchPlayersIndex,
} from '../data/loader.js'

// 置顶 = 钉钉推送的 10 名选手(2026-08-30 对齐 main.py WATCHED_PLAYERS + notify_daily DRAGON)
const WATCHED_IDS = new Set(['900456476', '900450475', '900351276', '900401128', '900422074', '900443192', '900315547', '900240956', '900376763', '900439290'])

// 分片 ref 表 + 加载器表（ensureSlices 用）
const SLICE_REF = {
  core: 'core', copy: 'copy', stocks: 'stocks',
  nameMap: 'nameMap', playersIndex: 'playersIndex',
}
const SLICE_LOADER = {
  core: fetchCore, copy: fetchCopy, stocks: fetchStocks,
  nameMap: fetchNameMap, playersIndex: fetchPlayersIndex,
}

export function useData() {
  const loading = ref({
    core: false, copy: false, stocks: false,
    nameMap: false, playersIndex: false,
  })
  const slices = {
    core: ref(null), copy: ref(null), stocks: ref(null),
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
      days: dy,
      // labels 已从数组瘦身为数量（兼容旧数据仍为数组）
      labels: Array.isArray(lb) ? lb.length : (lb || 0),
      ranks: rk,
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

  // ══ 来自 stocks.json ══
  const stockStats = computed(() => slices.stocks.value?.stockStats || [])

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
      const posCount = (p.labels || 0) + 1
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

  // 清空所有分片（配合 refreshData：置 null 后 ensureSlices 才会真正重新拉取）
  function clearSlices() {
    for (const k in slices) slices[k].value = null
  }

  return {
    currentDate, loading, crawlTime,
    sortedPlayers, stockStats,
    sortKey, qualityOnly, isQuality,
    playerStyles, fullRankPlayers, copyTradeSignals,
    qualityPlayerCount, tradedPlayerIds, tradeAlerts, suspectedClears, playerNameMap,
    playerLookup,
    ensureSlices, loadData, clearSlices,
  }
}
