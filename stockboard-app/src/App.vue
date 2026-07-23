<script setup>
import { onMounted, ref } from 'vue'
import { useData } from './composables/useData.js'
import { useHistory } from './composables/useHistory.js'
import HeaderStats from './components/HeaderStats.vue'
import OverviewTab from './components/OverviewTab.vue'
import RankingsTab from './components/RankingsTab.vue'
import StockTab from './components/StockTab.vue'
import TradeTab from './components/TradeTab.vue'
import SectorTab from './components/SectorTab.vue'
import CopyTradeTab from './components/CopyTradeTab.vue'
import CompareTab from './components/CompareTab.vue'
import PositionTracking from './components/PositionTracking.vue'
import PlayerDetail from './components/PlayerDetail.vue'

const { dates, currentDate, loading, sortedPlayers, stockStats, tradeConsensus, positionDist, playerStyles, sectorStats, fullRankPlayers, copyTradeSignals, stockCompare, tradedPlayerIds, playerNameMap, loadDates, loadDate } = useData()
const { positionChanges, getPlayerHistory, loadHistory } = useHistory()

const tabs = [
  { key: 'copy', label: '💡 抄作业' },
  { key: 'overview', label: '📋 总览' },
  { key: 'rankings', label: '🏆 排行榜' },
  { key: 'stocks', label: '📈 重仓共识' },
  { key: 'sectors', label: '🏭 行业板块' },
  { key: 'trades', label: '🤝 调仓共识' },
  { key: 'compare', label: '📊 多空对比' },
  { key: 'tracking', label: '📅 变动追踪' },
]
const activeTab = ref('overview')
const detailPlayer = ref(null)
const playerReturnHistory = ref([])

function showPlayer(id) {
  const all = [...sortedPlayers.value.pinned, ...sortedPlayers.value.rest]
  detailPlayer.value = all.find(x => x.zh_id === id) || null
  playerReturnHistory.value = getPlayerHistory(id)
}
function backToList() {
  detailPlayer.value = null
  activeTab.value = 'rankings'
}
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
        数据来源: 东方财富
        <span v-if="fullRankPlayers.length" style="font-size:11px;color:#f1c40f;">{{ fullRankPlayers.length }}人五榜全上</span>
        <span v-if="loading" class="loading">加载中...</span>
      </div>
    </header>

    <HeaderStats :count="sortedPlayers.rest.length + sortedPlayers.pinned.length"
                  :pos-count="stockStats.reduce((s,x)=>s+x.count,0)"
                  :trade-count="tradeConsensus.reduce((s,x)=>s+x.buy_count+x.sell_count,0)"
                  :stock-count="stockStats.length" />

    <div class="container">
      <nav class="tabs" v-if="!detailPlayer">
        <button v-for="t in tabs" :key="t.key"
                :class="['tab', { active: activeTab === t.key }]"
                @click="activeTab = t.key">{{ t.label }}</button>
      </nav>

      <div v-if="detailPlayer">
        <button class="back-btn" @click="backToList">← 返回列表</button>
        <PlayerDetail :player="detailPlayer" :history="playerReturnHistory" @show-player="showPlayer" />
      </div>

      <template v-else>
        <CopyTradeTab v-if="activeTab === 'copy'" :signals="copyTradeSignals" :player-ids="playerNameMap" @show-player="showPlayer" />
        <OverviewTab v-else-if="activeTab === 'overview'" :dist="positionDist" :players="sortedPlayers" :traded-player-ids="tradedPlayerIds" />
        <RankingsTab v-else-if="activeTab === 'rankings'" :sorted="sortedPlayers" :styles="playerStyles" :traded-player-ids="tradedPlayerIds" @show-player="showPlayer" />
        <StockTab v-else-if="activeTab === 'stocks'" :stats="stockStats" :all-players="sortedPlayers" @show-player="showPlayer" />
        <SectorTab v-else-if="activeTab === 'sectors'" :sectors="sectorStats" />
        <TradeTab v-else-if="activeTab === 'trades'" :consensus="tradeConsensus" :player-ids="playerNameMap" @show-player="showPlayer" />
        <CompareTab v-else-if="activeTab === 'compare'" :compare="stockCompare" />
        <PositionTracking v-else-if="activeTab === 'tracking'" :changes="positionChanges" @show-player="showPlayer" />
      </template>
    </div>

    <footer class="footer">数据看板 · {{ currentDate || '—' }}</footer>
  </div>
</template>
