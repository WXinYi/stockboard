import { ref } from 'vue'
import { fetchChangesSummary } from '../data/loader.js'

export function useHistory() {
  const dateList = ref([])
  const changesSummary = ref(null)   // 摘要 counts（/copy）
  // 拉取失败标记(2026-09-15 审计 E3): 与"数据不足两天"是两回事,
  // 前端据此给"加载失败"而不是"需要至少2天数据"的错误归因
  const changesSummaryError = ref(false)

  // ═══════════════════════════════════════
  // 数据加载
  // ═══════════════════════════════════════

  // /copy 用：{ hasHistory, today, yesterday, addedCount, clearedCount, changeCount }
  async function loadChangesSummary() {
    try {
      const data = await fetchChangesSummary()
      changesSummary.value = data
      dateList.value = [data.yesterday, data.today].filter(Boolean)
      changesSummaryError.value = false
    } catch (e) {
      changesSummaryError.value = true
      changesSummary.value = null
      throw e
    }
  }

  return {
    dateList,
    changesSummary,
    changesSummaryError,
    loadChangesSummary,
  }
}
