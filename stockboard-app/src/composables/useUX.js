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

function beijingNow() {
  try {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Shanghai', weekday: 'short',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date()).map(x => [x.type, x.value]))
    return { weekday: p.weekday || '', hhmm: Number(p.hour) * 100 + Number(p.minute) }
  } catch { return { weekday: '', hhmm: -1 } }
}

// 探测窗口：北京工作日 09:00–15:30（覆盖 09:26 rank 至 15:15 eod）。
// 收盘后/周末 today.json 不会被采集班改写，定时探测纯属无效请求。
export function inProbeWindow(now = beijingNow()) {
  return now.weekday !== 'Sat' && now.weekday !== 'Sun' && now.hhmm >= 900 && now.hhmm <= 1530
}

export function useDataRefresh(onNewData) {
  const updateAvailable = ref(false)
  let timer = null

  async function check(snapshot = null) {
    let changed = false
    try {
      // 首屏传入 loadData() 已取到的 today 快照，避免再发一次请求；定时探测才走网络。
      let today = snapshot
      if (!today) {
        const resp = await fetch(`${import.meta.env.BASE_URL}data/latest/today.json`, { cache: 'no-cache' })
        today = await resp.json()
      }
      const newTime = today.updatedAt || today.date || ''
      const last = localStorage.getItem(TODAY_STAMP_KEY)
      if (newTime && newTime !== last) {
        if (last) changed = true   // 首次加载只记基线, 不弹横幅
        localStorage.setItem(TODAY_STAMP_KEY, newTime)
      }
    } catch { /* 分片缺失：交由 sliceErrors 显性化，不在此静默重试 */ }

    const { hhmm } = beijingNow()
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
      // 非探测窗口不轮询；切回标签页仍由 onVisible 补一次，保证新部署不会漏。
      if (document.visibilityState !== 'visible' || !inProbeWindow()) return
      check()
    }, 60_000)
  })

  onUnmounted(() => {
    document.removeEventListener('visibilitychange', onVisible)
    if (timer) clearInterval(timer)
  })

  function dismiss() { updateAvailable.value = false }
  async function initCheck(snapshot) { await check(snapshot) }

  return { updateAvailable, dismiss, initCheck }
}
