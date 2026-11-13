import { ref, onMounted, onUnmounted } from 'vue'

// 防抖
export function useDebounce(fn, delay = 300) {
  let timer = null
  return (...args) => {
    clearTimeout(timer)
    timer = setTimeout(() => fn(...args), delay)
  }
}

// R5#8：刷新探测唯一真源改为 today.json.updatedAt（index.json.crawl_time 已随 P3 删除）；
// 09:20–09:40 的 auction.json 探测保留（竞价班独立部署，不写 today.json）。
const AUCTION_STAMP_KEY = '__last_auction_stamp'
const TODAY_STAMP_KEY = '__last_today_updated_at'

function beijingHHMM() {
  try {
    const s = new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).format(new Date())
    const m = s.match(/(\d{2}):(\d{2})/)
    return m ? Number(m[1]) * 100 + Number(m[2]) : -1
  } catch { return -1 }
}

export function useDataRefresh(onNewData) {
  const updateAvailable = ref(false)
  let timer = null

  async function check() {
    let changed = false
    try {
      const resp = await fetch(`${import.meta.env.BASE_URL}data/latest/today.json`, { cache: 'no-cache' })
      const today = await resp.json()
      const newTime = today.updatedAt || today.date || ''
      const last = localStorage.getItem(TODAY_STAMP_KEY)
      if (newTime && newTime !== last) {
        if (last) changed = true   // 首次加载只记基线, 不弹横幅
        localStorage.setItem(TODAY_STAMP_KEY, newTime)
      }
    } catch { /* 分片缺失：交由 sliceErrors 显性化，不在此静默重试 */ }

    const hhmm = beijingHHMM()
    if (hhmm >= 920 && hhmm <= 940) {
      try {
        const r = await fetch(`${import.meta.env.BASE_URL}data/latest/auction.json`, { cache: 'no-store' })
        const a = await r.json()
        const stamp = `${a.date || ''}|${a.generated_at || ''}`
        const lastStamp = localStorage.getItem(AUCTION_STAMP_KEY)
        if (stamp && stamp !== lastStamp) {
          if (lastStamp) changed = true
          localStorage.setItem(AUCTION_STAMP_KEY, stamp)
        }
      } catch { /* ignore */ }
    }

    if (changed) {
      updateAvailable.value = true
      onNewData?.()
    }
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

  function dismiss() { updateAvailable.value = false }
  async function initCheck() { await check() }

  return { updateAvailable, dismiss, initCheck }
}
