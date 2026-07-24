<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'

const router = useRouter()
const { stockStats: stats, sortedPlayers: sp, isQuality } = inject('stockData')
function navigateToPlayer(id) { router.push('/player/' + id) }

const stockSearch = ref('')
const lookedUpHolders = ref(null)
const allPlayers = computed(() => [...sp.value.pinned, ...sp.value.rest])

const { sorted: sortedStats, toggle: tog, indicator: ind } = useTableSort(computed(() => stats.value), 'total_position')

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}

function lookupStock() {
  const q = stockSearch.value.trim()
  if (!q) { lookedUpHolders.value = null; return }
  const holders = allPlayers.value.filter(p => {
    return (p.stocks || []).some(code => code.includes(q))
  })
  lookedUpHolders.value = holders.sort((a, b) => (b._total_position || 0) - (a._total_position || 0))
}

const chartCanvas = ref(null)
let chart = null
onMounted(async () => {
  if (!chartCanvas.value) return
  const top10 = stats.value.slice(0, 10)
  const { Chart, BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend } = await import('chart.js')
  Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend)
  chart = new Chart(chartCanvas.value, {
    type: 'bar', data: {
      labels: top10.map(s => s.name),
      datasets: [{ label: '持有人数', data: top10.map(s => s.holders), backgroundColor: '#8e44ad', borderRadius: 6 }]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } }, scales: { x: { ticks: { stepSize: 1 } } }
    }
  })
})
</script>

<template>
  <div class="card" style="margin-bottom:20px;">
    <h2>🔍 反向查股票</h2>
    <p class="hint">输入股票代码或名称，查看哪些选手持有它</p>
    <div class="search-box" style="display:flex;gap:8px;">
      <input type="text" v-model="stockSearch" placeholder="例: 000938 或 紫光股份" @keyup.enter="lookupStock" style="flex:1;" />
      <button @click="lookupStock" style="padding:8px 16px;background:#2980b9;color:white;border:none;border-radius:8px;cursor:pointer;font-size:13px;">查询</button>
    </div>
    <div v-if="lookedUpHolders !== null" style="margin-top:12px;">
      <p v-if="!lookedUpHolders.length" class="empty-state">未找到持有该股票的选手</p>
      <div v-else>
        <p style="font-size:12px;color:#888;margin-bottom:8px;">{{ lookedUpHolders.length }} 人持有:</p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <span v-for="p in lookedUpHolders" :key="p.zh_id" style="background:#f0f2f5;border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer;" @click="navigateToPlayer(p.zh_id)">
            {{ p.name || p.zh_id }}<span v-if="isQuality(p)"> 🏅</span> <span style="color:#888;">{{ (p._total_position || 0).toFixed(0) }}%仓位</span>
          </span>
        </div>
      </div>
    </div>
  </div>

  <div class="grid-2">
    <div class="card">
      <h2>重仓共识 <span class="badge">Top 20</span></h2>
      <p class="hint">按加权总仓位排序，点击表头可切换排序</p>
      <div style="max-height:500px;overflow-y:auto;">
        <table><thead><tr>
          <th>#</th><th>股票</th><th>代码</th>
          <th style="cursor:pointer;" @click="tog('holders')">持有人{{ ind('holders') }}</th>
          <th style="cursor:pointer;" @click="tog('total_position')">总仓位{{ ind('total_position') }}</th>
          <th style="cursor:pointer;" @click="tog('avg_profit')">平均盈亏{{ ind('avg_profit') }}</th>
        </tr></thead>
          <tbody>
            <tr v-for="(s, i) in sortedStats.slice(0,20)" :key="s.code">
              <td>{{ i + 1 }}</td>
              <td><strong>{{ s.name }}</strong></td>
              <td style="color:#999;">{{ s.code }}</td>
              <td>{{ s.holders }}人</td>
              <td>
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, s.total_position/s.count) + '%' }"></span></span>
                {{ s.total_position.toFixed(0) }}%
              </td>
              <td v-html="pct(s.total_profit/s.count)"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <h2>重仓共识图表</h2>
      <div class="chart-wrap tall"><canvas ref="chartCanvas"></canvas></div>
    </div>
  </div>
</template>
