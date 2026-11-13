<script setup>
import { computed, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearDataCache } from './data/loader.js'
import { useDataV2 } from './composables/useDataV2.js'
import { useRelativeTime } from './composables/useRelativeTime.js'
import { useDataRefresh } from './composables/useUX.js'
import { getLatestTradingDay } from './composables/useKplApi.js'
import { MARKET_SECTION_TITLES } from './utils/sectionTitles.js'
import NavBar from './components/NavBar.vue'
import PullToRefresh from './components/PullToRefresh.vue'
import StockSearch from './components/StockSearch.vue'

// P3 / AQ3=B：单一新数据层，4 契约；useData / useHistory / core.json 已下线
const stockData = useDataV2()
provide('stockData', stockData)

// 刷新信号：refreshData() 递增，PlayerDetail watch 后重新直连 EM
const refreshTick = ref(0)
provide('refreshTick', refreshTick)

const { currentDate, loading, fullRankPlayers, crawlTime, loadData, ensureSlices, clearSlices } = stockData
const { relativeTime } = useRelativeTime()
const crawlTimeRelative = computed(() => relativeTime(crawlTime.value))
// 数据时点显式化：today.json.updatedAt 是唯一真源（R5#1/#8）
const crawlTimeShort = computed(() => {
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(crawlTime.value || '')
  return m ? `${m[2]}-${m[3]} ${m[4]}:${m[5]}` : ''
})

const route = useRoute()
const router = useRouter()

const pageTitles = {
  market: '选股',
  rankings: '排行榜',
  stocks: '重仓共识',
  auction: '竞价抢筹',
}
const marketSectionTitles = MARKET_SECTION_TITLES
const pageTitle = computed(() => {
  if (route.path.startsWith('/player/')) return '选手详情'
  if (route.path.startsWith('/stock/')) return '股票详情'
  if (route.path.startsWith('/info/')) return '资讯详情'
  if (route.path.startsWith('/market/')) return marketSectionTitles[route.path.slice(8)] || '盘面详情'
  if (route.path.startsWith('/board/')) return '板块详情'
  return pageTitles[route.path.slice(1)] || ''
})
const isPlayerDetail = computed(() => route.path.startsWith('/player/'))
const isStockDetail = computed(() => route.path.startsWith('/stock/'))
const isDetailPage = computed(() => isPlayerDetail.value || isStockDetail.value || route.path.startsWith('/info/') || route.path.startsWith('/board/') || route.path.startsWith('/market/'))

const refreshing = ref(false)
const showSearch = ref(false)

async function refreshData() {
  if (refreshing.value) return
  refreshing.value = true
  dismiss()
  clearDataCache()
  clearSlices()
  try {
    await Promise.all([loadData(), ensureRoute()])
    refreshTick.value++
  } finally {
    refreshing.value = false
  }
}

const initialLoading = computed(() => loading.value.today && !isDetailPage.value && !refreshing.value)

function goBack() {
  if (window.history.length > 2) router.back()
  else router.push('/market')
}

const { updateAvailable, initCheck, dismiss } = useDataRefresh(() => { refreshData() })

// 路由 → 需要的分片（全部来自 4 契约；/copy 与 useHistory 已移除）
const ROUTE_SLICES = {
  '/rankings': ['players', 'today'],
  '/stocks': ['stocks', 'players'],
  '/player': ['players'],
}

async function ensureRoute() {
  const key = route.path.startsWith('/player/') ? '/player' : route.path
  // 分片缺失/失败由 sliceErrors 显性化，不抛未捕获 rejection（4 契约尚未产出时页面显示空态而非白屏）
  try { await ensureSlices(ROUTE_SLICES[key] || []) } catch { /* marked in sliceErrors */ }
}

watch(() => route.path, () => { ensureRoute() })

const tradingDay = ref('')
const crawlDay = computed(() => (currentDate.value || '').slice(0, 10))

onMounted(async () => {
  await loadData()          // R5#5：只等 today.json（最小分片，最早拿 emContract）
  ensureRoute()
  initCheck()
  getLatestTradingDay().then(d => { if (d && d.length === 8) tradingDay.value = `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` }).catch(() => {})
})

</script>

<template>
  <div class="app">
    <header class="header">
      <div class="header-row">
        <div class="header-left">
          <span class="brand-mark" aria-hidden="true">SB</span>
          <button v-if="isDetailPage" class="back-btn" @click="goBack()">←</button>
          <span class="header-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <button v-if="!isDetailPage" class="refresh-btn" title="搜索股票" @click="showSearch = true">🔍</button>
          <span v-if="crawlTime" class="header-time-label">数据时点</span>
          <span v-if="crawlTime" class="header-time" :title="crawlTime">{{ crawlTimeRelative }}</span>
          <span v-if="fullRankPlayers.length" class="header-badge">{{ fullRankPlayers.length }} 名选手</span>
          <span v-if="loading.today" class="skeleton" style="width:32px;height:10px;display:inline-block;vertical-align:middle;"></span>
          <button class="refresh-btn" :class="{ spinning: refreshing }"
                  @click="refreshData()" :disabled="refreshing" title="刷新最新数据">⟳</button>
        </div>
      </div>
    </header>

    <NavBar v-if="!isDetailPage" />

    <div v-if="updateAvailable" class="update-banner" @click="refreshData()">
      📊 检测到新采集 · 正在自动刷新（点击手动重刷）
    </div>

    <main class="main-content" :class="{ 'detail-page': isDetailPage, 'stock-page': isStockDetail }">
      <PullToRefresh :refreshing="refreshing" @refresh="refreshData()">
        <div v-if="initialLoading" class="loading-view">
          <div class="loading-spinner"></div>
          <p class="loading-text">正在加载数据…</p>
          <p class="loading-sub">读取今日快照</p>
        </div>
        <router-view v-else v-slot="{ Component }">
          <KeepAlive>
            <component :is="Component" />
          </KeepAlive>
        </router-view>
      </PullToRefresh>
    </main>

    <footer class="footer" :title="`数据时点 ${crawlTime || '—'} · 选手持仓/收益为最近收盘校准值(盘中不刷新, 15:15 后更新当日) · 竞价为 09:25 快照`">
      StockBoard · {{ crawlTimeShort || currentDate || '—' }}<template v-if="tradingDay && tradingDay !== crawlDay"> · 数据截至 {{ tradingDay }}</template>
    </footer>

    <StockSearch v-if="showSearch" @close="showSearch = false" />
  </div>
</template>
