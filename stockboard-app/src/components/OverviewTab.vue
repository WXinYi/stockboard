<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Chart, DoughnutController, ArcElement, BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend } from 'chart.js'

Chart.register(DoughnutController, ArcElement, BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend)

const router = useRouter()
const { sortedPlayers: players, positionDist: dist, tradedPlayerIds, stockStats, tradeConsensus, isQuality } = inject('stockData')

const allPlayers = computed(() => [...players.value.pinned, ...players.value.rest])

const dailyTop = computed(() =>
  [...allPlayers.value].sort((a, b) => (b.daily_return || 0) - (a.daily_return || 0)).slice(0, 10)
)
const totalTop = computed(() =>
  [...allPlayers.value].sort((a, b) => (b.total_return || 0) - (a.total_return || 0)).slice(0, 10)
)

const distCanvas = ref(null)
const profitCanvas = ref(null)
let distChart = null, profitChart = null

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  const cls = n >= 0 ? 'positive' : 'negative'
  const sign = n >= 0 ? '+' : ''
  return `<span class="${cls}">${sign}${n.toFixed(2)}%</span>`
}

function renderCharts() {
  if (distChart) distChart.destroy()
  if (profitChart) profitChart.destroy()

  const d = dist.value
  const labels = ['9成以上','7-9成','5-7成','3-5成','1-3成','1成以下','空仓']
  const colors = ['#e74c3c','#e67e22','#f39c12','#f1c40f','#2ecc71','#3498db','#95a5a6']
  const data = labels.map(l => d[l] || 0)
  const total = data.reduce((a, b) => a + b, 0)

  distChart = new Chart(distCanvas.value, {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'right',
          labels: {
            generateLabels(chart) {
              return chart.data.labels.map((label, i) => {
                const v = chart.data.datasets[0].data[i]
                const p = total > 0 ? ((v / total) * 100).toFixed(1) + '%' : '0%'
                return { text: label + '  ' + p, fillStyle: chart.data.datasets[0].backgroundColor[i], index: i, strokeStyle: '#fff' }
              })
            }
          }
        }
      }
    }
  })

  // 盈亏分布 - 从 positions 生成
  const allPositions = allPlayers.value.flatMap(p => p._positions || [])
  const bins = { '<-10%': 0, '-10~0%': 0, '0~10%': 0, '10~20%': 0, '>20%': 0 }
  allPositions.forEach(x => {
    const r = x.profit_ratio || 0
    if (r < -10) bins['<-10%']++
    else if (r < 0) bins['-10~0%']++
    else if (r < 10) bins['0~10%']++
    else if (r < 20) bins['10~20%']++
    else bins['>20%']++
  })
  profitChart = new Chart(profitCanvas.value, {
    type: 'bar',
    data: { labels: Object.keys(bins), datasets: [{ label: '持仓数量', data: Object.values(bins), backgroundColor: '#2980b9', borderRadius: 6 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } }
  })
}

onMounted(() => {
  if (distCanvas.value) renderCharts()
})
watch(() => dist.value, () => renderCharts(), { deep: true })
</script>

<template>
  <div class="stats-row">
    <div class="stat-card"><div class="stat-val">{{ allPlayers.length }}</div><div class="stat-lbl">选手总数</div></div>
    <div class="stat-card"><div class="stat-val">{{ stockStats.reduce((s,x)=>s+x.count,0) }}</div><div class="stat-lbl">持仓记录</div></div>
    <div class="stat-card"><div class="stat-val">{{ tradeConsensus.reduce((s,x)=>s+x.buy_count+x.sell_count,0) }}</div><div class="stat-lbl">调仓笔数</div></div>
    <div class="stat-card"><div class="stat-val">{{ stockStats.length }}</div><div class="stat-lbl">涉及标的</div></div>
  </div>
  <div class="grid-2">
    <div class="card">
      <h2>仓位分布</h2>
      <div class="chart-wrap"><canvas ref="distCanvas"></canvas></div>
    </div>
    <div class="card">
      <h2>日收益 Top 10</h2>
      <table><thead><tr><th>#</th><th>选手</th><th>日收益</th><th>总收益</th></tr></thead>
        <tbody>
          <tr v-for="(p, i) in dailyTop" :key="p.zh_id">
            <td>{{ i + 1 }}</td>
            <td><a href="#" @click.prevent="router.push('/player/' + p.zh_id)" style="color:#2980b9;text-decoration:none;">{{ p.name || p.zh_id }}<span v-if="isQuality(p)"> 🏅</span></a><span v-if="tradedPlayerIds && tradedPlayerIds.has(p.zh_id)" class="trade-dot" title="今日有调仓"></span></td>
            <td v-html="pct(p.daily_return)"></td>
            <td v-html="pct(p.total_return)"></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  <div class="grid-2">
    <div class="card">
      <h2>盈亏分布</h2>
      <div class="chart-wrap"><canvas ref="profitCanvas"></canvas></div>
    </div>
    <div class="card">
      <h2>总收益 Top 10</h2>
      <table><thead><tr><th>#</th><th>选手</th><th>总收益</th><th>日收益</th><th>净值</th></tr></thead>
        <tbody>
          <tr v-for="(p, i) in totalTop" :key="p.zh_id">
            <td>{{ i + 1 }}</td>
            <td><a href="#" @click.prevent="router.push('/player/' + p.zh_id)" style="color:#2980b9;text-decoration:none;">{{ p.name || p.zh_id }}<span v-if="isQuality(p)"> 🏅</span></a><span v-if="tradedPlayerIds && tradedPlayerIds.has(p.zh_id)" class="trade-dot" title="今日有调仓"></span></td>
            <td v-html="pct(p.total_return)"></td>
            <td v-html="pct(p.daily_return)"></td>
            <td>{{ (p.net_value || 0).toFixed(3) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
