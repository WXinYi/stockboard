import { ref, computed } from 'vue'
import { fetchIndex, fetchDateData } from '../data/loader.js'

export function useHistory() {
  const historyLoaded = ref(false)
  const dateList = ref([])
  const allPlayersByDate = ref({})  // { '2026-07-22': [...players], ... }
  const allPositionsByDate = ref({})
  const allTradesByDate = ref({})

  // 持仓变化（最新两天对比）
  const positionChanges = computed(() => {
    const dates = dateList.value
    if (dates.length < 2) return { hasHistory: false, changes: [] }
    const today = dates[dates.length - 1]
    const yesterday = dates[dates.length - 2]
    const todayPos = allPositionsByDate.value[today] || []
    const yesterdayPos = allPositionsByDate.value[yesterday] || []

    // 构建查找
    const yesterdayMap = {}  // { zh_id: { code: position_ratio } }
    for (const p of yesterdayPos) {
      (yesterdayMap[p.zh_id] ||= {})[p.stock_code] = p
    }
    const todayMap = {}
    for (const p of todayPos) {
      (todayMap[p.zh_id] ||= {})[p.stock_code] = p
    }

    const todayPlayers = allPlayersByDate.value[today] || []
    const playerNames = {}
    for (const p of todayPlayers) {
      playerNames[p.zh_id] = p.name || p.zh_id
    }

    const changes = []
    // 遍历每个选手
    for (const zh_id of new Set([...Object.keys(todayMap), ...Object.keys(yesterdayMap)])) {
      const todayStocks = todayMap[zh_id] || {}
      const yesterdayStocks = yesterdayMap[zh_id] || {}
      const allCodes = new Set([...Object.keys(todayStocks), ...Object.keys(yesterdayStocks)])

      for (const code of allCodes) {
        const t = todayStocks[code]
        const y = yesterdayStocks[code]
        const todayRatio = t ? (t.position_ratio || 0) : 0
        const yesterdayRatio = y ? (y.position_ratio || 0) : 0
        const delta = todayRatio - yesterdayRatio

        if (Math.abs(delta) < 1) continue  // 忽略微小变化
        let type, emoji
        if (!y && t) { type = '新进'; emoji = '🆕' }
        else if (y && !t) { type = '清仓'; emoji = '🚫' }
        else if (delta > 0) { type = '加仓'; emoji = '📈' }
        else { type = '减仓'; emoji = '📉' }

        changes.push({
          zh_id, player_name: playerNames[zh_id] || zh_id,
          stock_code: code, stock_name: (t || y).stock_name || '',
          type, emoji, delta,
          yesterdayRatio, todayRatio,
        })
      }
    }
    return {
      hasHistory: true,
      yesterday, today,
      changes: changes.sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta)),
      added: changes.filter(c => c.type === '新进'),
      cleared: changes.filter(c => c.type === '清仓'),
    }
  })

  // 指定选手的收益历史
  function getPlayerHistory(zhId) {
    const result = []
    for (const date of dateList.value) {
      const players = allPlayersByDate.value[date] || []
      const p = players.find(x => x.zh_id === zhId)
      if (p) {
        result.push({
          date,
          daily_return: p.daily_return || 0,
          total_return: p.total_return || 0,
          net_value: p.net_value || 0,
        })
      }
    }
    return result.sort((a, b) => a.date.localeCompare(b.date))
  }

  async function loadHistory() {
    try {
      const idx = await fetchIndex()
      dateList.value = idx.dates || []
      // 并行加载所有日期的数据
      const results = await Promise.all(
        dateList.value.map(d => fetchDateData(d).catch(() => null))
      )
      for (let i = 0; i < dateList.value.length; i++) {
        if (results[i]) {
          allPlayersByDate.value[dateList.value[i]] = results[i].players
          allPositionsByDate.value[dateList.value[i]] = results[i].positions
          allTradesByDate.value[dateList.value[i]] = results[i].trades
        }
      }
      historyLoaded.value = true
    } catch (e) {
      console.warn('加载历史数据失败:', e.message)
    }
  }

  // 异常检测：清仓 + 大幅减仓
  const alerts = computed(() => {
    const ch = positionChanges.value
    if (!ch || !ch.changes) return { highByStock: [], mid: [] }
    const stockMap = {}
    const mid = []
    for (const c of ch.changes) {
      if (c.type === '清仓') {
        if (!stockMap[c.stock_code]) stockMap[c.stock_code] = { stock_name: c.stock_name, stock_code: c.stock_code, players: [] }
        stockMap[c.stock_code].players.push({ name: c.player_name, zh_id: c.zh_id })
      } else if (c.delta < -30) {
        mid.push({ player_name: c.player_name, zh_id: c.zh_id, stock_name: c.stock_name, stock_code: c.stock_code })
      }
    }
    const highByStock = Object.values(stockMap).sort((a, b) => b.players.length - a.players.length)
    return { highByStock, mid, totalClear: highByStock.reduce((s, x) => s + x.players.length, 0) }
  })

  // 聚合所有日期的调仓记录
  function getPlayerAllTrades(zhId) {
    const result = []
    const seen = new Set()
    for (const date of dateList.value) {
      const trades = allTradesByDate.value[date] || []
      for (const t of trades) {
        if (t.zh_id !== zhId) continue
        const key = t.trade_date + '_' + t.stock_code + '_' + t.direction
        if (seen.has(key)) continue
        seen.add(key)
        result.push({ ...t, crawl_date: date })
      }
    }
    return result.sort((a, b) => b.trade_date.localeCompare(a.trade_date) || b.crawl_date.localeCompare(a.crawl_date))
  }

  // 推测持仓：基于调仓记录推算可能还持有的股票
  function getInferredPositions(zhId, confirmedCodes) {
    const stockState = {}  // code -> { buys: [{level, date}], sells: [{level, date}] }
    for (const date of dateList.value) {
      const trades = allTradesByDate.value[date] || []
      for (const t of trades) {
        if (t.zh_id !== zhId) continue
        if (confirmedCodes && confirmedCodes.has(t.stock_code)) continue
        const code = t.stock_code
        if (!stockState[code]) stockState[code] = { stock_name: t.stock_name, stock_code: code, buys: [], sells: [] }
        const level = t.position_ratio
        if (t.direction === '买入') {
          stockState[code].buys.push({ level: level || '?', date: t.trade_date })
        } else {
          stockState[code].sells.push({ level: level || '?', date: t.trade_date })
        }
      }
    }
    // Build result
    const result = []
    for (const [code, s] of Object.entries(stockState)) {
      // Skip if no buys
      if (s.buys.length === 0) continue
      const hasSells = s.sells.length > 0
      // Find latest buy level and latest sell level
      const latestBuy = s.buys.sort((a,b) => b.date.localeCompare(a.date))[0]
      const latestSell = s.sells.sort((a,b) => b.date.localeCompare(a.date))[0]
      
      let confidence = 'low'
      let status = '可能持有'
      
      if (!hasSells) {
        // Only bought, never sold
        status = '持续买入'
        confidence = s.buys.length >= 2 ? 'mid' : 'low'
      } else if (latestBuy.date > latestSell.date) {
        // Latest action is buy
        status = '近期加仓'
        confidence = 'mid'
      } else if (latestSell.date > latestBuy.date && latestBuy.level === latestSell.level) {
        // Sold at same level as bought — likely cleared
        continue // skip
      } else {
        // Sold but level decreased, may still hold
        status = '可能减持'
        confidence = 'low'
      }
      
      result.push({
        stock_name: s.stock_name,
        stock_code: code,
        level_estimate: latestBuy.level || '?',
        status,
        confidence,
        buy_count: s.buys.length,
        sell_count: s.sells.length,
      })
    }
    return result.sort((a, b) => a.confidence === 'mid' ? -1 : 1)
  }

  return { historyLoaded, dateList, positionChanges, alerts, getPlayerHistory, getPlayerAllTrades, getInferredPositions, loadHistory }
}
