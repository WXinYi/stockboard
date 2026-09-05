<script setup>
import { computed, onMounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearDataCache } from './data/loader.js'
import { useData } from './composables/useData.js'
import { useHistory } from './composables/useHistory.js'
import { useRelativeTime } from './composables/useRelativeTime.js'
import { useDataRefresh } from './composables/useUX.js'
import NavBar from './components/NavBar.vue'
import PullToRefresh from './components/PullToRefresh.vue'
import StockSearch from './components/StockSearch.vue'

const stockData = useData()
const stockHistory = useHistory()

provide('stockData', stockData)
provide('stockHistory', stockHistory)

// 刷新信号：refreshData() 递增，PlayerDetail watch 后重新拉取选手详情数据
const refreshTick = ref(0)
provide('refreshTick', refreshTick)

const { currentDate, loading, fullRankPlayers, crawlTime, loadData, ensureSlices, clearSlices } = stockData
const { loadChangesSummary } = stockHistory
const { relativeTime } = useRelativeTime()
const crawlTimeRelative = computed(() => relativeTime(crawlTime.value))

const route = useRoute()
const router = useRouter()

const pageTitles = {
  market: '盘面',
  copy: '抄作业',
  rankings: '排行榜',
  stocks: '重仓共识',
  auction: '竞价抢筹',
}
const marketSectionTitles = {
  auction: '竞价抢筹', wind: '最强风口', ladder: '涨停天梯', reasons: '涨停原因',
  newhighs: '百日新高', global: '外围市场', institution: '机构增仓',
  mood: '市场情绪', live: '盘面动态', lhb: '龙虎榜', discipline: '我的纪律卡',
}
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
const showSearch = ref(false)   // 全局股票搜索浮层(详情页顶栏窄, 不展示入口)

// 统一刷新入口：清缓存 → 重载 core + 当前路由分片 + 变化摘要
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

// 修正 Task 5 的 loading gating：loading 是 ref；刷新期间不闪全屏 loading
const initialLoading = computed(() => loading.value.core && !isDetailPage.value && !refreshing.value)

function goBack() {
  if (window.history.length > 2) router.back()
  else router.push('/market')
}

const { updateAvailable, initCheck, dismiss } = useDataRefresh()

// 路由 → 需要的分片（data 来自 useData，history 来自 useHistory）
const ROUTE_SLICES = {
  '/copy':     { data: ['copy', 'nameMap'],            history: 'summary' },
  '/rankings': { data: ['playersIndex'],               history: null },
  '/stocks':   { data: ['stocks', 'playersIndex'],     history: null },
  '/player':   { data: ['playersIndex'],               history: null },
}

async function ensureRoute() {
  const path = route.path
  const key = path.startsWith('/player/') ? '/player' : path
  const m = ROUTE_SLICES[key] || { data: [], history: null }
  await Promise.all([
    ensureSlices(m.data),
    m.history === 'summary' ? loadChangesSummary().catch(() => {}) : Promise.resolve(),
  ])
}

watch(() => route.path, () => { ensureRoute() })

onMounted(async () => {
  await loadData()          // 只等 core（~20KB），首屏尽快渲染
  ensureRoute()             // 当前 Tab 分片，不阻塞首屏
  initCheck()
})

</script>

<template>
  <div class="app">
    <header class="header">
      <div class="header-row">
        <div class="header-left">
          <button v-if="isDetailPage" class="back-btn" @click="goBack()">←</button>
          <span class="header-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <button v-if="!isDetailPage" class="refresh-btn" title="搜索股票" @click="showSearch = true">🔍</button>
          <span v-if="crawlTime" class="header-time-label">采集</span>
          <span v-if="crawlTime" class="header-time" :title="crawlTime">{{ crawlTimeRelative }}</span>
          <!-- 依赖 players_index 懒加载：首屏 /copy 不显示，进入榜单/重仓等页后出现 -->
          <span v-if="fullRankPlayers.length" class="header-badge">{{ fullRankPlayers.length }}人五榜</span>
          <span v-if="loading.core" class="skeleton" style="width:32px;height:10px;display:inline-block;vertical-align:middle;"></span>
          <button class="refresh-btn" :class="{ spinning: refreshing }"
                  @click="refreshData()" :disabled="refreshing" title="刷新最新数据">⟳</button>
        </div>
      </div>
    </header>

    <!-- 非一级页面(详情/嵌套页)不展示底部导航 -->
    <NavBar v-if="!isDetailPage" />

    <div v-if="updateAvailable" class="update-banner" @click="refreshData()">
      📊 数据已更新 · 点击刷新
    </div>

    <!-- detail-page: 无底部导航, 减少底部留白; stock-page: 股票详情全宽无左右留白 -->
    <main class="main-content" :class="{ 'detail-page': isDetailPage, 'stock-page': isStockDetail }">
      <PullToRefresh :refreshing="refreshing" @refresh="refreshData()">
        <div v-if="initialLoading" class="loading-view">
          <div class="loading-spinner"></div>
          <p class="loading-text">正在加载数据…</p>
          <p class="loading-sub">从服务器获取最新行情</p>
        </div>
        <router-view v-else v-slot="{ Component }">
          <!-- 全部页面 KeepAlive: 详情页返回保留状态不重载; 参数化页面各自 watch 参数重载 -->
          <KeepAlive>
            <component :is="Component" />
          </KeepAlive>
        </router-view>
      </PullToRefresh>
    </main>

    <footer class="footer">StockBoard · {{ currentDate || '—' }}</footer>

    <!-- 全局股票搜索浮层 -->
    <StockSearch v-if="showSearch" @close="showSearch = false" />
  </div>
</template>
