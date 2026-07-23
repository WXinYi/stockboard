<script setup>
import { computed, inject, onMounted, ref } from 'vue'
import { Chart, BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend } from 'chart.js'
import { useTableSort } from '../composables/useTableSort.js'
Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip, Legend)

const { sectorStats: sectors } = inject('stockData')

const secData = computed(() => sectors.value)
const { sorted, toggle: tog, indicator: ind } = useTableSort(secData, 'count')

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}

const chartCanvas = ref(null)
onMounted(() => {
  if (!chartCanvas.value || !sectors.value.length) return
  const top12 = sectors.value.slice(0, 12)
  new Chart(chartCanvas.value, {
    type: 'bar', data: {
      labels: top12.map(s => s.name),
      datasets: [
        { label: '选手数', data: top12.map(s => s.count), backgroundColor: '#2980b9', borderRadius: 6 },
        { label: '高手数', data: top12.map(s => s.qualityCount), backgroundColor: '#27ae60', borderRadius: 6 },
      ]
    },
    options: {
      indexAxis: 'y', responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: 'top' } }, scales: { x: { ticks: { stepSize: 1 } } }
    }
  })
})
</script>

<template>
  <div class="grid-2">
    <div class="card">
      <h2>行业板块分布</h2>
      <p class="hint">按选手行业标签聚合，颜色深浅 = 选手数量</p>
      <div class="chart-wrap tall"><canvas ref="chartCanvas"></canvas></div>
    </div>
    <div class="card">
      <h2>板块详情 <span class="badge">Top 20</span></h2>
      <p class="hint">点击表头可切换排序</p>
      <div style="max-height:500px;overflow-y:auto;">
        <table><thead><tr>
          <th>#</th><th>板块</th>
          <th style="cursor:pointer;" @click="tog('count')">选手{{ ind('count') }}</th>
          <th style="cursor:pointer;" @click="tog('qualityCount')">高手{{ ind('qualityCount') }}</th>
          <th style="cursor:pointer;" @click="tog('avg_position')">平均仓位{{ ind('avg_position') }}</th>
          <th style="cursor:pointer;" @click="tog('avg_return')">平均总收益{{ ind('avg_return') }}</th>
          <th style="cursor:pointer;" @click="tog('avg_daily')">今日平均{{ ind('avg_daily') }}</th>
        </tr></thead>
          <tbody>
            <tr v-for="(s, i) in sorted.slice(0,20)" :key="s.name">
              <td>{{ i + 1 }}</td>
              <td><strong>{{ s.name }}</strong></td>
              <td>{{ s.count }}人</td>
              <td style="color:#27ae60;">{{ s.qualityCount }}人</td>
              <td>{{ s.avg_position.toFixed(0) }}%</td>
              <td v-html="pct(s.avg_return)"></td>
              <td v-html="pct(s.avg_daily)"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
