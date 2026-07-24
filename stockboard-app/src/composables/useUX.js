import { ref, onMounted, onUnmounted } from 'vue'

// 防抖
export function useDebounce(fn, delay = 300) {
  let timer = null
  return (...args) => {
    clearTimeout(timer)
    timer = setTimeout(() => fn(...args), delay)
  }
}

// 页面可见性检测 — 切回页面时检查是否有新数据
export function useDataRefresh(onNewData) {
  const updateAvailable = ref(false)

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
  })

  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisible)
  })

  function dismiss() {
    updateAvailable.value = false
  }

  async function initCheck() {
    await check()
  }

  return { updateAvailable, dismiss, initCheck }
}
