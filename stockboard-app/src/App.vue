<script setup>
import { onMounted, provide } from 'vue'
import { useData } from './composables/useData.js'
import { useHistory } from './composables/useHistory.js'
import NavBar from './components/NavBar.vue'

const stockData = useData()
const stockHistory = useHistory()

provide('stockData', stockData)
provide('stockHistory', stockHistory)

const { dates, currentDate, loading, fullRankPlayers, crawlTime, loadDates, loadDate } = stockData
const { loadHistory } = stockHistory

onMounted(async () => {
  await loadDates()
  await loadHistory()
})
</script>

<template>
  <div class="app">
    <header class="header">
      <h1>📊 数据看板</h1>
      <div class="subtitle">
        更新日期:
        <select class="date-select" v-model="currentDate" @change="loadDate(currentDate)">
          <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
        </select>
        <span v-if="crawlTime" style="font-size:11px;color:#aaa;">抓取时间: {{ crawlTime }}</span>
        数据来源: 东方财富
        <span v-if="fullRankPlayers.length" style="font-size:11px;color:#f1c40f;">{{ fullRankPlayers.length }}人五榜全上</span>
        <span v-if="loading" class="loading">加载中...</span>
      </div>
    </header>

    <NavBar />

    <main class="main-content">
      <router-view v-slot="{ Component, route: r }">
        <KeepAlive v-if="r.meta.keepAlive">
          <component :is="Component" />
        </KeepAlive>
        <component v-else :is="Component" />
      </router-view>
    </main>

    <footer class="footer">数据看板 · {{ currentDate || '—' }}</footer>
  </div>
</template>
