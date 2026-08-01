import { ref } from 'vue'
import { fetchChangesSummary } from '../data/loader.js'

export function useHistory() {
  const dateList = ref([])
  const changesSummary = ref(null)   // 摘要 counts（/copy）

  // ═══════════════════════════════════════
  // 数据加载
  // ═══════════════════════════════════════

  // /copy 用：{ hasHistory, today, yesterday, addedCount, clearedCount, changeCount }
  async function loadChangesSummary() {
    const data = await fetchChangesSummary()
    changesSummary.value = data
    dateList.value = [data.yesterday, data.today].filter(Boolean)
  }

  return {
    dateList,
    changesSummary,
    loadChangesSummary,
  }
}
