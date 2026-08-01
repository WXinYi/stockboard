import { ref, computed } from 'vue'
import { fetchChanges, fetchChangesSummary, fetchPlayerHistory } from '../data/loader.js'

export function useHistory() {
  const historyLoaded = ref(false)
  const dateList = ref([])
  const changesData = ref(null)      // 完整 changes（/tracking）
  const changesSummary = ref(null)   // 摘要 counts（/copy）
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

  async function loadPlayerHistory(zhId, force = false) {
    if (!force && playerHistoryCache.value[zhId]) return playerHistoryCache.value[zhId]
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

  // /copy 用：{ hasHistory, today, yesterday, addedCount, clearedCount, changeCount }
  async function loadChangesSummary() {
    const data = await fetchChangesSummary()
    changesSummary.value = data
    dateList.value = [data.yesterday, data.today].filter(Boolean)
  }

  // /tracking 用：完整 { changes, alerts }
  async function loadChanges() {
    const data = await fetchChanges()
    changesData.value = data.changes
    alerts.value = data.alerts || { highByStock: [], mid: [], totalClear: 0 }
    historyLoaded.value = true
  }

  return {
    historyLoaded, dateList,
    positionChanges, changesSummary, alerts,
    getPlayerHistory, loadPlayerHistory,
    loadChangesSummary, loadChanges,
  }
}
