<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Chart, LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, Filler } from 'chart.js'
import { useTableSort } from '../composables/useTableSort.js'
Chart.register(LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, Filler)

const props = defineProps({
  player: { type: Object, required: true },
  history: { type: Array, default: () => [] },
})
const emit = defineEmits(['show-player'])

const posData = computed(() => props.player._positions || [])
const tradeData = computed(() => props.player._trades || [])
const { sorted: sortedPos, toggle: tp, indicator: ip } = useTableSort(posData, 'position_ratio')
const { sorted: sortedTrades, toggle: tt, indicator: it } = useTableSort(tradeData, 'trade_date')

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}
function posLabel(total) {
  if (!total || total === 0) return '空仓'
  if (total < 10) return '1成以下'
  if (total < 30) return '1-3成'
  if (total < 50) return '3-5成'
  if (total < 70) return '5-7成'
  if (total < 90) return '7-9成'
  return '9成以上'
}

const curveCanvas = ref(null)
let curveChart = null

function renderCurve() {
  if (!curveCanvas.value || !props.history.length) return
  if (curveChart) curveChart.destroy()
  const labels = props.history.map(h => h.date.slice(5))
  const dailyData = props.history.map(h => h.daily_return)
  curveChart = new Chart(curveCanvas.value, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: '日收益 %', data: dailyData, borderColor: '#e74c3c', backgroundColor: 'rgba(231,76,60,0.1)', fill: true, tension: 0.3, pointRadius: 3, yAxisID: 'y' },
        { label: '净值', data: props.history.map(h => h.net_value), borderColor: '#2980b9', backgroundColor: 'rgba(41,128,185,0.05)', fill: true, tension: 0.3, pointRadius: 2, yAxisID: 'y1' }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: { legend: { position: 'top' } },
      scales: {
        y: { type: 'linear', position: 'left', title: { display: true, text: '日收益%' } },
        y1: { type: 'linear', position: 'right', title: { display: true, text: '净值' }, grid: { drawOnChartArea: false } },
      }
    }
  })
}

watch(() => props.history, () => renderCurve(), { deep: true })
onMounted(() => renderCurve())
</script>

<template>
  <div v-if="player">
    <div class="player-meta">
      <div class="player-meta-item"><div class="val" style="color:#2980b9;">{{ player.name || player.zh_id }}</div><div class="lbl">选手</div></div>
      <div class="player-meta-item"><div class="val" :style="{ color: player.total_return >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(player.total_return) }}</div><div class="lbl">总收益</div></div>
      <div class="player-meta-item"><div class="val" :style="{ color: player.daily_return >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(player.daily_return) }}</div><div class="lbl">日收益</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.net_value || 0).toFixed(3) }}</div><div class="lbl">净值</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.max_drawdown || 0).toFixed(1) }}%</div><div class="lbl">最大回撤</div></div>
      <div class="player-meta-item"><div class="val">{{ posLabel(player._total_position) }}</div><div class="lbl">当前仓位</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.win_rate || 0).toFixed(1) }}%</div><div class="lbl">胜率</div></div>
      <div class="player-meta-item"><div class="val">{{ player.days || 0 }}天</div><div class="lbl">运行天数</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.followers || 0).toLocaleString() }}</div><div class="lbl">关注人数</div></div>
      <div class="player-meta-item" style="grid-column:span 3;">
        <div style="font-size:13px;color:#666;text-align:left;">{{ player.intro || player.concept || '暂无简介' }}</div>
        <div class="lbl">简介</div>
      </div>
      <div v-if="player.manager_name" class="player-meta-item">
        <div class="val" style="font-size:16px;">{{ player.manager_name }}</div><div class="lbl">管理人</div>
      </div>
      <div v-if="player.ranks?.length" class="player-meta-item" style="grid-column:span 3;">
        <div style="font-size:12px;color:#666;text-align:left;">
          上榜: <span v-for="r in player.ranks" :key="r" class="rank-tag">{{ r }}</span>
        </div>
        <div class="lbl">榜单</div>
      </div>
    </div>

    <div class="card" style="margin-bottom:20px;">
      <h2>📈 收益走势 <span v-if="history.length < 2" style="font-size:11px;color:#999;font-weight:400;">（需要至少2天数据）</span></h2>
      <div v-if="history.length >= 2" class="chart-wrap tall"><canvas ref="curveCanvas"></canvas></div>
      <div v-else class="empty-state"><p>📭 每天运行数据采集，积累多天后自动生成收益曲线</p><p style="font-size:11px;color:#aaa;">当前仅 {{ history.length }} 天数据</p></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h2>📦 当前持仓</h2>
        <div v-if="!sortedPos.length" class="empty-state">📭 暂无持仓数据</div>
        <table v-else>
          <thead><tr>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tp('cost_price')">成本价{{ ip('cost_price') }}</th>
            <th style="cursor:pointer;" @click="tp('current_price')">现价{{ ip('current_price') }}</th>
            <th style="cursor:pointer;" @click="tp('profit_ratio')">盈亏{{ ip('profit_ratio') }}</th>
            <th style="cursor:pointer;" @click="tp('position_ratio')">仓位{{ ip('position_ratio') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in sortedPos" :key="x.stock_code">
              <td><strong>{{ x.stock_name }}</strong></td>
              <td style="color:#999;">{{ x.stock_code }}</td>
              <td>{{ (x.cost_price || 0).toFixed(3) }}</td>
              <td>{{ (x.current_price || 0).toFixed(3) }}</td>
              <td v-html="pct(x.profit_ratio)"></td>
              <td>
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, x.position_ratio || 0) + '%' }"></span></span>
                {{ (x.position_ratio || 0).toFixed(1) }}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="card">
        <h2>🔄 调仓记录</h2>
        <div v-if="!sortedTrades.length" class="empty-state">📭 暂无调仓记录</div>
        <table v-else>
          <thead><tr>
            <th style="cursor:pointer;" @click="tt('trade_date')">日期{{ it('trade_date') }}</th>
            <th style="cursor:pointer;" @click="tt('direction')">方向{{ it('direction') }}</th>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tt('trades_count')">笔数{{ it('trades_count') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in sortedTrades" :key="x.id || x.trade_date + x.stock_code">
              <td>{{ x.trade_date }}</td>
              <td><span :class="x.direction === '买入' ? 'buy' : 'sell'">{{ x.direction }}</span></td>
              <td><strong>{{ x.stock_name }}</strong></td>
              <td style="color:#999;">{{ x.stock_code }}</td>
              <td>{{ x.trades_count || 1 }}笔</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
