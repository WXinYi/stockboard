import { ref, computed } from 'vue'
import { fetchSummary } from '../data/loader.js'

const WATCHED_IDS = new Set(['900240956', '900354116'])

export function useData() {
  const crawlTime = ref('')
  const loading = ref(false)
  const _summary = ref(null)

  // 筛选状态
  const sortKey = ref('total_return')
  const qualityOnly = ref(false)

  // ═══════════════════════════════════════
  // 派生数据
  // ═══════════════════════════════════════
  const currentDate = computed(() => _summary.value?.date || '')

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
    return {
      ...p,
      zh_id: p.id || p.zh_id,
      _total_position: p.total_position ?? p._total_position ?? 0,
    }
  }

  // ═══════════════════════════════════════
  // 选手列表 + 排序
  // ═══════════════════════════════════════
  const allPlayers = computed(() => {
    if (!_summary.value) return []
    return _summary.value.players.map(normalize)
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

  // ═══════════════════════════════════════
  // 直接来自 summary 的计算
  // ═══════════════════════════════════════

  const stockStats = computed(() => _summary.value?.stockStats || [])
  const tradeConsensus = computed(() => _summary.value?.tradeConsensus || [])
  const sectorStats = computed(() => _summary.value?.sectorStats || [])
  const positionDist = computed(() => _summary.value?.positionDist || {})
  const profitDist = computed(() => _summary.value?.profitDist || {})
  const copyTradeSignals = computed(() => _summary.value?.copyTradeSignals || {
    buySignals: [], coreHoldings: [], sellWarnings: [], highQuality: [],
  })
  const stockCompare = computed(() => _summary.value?.stockCompare || {
    concentration: [], divergence: [], qualityCount: 0,
  })
  const tradeAlerts = computed(() => _summary.value?.tradeAlerts || [])
  const suspectedClears = computed(() => _summary.value?.suspectedClears || [])
  const qualityPlayerCount = computed(() => _summary.value?.qualityPlayerCount || 0)

  const tradedPlayerIds = computed(() => {
    const ids = _summary.value?.tradedPlayerIds || []
    return new Set(ids)
  })

  const fullRankPlayers = computed(() => {
    return allPlayers.value.filter(p => (p.ranks || []).length >= 5)
  })

  const playerNameMap = computed(() => {
    if (!_summary.value) return {}
    const map = { ..._summary.value.playerNameMap }
    for (const p of _summary.value.players) {
      map[p.id] = p.id
    }
    return map
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
  // 数据加载
  // ═══════════════════════════════════════

  async function loadData() {
    loading.value = true
    try {
      const s = await fetchSummary()
      _summary.value = s
      crawlTime.value = s.crawl_time || ''
    } catch (e) {
      console.error('加载数据失败:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    currentDate, loading, crawlTime,
    sortedPlayers, stockStats, tradeConsensus, positionDist, profitDist,
    sortKey, qualityOnly, isQuality,
    playerStyles, sectorStats, fullRankPlayers, copyTradeSignals, stockCompare,
    qualityPlayerCount, tradedPlayerIds, tradeAlerts, suspectedClears, playerNameMap,
    loadData,
  }
}
