<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Chart, LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, Filler } from 'chart.js'
import { useTableSort } from '../composables/useTableSort.js'
Chart.register(LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, Filler)

const route = useRoute()
const router = useRouter()
const { sortedPlayers } = inject('stockData')
const { historyLoaded, dateList, getPlayerHistory, getPlayerAllTrades, getInferredPositions } = inject('stockHistory')

const player = computed(() => {
  const all = [...sortedPlayers.value.pinned, ...sortedPlayers.value.rest]
  return all.find(p => p.zh_id === route.params.zh_id) || null
})
const history = computed(() => getPlayerHistory(route.params.zh_id))

const posData = computed(() => player.value?._positions || [])
const tradeData = computed(() => {
  const zhId = route.params.zh_id
  return zhId ? getPlayerAllTrades(zhId) : []
})
const inferredPositions = computed(() => {
  const zhId = route.params.zh_id
  if (!zhId) return []
  // Touch reactive deps explicitly so vue tracks them
  void historyLoaded.value
  void dateList.value
  const confirmedCodes = new Set((player.value?._positions || []).map(p => p.stock_code))
  return getInferredPositions(zhId, confirmedCodes)
})
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
  if (!curveCanvas.value || !history.value.length) return
  if (curveChart) curveChart.destroy()
  const labels = history.value.map(h => h.date.slice(5))
  const dailyData = history.value.map(h => h.daily_return)
  curveChart = new Chart(curveCanvas.value, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: '日收益 %', data: dailyData, borderColor: '#e74c3c', backgroundColor: 'rgba(231,76,60,0.1)', fill: true, tension: 0.3, pointRadius: 3, yAxisID: 'y' },
        { label: '净值', data: history.value.map(h => h.net_value), borderColor: '#2980b9', backgroundColor: 'rgba(41,128,185,0.05)', fill: true, tension: 0.3, pointRadius: 2, yAxisID: 'y1' }
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

watch(() => history.value, () => renderCurve(), { deep: true })
onMounted(() => renderCurve())
</script>

<template>
  <div v-if="player">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
      <span style="font-size:22px;font-weight:520;color:#111;">{{ player.name || player.zh_id }}</span>
      <span v-for="r in player.ranks" :key="r" style="font-size:11px;color:#5b6daa;background:rgba(91,109,170,.08);padding:2px 10px;border-radius:100px;">{{ r }}</span>
    </div>


    <div class="grid-2">
      <div class="card">
        <h2>📦 当前持仓 <span class="badge">{{ sortedPos.length }}</span></h2>
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
    <!-- 推测持仓 -->
    <div v-if="inferredPositions.length" class="card" style="margin-bottom:14px;">
      <h2>📎 推测持仓 <span class="badge">{{ inferredPositions.length }}</span></h2>
      <p class="hint">根据买卖记录推算，仅供参考，非 API 原始数据</p>
      <div style="max-height:300px;overflow-y:auto;">
        <table><thead><tr><th>股票</th><th>代码</th><th>估算仓位</th><th>状态</th><th>买入</th><th>卖出</th></tr></thead>
          <tbody>
            <tr v-for="s in inferredPositions" :key="s.stock_code">
              <td><strong>{{ s.stock_name }}</strong></td>
              <td style="color:#666;">{{ s.stock_code }}</td>
              <td style="color:#666;">{{ s.level_estimate }}</td>
              <td><span :style="{ color: s.confidence === 'mid' ? '#5b6daa' : '#999', fontSize:'12px' }">{{ s.status }}</span></td>
              <td>{{ s.buy_count }}笔</td>
              <td>{{ s.sell_count }}笔</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
      <div class="card">
        <h2>🔄 调仓记录 <span class="badge">{{ sortedTrades.length }}</span></h2>
        <div v-if="!sortedTrades.length" class="empty-state">📭 暂无调仓记录</div>
        <table v-else>
          <thead><tr>
            <th style="cursor:pointer;" @click="tt('trade_date')">日期{{ it('trade_date') }}</th>
            <th style="cursor:pointer;" @click="tt('direction')">方向{{ it('direction') }}</th>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tt('trades_count')">笔数{{ it('trades_count') }}</th>
            <th>仓位</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in sortedTrades" :key="x.id || x.trade_date + x.stock_code">
              <td>{{ x.trade_date }}</td>
              <td><span :class="x.direction === '买入' ? 'buy' : 'sell'">{{ x.direction }}</span></td>
              <td><strong>{{ x.stock_name }}</strong></td>
              <td style="color:#999;">{{ x.stock_code }}</td>
              <td>{{ x.trades_count || 1 }}笔</td>
              <td style="font-size:12px;color:#888;">{{ x.position_ratio || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>

    </div>

  </div>
    <div v-if="player" class="player-meta" style="margin-top:20px;">
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
</template>
