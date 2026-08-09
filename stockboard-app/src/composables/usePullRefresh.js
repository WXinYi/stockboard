import { inject, onActivated, onDeactivated, onMounted, ref, watch } from 'vue'

// 下拉刷新订阅: 只让「当前激活」的 KeepAlive 页面响应 refreshTick
// KeepAlive 缓存的所有页面都会收到 App 的 refreshTick 广播, 若全部响应会在
// 任意页面下拉时触发大量无关接口请求(如天梯页下拉 → 盘面概览 6 接口 + 股票详情 6 接口)
// → 用 onActivated/onDeactivated 维护激活态, 非激活页面跳过重载
export function usePullRefresh(loadFn) {
  const active = ref(false)
  const refreshTick = inject('refreshTick', ref(0))
  onMounted(() => { active.value = true })   // 首次挂载即激活(keepAlive 下 mounted 后必触发 activated)
  onActivated(() => { active.value = true })
  onDeactivated(() => { active.value = false })
  watch(refreshTick, () => { if (active.value && loadFn) loadFn() })
}
