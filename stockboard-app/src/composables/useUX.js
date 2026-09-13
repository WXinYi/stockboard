import { ref, onMounted, onUnmounted } from 'vue'

// 防抖
export function useDebounce(fn, delay = 300) {
  let timer = null
  return (...args) => {
    clearTimeout(timer)
    timer = setTimeout(() => fn(...args), delay)
  }
}

// 页面可见性 + 定时轮询检测新采集(2026-09-13 拍板①: 自动刷新) —
// 检测到新 crawl_time 时置 updateAvailable 并回调 onNewData(App 传 refreshData 自动应用);
// 轮询仅在页面可见时进行, 60s 一次, 避免后台烧请求
export function useDataRefresh(onNewData) {
  const updateAvailable = ref(false)
  let timer = null

  async function check() {
    try {
      const resp = await fetch(`${import.meta.env.BASE_URL}data/index.json`, { cache: 'no-cache' })
      const idx = await resp.json()
      const newTime = idx.crawl_time || ''
      if (newTime && newTime !== localStorage.getItem('__last_crawl_time')) {
        if (localStorage.getItem('__last_crawl_time')) {
          // 不是首次加载，真的更新了
          updateAvailable.value = true
          onNewData?.()
        }
        localStorage.setItem('__last_crawl_time', newTime)
      }
    } catch { /* ignore */ }
  }

  function onVisible() {
    if (document.visibilityState === 'visible') check()
  }

  onMounted(() => {
    document.addEventListener('visibilitychange', onVisible)
    timer = setInterval(() => {
      if (document.visibilityState === 'visible') check()
    }, 60_000)
  })

  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisible)
    if (timer) clearInterval(timer)
  })

  function dismiss() {
    updateAvailable.value = false
  }

  async function initCheck() {
    await check()
  }

  return { updateAvailable, dismiss, initCheck }
}
