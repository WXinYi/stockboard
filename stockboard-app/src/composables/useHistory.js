import { ref, computed } from 'vue'
import { fetchIndex, fetchDateData } from '../data/loader.js'

export function useHistory() {
  const historyLoaded = ref(false)
  const dateList = ref([])
  const allPlayersByDate = ref({})  // { '2026-07-22': [...players], ... }
  const allPositionsByDate = ref({})

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
        }
      }
      historyLoaded.value = true
    } catch (e) {
      console.warn('加载历史数据失败:', e.message)
    }
  }

  return { historyLoaded, dateList, positionChanges, getPlayerHistory, loadHistory }
}
