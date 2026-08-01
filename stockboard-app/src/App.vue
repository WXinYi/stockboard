<script setup>
import { computed, onMounted, provide, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearDataCache } from './data/loader.js'
import { useData } from './composables/useData.js'
import { useHistory } from './composables/useHistory.js'
import { useRelativeTime } from './composables/useRelativeTime.js'
import { useDataRefresh } from './composables/useUX.js'
import NavBar from './components/NavBar.vue'

const stockData = useData()
const stockHistory = useHistory()

provide('stockData', stockData)
provide('stockHistory', stockHistory)

const { currentDate, loading, fullRankPlayers, crawlTime, loadData, ensureSlices } = stockData
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
// ⚠️ loading 是 ref，脚本内必须写 loading.value.core（Task 6 会在此基础上排除 refreshing）
const initialLoading = computed(() => loading.value.core && !isPlayerDetail.value)

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

// 数据更新 → 清缓存后重载 core + 当前路由分片（修复旧代码缓存不失效的 bug）
function refreshData() {
  dismiss()
  clearDataCache()
  loadData()
  ensureRoute()
}

// 路由级骨架屏：分片加载中显示（分片缓存后再次进入不触发）
const routeLoading = computed(() => {
  const key = route.path.startsWith('/player/') ? '/player' : route.path
  const m = ROUTE_SLICES[key] || { data: [] }
  return m.data.some((k) => loading.value[k])
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
          <span v-if="loading" class="skeleton" style="width:32px;height:10px;display:inline-block;vertical-align:middle;"></span>
        </div>
      </div>
    </header>

    <NavBar />

    <div v-if="updateAvailable" class="update-banner" @click="refreshData()">
      📊 数据已更新 · 点击刷新
    </div>

    <main class="main-content">
      <div v-if="initialLoading" class="loading-view">
        <div class="loading-spinner"></div>
        <p class="loading-text">正在加载数据…</p>
        <p class="loading-sub">从服务器获取最新行情</p>
      </div>
      <div v-else-if="routeLoading" class="loading-view">
        <div class="loading-spinner"></div>
        <p class="loading-text">加载中…</p>
      </div>
      <router-view v-else v-slot="{ Component }">
        <KeepAlive :exclude="['PlayerDetail']">
          <component :is="Component" />
        </KeepAlive>
      </router-view>
    </main>

    <footer class="footer">StockBoard · {{ currentDate || '—' }}</footer>
  </div>
</template>
