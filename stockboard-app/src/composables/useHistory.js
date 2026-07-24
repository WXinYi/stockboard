import { ref, computed } from 'vue'
import { fetchChanges, fetchPlayerHistory } from '../data/loader.js'

export function useHistory() {
  const historyLoaded = ref(false)
  const dateList = ref([])
  const changesData = ref(null)
  const alerts = ref({ highByStock: [], mid: [], totalClear: 0 })

  // ═══════════════════════════════════════
  // 持仓变动
  // ═══════════════════════════════════════
  const positionChanges = computed(() => {
    return changesData.value || { hasHistory: false, changes: [] }
  })

  // ═══════════════════════════════════════
  // 选手历史时间序列（按需加载）
  // ═══════════════════════════════════════
  const playerHistoryCache = ref({})

  function getPlayerHistory(zhId) {
    return playerHistoryCache.value[zhId] || []
  }

  async function loadPlayerHistory(zhId) {
    if (playerHistoryCache.value[zhId]) return playerHistoryCache.value[zhId]
    try {
      const entries = await fetchPlayerHistory(zhId)
      const converted = entries.map(e => ({
        date: e.d,
        daily_return: e.dr || 0,
        total_return: e.tr || 0,
        net_value: e.nv || 0,
      }))
      playerHistoryCache.value[zhId] = converted
      return converted
    } catch (e) {
      console.warn(`选手 ${zhId} 历史数据加载失败:`, e.message)
      return []
    }
  }

  // ═══════════════════════════════════════
  // 数据加载
  // ═══════════════════════════════════════
  async function loadHistory() {
    try {
      const data = await fetchChanges()
      changesData.value = data.changes
      alerts.value = data.alerts || { highByStock: [], mid: [], totalClear: 0 }
      if (data.changes && data.changes.today) {
        dateList.value = [data.changes.yesterday, data.changes.today].filter(Boolean)
      }
      historyLoaded.value = true
    } catch (e) {
      console.warn('加载变动数据失败:', e.message)
    }
  }

  return {
    historyLoaded, dateList,
    positionChanges, alerts,
    getPlayerHistory, loadPlayerHistory,
    loadHistory,
  }
}
