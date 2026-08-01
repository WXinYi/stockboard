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

const stockData = useData()
const stockHistory = useHistory()

provide('stockData', stockData)
provide('stockHistory', stockHistory)

// 刷新信号：refreshData() 递增，PlayerDetail watch 后重新拉取选手详情数据
const refreshTick = ref(0)
provide('refreshTick', refreshTick)

const { currentDate, loading, fullRankPlayers, crawlTime, loadData, ensureSlices, clearSlices } = stockData
const { loadChangesSummary, loadChanges } = stockHistory
const { relativeTime } = useRelativeTime()
const crawlTimeRelative = computed(() => relativeTime(crawlTime.value))

const route = useRoute()
const router = useRouter()

const pageTitles = {
  copy: '抄作业',
  overview: '总览',
  rankings: '排行榜',
  stocks: '重仓共识',
  sectors: '行业板块',
  trades: '调仓共识',
  compare: '多空对比',
  tracking: '变动追踪',
}
const pageTitle = computed(() => {
  if (route.path.startsWith('/player/')) return '选手详情'
  return pageTitles[route.path.slice(1)] || ''
})
const isPlayerDetail = computed(() => route.path.startsWith('/player/'))

const refreshing = ref(false)

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
const initialLoading = computed(() => loading.value.core && !isPlayerDetail.value && !refreshing.value)

function goBack() {
  if (window.history.length > 2) router.back()
  else router.push('/copy')
}

const { updateAvailable, initCheck, dismiss } = useDataRefresh()

// 路由 → 需要的分片（data 来自 useData，history 来自 useHistory）
const ROUTE_SLICES = {
  '/copy':     { data: ['copy', 'nameMap'],            history: 'summary' },
  '/overview': { data: ['overview', 'stocks', 'trades', 'playersIndex'], history: null },
  '/rankings': { data: ['playersIndex'],               history: null },
  '/stocks':   { data: ['stocks', 'playersIndex'],     history: null },
  '/sectors':  { data: ['sectors'],                    history: null },
  '/trades':   { data: ['trades', 'nameMap'],          history: null },
  '/compare':  { data: ['compare'],                    history: null },
  '/tracking': { data: [],                             history: 'full' },
  '/player':   { data: ['playersIndex'],               history: null },
}

async function ensureRoute() {
  const path = route.path
  const key = path.startsWith('/player/') ? '/player' : path
  const m = ROUTE_SLICES[key] || { data: [], history: null }
  await Promise.all([
    ensureSlices(m.data),
    m.history === 'summary' ? loadChangesSummary().catch(() => {})
      : m.history === 'full' ? loadChanges().catch(() => {}) : Promise.resolve(),
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
          <button v-if="isPlayerDetail" class="back-btn" @click="goBack()">←</button>
          <span class="header-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
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

    <NavBar />

    <div v-if="updateAvailable" class="update-banner" @click="refreshData()">
      📊 数据已更新 · 点击刷新
    </div>

    <main class="main-content">
      <PullToRefresh :refreshing="refreshing" @refresh="refreshData()">
        <div v-if="initialLoading" class="loading-view">
          <div class="loading-spinner"></div>
          <p class="loading-text">正在加载数据…</p>
          <p class="loading-sub">从服务器获取最新行情</p>
        </div>
        <router-view v-else v-slot="{ Component }">
          <KeepAlive :exclude="['PlayerDetail']">
            <component :is="Component" />
          </KeepAlive>
        </router-view>
      </PullToRefresh>
    </main>

    <footer class="footer">StockBoard · {{ currentDate || '—' }}</footer>
  </div>
</template>
