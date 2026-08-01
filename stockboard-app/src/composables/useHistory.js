import { ref, computed } from 'vue'
import { fetchChanges, fetchChangesSummary } from '../data/loader.js'

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
    loadChangesSummary, loadChanges,
  }
}
