<script setup>
import { computed, onMounted, provide } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useData } from './composables/useData.js'
import { useHistory } from './composables/useHistory.js'
import NavBar from './components/NavBar.vue'

const stockData = useData()
const stockHistory = useHistory()

provide('stockData', stockData)
provide('stockHistory', stockHistory)

const { currentDate, loading, fullRankPlayers, crawlTime, loadData } = stockData
const { loadHistory } = stockHistory

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
const initialLoading = computed(() => loading.value && !isPlayerDetail.value)

onMounted(async () => {
  await loadData()
  loadHistory()
})
</script>

<template>
  <div class="app">
    <header class="header">
      <div class="header-row">
        <div class="header-left">
          <button v-if="isPlayerDetail" class="back-btn" @click="window.history.length > 1 ? router.back() : router.push('/copy')">←</button>
          <span class="header-title">{{ pageTitle }}</span>
        </div>
        <div class="header-right">
          <span v-if="crawlTime" class="header-time-label">采集</span>
          <span v-if="crawlTime" class="header-time">{{ crawlTime }}</span>
          <span v-if="fullRankPlayers.length" class="header-badge">{{ fullRankPlayers.length }}人五榜</span>
          <span v-if="loading" class="skeleton" style="width:32px;height:10px;display:inline-block;vertical-align:middle;"></span>
        </div>
      </div>
    </header>

    <NavBar />

    <main class="main-content">
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
    </main>

    <footer class="footer">StockBoard · {{ currentDate || '—' }}</footer>
  </div>
</template>
