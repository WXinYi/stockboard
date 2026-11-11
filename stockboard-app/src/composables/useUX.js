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
const AUCTION_STAMP_KEY = '__last_auction_stamp'

// 北京时间 HHMM(用 en-GB + h23 避免 12/24 小时制与 locale 差异)
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
      const resp = await fetch(`${import.meta.env.BASE_URL}data/index.json`, { cache: 'no-cache' })
      const idx = await resp.json()
      const newTime = idx.crawl_time || ''
      const last = localStorage.getItem('__last_crawl_time')
      if (newTime && newTime !== last) {
        if (last) changed = true   // 首次加载只记基线, 不弹横幅
        localStorage.setItem('__last_crawl_time', newTime)
      }
    } catch { /* ignore */ }

    // 2026-11-11: 竞价班 09:25 独立部署 auction.json, 不跑 export_json、不写 index.json,
    // 因此只靠 crawl_time 探测不到竞价数据上线; AuctionTab 又被 KeepAlive 保留、只在
    // onMounted 拉一次 → 已打开页面会一直停在昨日快照。仅在盘前关键窗口(09:20-09:40)
    // 额外探测竞价快照, 变化即触发全局刷新(清缓存 + refreshTick, AuctionTab watch 重拉)。
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

  function dismiss() {
    updateAvailable.value = false
  }

  async function initCheck() {
    await check()
  }

  return { updateAvailable, dismiss, initCheck }
}
