import { ref, computed, watch } from 'vue'
import { fetchIndex, fetchDateData } from '../data/loader.js'

// 置顶关注的选手
const WATCHED_IDS = new Set(['900240956'])

export function useData() {
  const dates = ref([])
  const currentDate = ref('')
  const crawlTime = ref('')
  const loading = ref(false)
  const players = ref([])
  const positions = ref([])
  const trades = ref([])

  // 原始数据（未筛选）
  const rawPlayers = ref([])
  const rawPositions = ref([])
  const rawTrades = ref([])

  // 筛选状态
  const sortKey = ref('total_return')
  const qualityOnly = ref(false)

  function isQuality(p) {
    // 科学筛选：时间加权 + 动量趋势 + 风险调整
    // 近期表现权重更高（月>周>日），结合长期年收益，除以回撤做风险调整
    const days = p.days || 0
    if (days < 200) return false

    const daily = p.daily_return || 0
    const weekly = p.weekly_return || 0
    const monthly = p.monthly_return || 0
    const yearly = p.yearly_return || 0

    // 近端得分：月×0.5 + 周×0.3 + 日×0.2 （近期眼光更重）
    const recentScore = monthly * 0.5 + weekly * 0.3 + daily * 0.2
    // 长期得分：年×0.6 + 近端×0.4 （长短期平衡）
    const longTermScore = yearly * 0.6 + recentScore * 0.4

    const drawdown = Math.abs(p.max_drawdown || 0)
    if (drawdown < 0.01) return longTermScore > 0

    // 风险调整：每承担1%回撤能获得多少收益
    const riskAdjusted = longTermScore / drawdown
    return riskAdjusted >= 0.15
  }

  // 排序后的选手（置顶优先）
  const sortedPlayers = computed(() => {
    const list = [...rawPlayers.value]
    list.sort((a, b) => (b[sortKey.value] || 0) - (a[sortKey.value] || 0))

    const pinned = list.filter(p => WATCHED_IDS.has(p.zh_id))
    const rest = list.filter(p => !WATCHED_IDS.has(p.zh_id)).filter(p => !qualityOnly.value || isQuality(p))

    // 计算真实排名
    const rankMap = {}
    list.forEach((p, i) => { rankMap[p.zh_id] = i + 1 })

    return { pinned, rest, rankMap }
  })

  // 股票统计（按加权总仓位排序）
  const stockStats = computed(() => {
    const map = {}
    for (const p of rawPositions.value) {
      const code = p.stock_code
      if (!code) continue
      if (!map[code]) {
        map[code] = { code, name: p.stock_name, holders: 0, total_position: 0, total_profit: 0, count: 0 }
      }
      map[code].holders++
      map[code].total_position += p.position_ratio || 0
      map[code].total_profit += p.profit_ratio || 0
      map[code].count++
    }
    for (const s of Object.values(map)) {
      s.avg_profit = s.count > 0 ? s.total_profit / s.count : 0
    }
    return Object.values(map).sort((a, b) => b.total_position - a.total_position)
  })

  // 调仓共识（仅当日实际交易）
  const tradeConsensus = computed(() => {
    const map = {}
    const today = currentDate.value
    for (const t of rawTrades.value) {
      if (t.trade_date !== today) continue
      const code = t.stock_code
      if (!code) continue
      if (!map[code]) {
        map[code] = { code, name: t.stock_name, buy_players: [], sell_players: [], buy_count: 0, sell_count: 0 }
      }
      if (t.direction === '买入') {
        map[code].buy_count += t.trades_count || 1
        const pn = t.player_name || t.zh_id || ''
        if (!map[code].buy_players.includes(pn)) {
          map[code].buy_players.push(pn)
        }
      } else {
        map[code].sell_count += t.trades_count || 1
        const pn = t.player_name || t.zh_id || ''
        if (!map[code].sell_players.includes(pn)) {
          map[code].sell_players.push(pn)
        }
      }
    }
    const result = Object.values(map).sort((a, b) =>
      (b.buy_players.length + b.sell_players.length) - (a.buy_players.length + a.sell_players.length)
    )
    // 共识强度分级
    for (const item of result) {
      const n = item.buy_players.length + item.sell_players.length
      if (n >= 10) { item.strength = '🔥 强烈'; item.strengthColor = '#e74c3c' }
      else if (n >= 5) { item.strength = '📈 一般'; item.strengthColor = '#e67e22' }
      else if (n >= 2) { item.strength = '💡 微弱'; item.strengthColor = '#2980b9' }
      else { item.strength = '单一'; item.strengthColor = '#999' }
    }
    return result
  })

  // 操作风格分类
  const playerStyles = computed(() => {
    const map = {}
    for (const p of rawPlayers.value) {
      const tradeCount = (p._trades || []).length
      const posCount = (p._positions || []).length
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

  // 行业板块分析
  const sectorStats = computed(() => {
    const map = {}
    for (const p of rawPlayers.value) {
      const labels = p.labels || []
      if (!labels.length) continue
      for (const lb of labels) {
        if (!map[lb]) {
          map[lb] = { name: lb, count: 0, total_return: 0, daily_return: 0, winRate: 0, total_position: 0, qualityCount: 0 }
        }
        map[lb].count++
        map[lb].total_return += p.total_return || 0
        map[lb].daily_return += p.daily_return || 0
        map[lb].total_position += p._total_position || 0
        if (isQuality(p)) map[lb].qualityCount++
      }
    }
    return Object.values(map).map(s => ({
      ...s,
      avg_return: s.total_return / s.count,
      avg_daily: s.daily_return / s.count,
      avg_position: s.total_position / s.count,
    })).sort((a, b) => b.count - a.count)
  })

  // 五榜全上选手
  const fullRankPlayers = computed(() => {
    return rawPlayers.value.filter(p => (p.ranks || []).length >= 5)
  })

  // ===== 抄作业信号（只看高质量选手） =====
  const copyTradeSignals = computed(() => {
    const qualityPlayers = {}
    for (const p of rawPlayers.value) {
      if (isQuality(p)) qualityPlayers[p.zh_id] = p
    }

    const stockSignals = {}
    // 持仓信号：高手持有加分
    for (const pos of rawPositions.value) {
      if (!qualityPlayers[pos.zh_id]) continue
      const code = pos.stock_code
      if (!code) continue
      if (!stockSignals[code]) {
        stockSignals[code] = { code, name: pos.stock_name || '', score: 0, holders: [], buyers: [], sellers: [], totalPosition: 0, holderCount: 0 }
      }
      const ratio = (pos.position_ratio || 0) / 100
      stockSignals[code].score += ratio
      stockSignals[code].totalPosition += pos.position_ratio || 0
      stockSignals[code].holderCount++
      const pn = qualityPlayers[pos.zh_id].name || pos.zh_id
      if (!stockSignals[code].holders.includes(pn)) {
        stockSignals[code].holders.push(pn)
      }
    }

    // 调仓信号：高手买入大幅加分，卖出扣分（仅当日实际交易）
    const today = currentDate.value
    for (const t of rawTrades.value) {
      if (t.trade_date !== today) continue
      if (!qualityPlayers[t.zh_id]) continue
      const code = t.stock_code
      if (!code) continue
      if (!stockSignals[code]) {
        stockSignals[code] = { code, name: t.stock_name || '', score: 0, holders: [], buyers: [], sellers: [], totalPosition: 0, holderCount: 0 }
      }
      const weight = (t.trades_count || 1) * 2
      const pn = qualityPlayers[t.zh_id].name || t.zh_id
      if (t.direction === '买入') {
        stockSignals[code].score += weight
        if (!stockSignals[code].buyers.includes(pn)) {
          stockSignals[code].buyers.push(pn)
        }
      } else {
        stockSignals[code].score -= weight
        if (!stockSignals[code].sellers.includes(pn)) {
          stockSignals[code].sellers.push(pn)
        }
      }
    }

    const all = Object.values(stockSignals)
    return {
      // 买入信号：有高手在买 + 得分高
      buySignals: all.filter(s => s.buyers.length > 0).sort((a, b) => b.score - a.score),
      // 核心重仓：高手集中持有
      coreHoldings: all.filter(s => s.holderCount >= 2).sort((a, b) => b.totalPosition - a.totalPosition),
      // 卖出预警：高手在卖出
      sellWarnings: all.filter(s => s.sellers.length > 0 && s.buyers.length === 0).sort((a, b) => b.sellers.length - a.sellers.length),
      highQuality: all.filter(s => s.score >= 3).sort((a, b) => b.score - a.score),
    }
  })

  // ===== 多空对比（不做结论，只客观对比） =====
  const stockCompare = computed(() => {
    const qualitySet = {}
    for (const p of rawPlayers.value) {
      if (isQuality(p)) qualitySet[p.zh_id] = p
    }
    const stockMap = {}
    for (const pos of rawPositions.value) {
      const code = pos.stock_code
      if (!code) continue
      if (!stockMap[code]) {
        stockMap[code] = { code, name: pos.stock_name || '', totalHolders: 0, totalPosition: 0, qualityHolders: 0, allBuying: 0, allSelling: 0, qualityBuying: 0, qualitySelling: 0 }
      }
      stockMap[code].totalHolders++
      stockMap[code].totalPosition += pos.position_ratio || 0
      if (qualitySet[pos.zh_id]) stockMap[code].qualityHolders++
    }
    const today2 = currentDate.value
    for (const t of rawTrades.value) {
      if (t.trade_date !== today2) continue
      const code = t.stock_code
      if (!code || !stockMap[code]) continue
      if (t.direction === '买入') {
        stockMap[code].allBuying++
        if (qualitySet[t.zh_id]) stockMap[code].qualityBuying++
      } else {
        stockMap[code].allSelling++
        if (qualitySet[t.zh_id]) stockMap[code].qualitySelling++
      }
    }
    const all = Object.values(stockMap)
    return {
      // 持仓拥挤度：按总持有人数排序
      concentration: [...all].sort((a, b) => b.totalHolders - a.totalHolders).slice(0, 30),
      // 分歧度：高手买卖差 vs 全体买卖差，差距最大的排前面
      divergence: all.map(s => {
        const allNet = s.allBuying - s.allSelling
        const qualityNet = s.qualityBuying - s.qualitySelling
        return { ...s, allNet, qualityNet, gap: Math.abs(allNet - qualityNet) }
      }).filter(s => s.totalHolders >= 3).sort((a, b) => b.gap - a.gap).slice(0, 20),
      qualityCount: Object.keys(qualitySet).length,
    }
  })

  // 今日有调仓的选手 ID 集合（按 trade_date 过滤，非 crawl_date）
  const tradedPlayerIds = computed(() => {
    const ids = new Set()
    const today = currentDate.value
    for (const t of rawTrades.value) {
      if (t.zh_id && t.trade_date === today) ids.add(t.zh_id)
    }
    return ids
  })

  // 选手名 → zh_id 映射（用于 TradeTab/CopyTradeTab 名称反查）
  const playerNameMap = computed(() => {
    const map = {}
    for (const p of rawPlayers.value) {
      map[p.name || p.zh_id] = p.zh_id
      map[p.zh_id] = p.zh_id
    }
    return map
  })

  // 仓位分布
  const positionDist = computed(() => {
    const dist = { '9成以上': 0, '7-9成': 0, '5-7成': 0, '3-5成': 0, '1-3成': 0, '1成以下': 0, '空仓': 0 }
    const playerPos = {}
    for (const p of rawPositions.value) {
      playerPos[p.zh_id] = (playerPos[p.zh_id] || 0) + (p.position_ratio || 0)
    }
    // 也计入没有持仓数据的选手（空仓）
    for (const p of rawPlayers.value) {
      const total = playerPos[p.zh_id] || 0
      if (total === 0) dist['空仓']++
      else if (total < 10) dist['1成以下']++
      else if (total < 30) dist['1-3成']++
      else if (total < 50) dist['3-5成']++
      else if (total < 70) dist['5-7成']++
      else if (total < 90) dist['7-9成']++
      else dist['9成以上']++
    }
    return dist
  })

  async function loadDates() {
    try {
      const idx = await fetchIndex()
      dates.value = idx.dates || []
      crawlTime.value = idx.crawl_time || ''
      if (dates.value.length > 0) {
        currentDate.value = dates.value[dates.value.length - 1]
        await loadDate(currentDate.value)
      }
    } catch (e) {
      console.warn('无法加载日期索引，使用空数据:', e.message)
    }
  }

  async function loadDate(date) {
    loading.value = true
    try {
      const data = await fetchDateData(date)
      rawPlayers.value = data.players
      rawPositions.value = data.positions
      rawTrades.value = data.trades
      currentDate.value = date

      // 按选手分组
      const posMap = {}
      const tradeMap = {}
      for (const p of data.positions) {
        (posMap[p.zh_id] ||= []).push(p)
      }
      for (const t of data.trades) {
        (tradeMap[t.zh_id] ||= []).push(t)
      }
      for (const p of data.players) {
        p._positions = posMap[p.zh_id] || []
        p._trades = tradeMap[p.zh_id] || []
        p._total_position = p._positions.reduce((s, x) => s + (x.position_ratio || 0), 0)
        // 解析 labels/ranks
        for (const key of ['labels', 'ranks']) {
          if (typeof p[key] === 'string') {
            try { p[key] = JSON.parse(p[key]) } catch { p[key] = [] }
          }
        }
      }
    } catch (e) {
      console.error('加载数据失败:', e)
    } finally {
      loading.value = false
    }
  }

  return {
    dates, currentDate, loading,
    sortedPlayers, stockStats, tradeConsensus, positionDist,
    sortKey, qualityOnly, isQuality,
    playerStyles, sectorStats, fullRankPlayers, copyTradeSignals, stockCompare,
    tradedPlayerIds, playerNameMap,
    crawlTime, loadDates, loadDate,
  }
}
