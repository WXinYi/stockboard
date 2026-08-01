<script setup>
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchPlayerDetail } from '../data/loader.js'
import { useTableSort } from '../composables/useTableSort.js'

const route = useRoute()
const router = useRouter()
const { loadPlayerHistory } = inject('stockHistory')
const { playerLookup } = inject('stockData')
const refreshTick = inject('refreshTick', ref(0))

const playerData = ref(null)
const loadingDetail = ref(false)

// 合并：player文件(pos/trades/inferred) + stockData(基本字段如ranks/returns)
const player = computed(() => {
  if (!playerData.value) return null
  const info = playerLookup.value[playerData.value.id] || {}
  return { ...playerData.value, ...info }
})
const history = ref([])

async function loadHistoryData(zhId, force = false) {
  history.value = await loadPlayerHistory(zhId, force)
}

const posData = computed(() => playerData.value?.p || [])
const tradeData = computed(() => playerData.value?.t || [])
const inferredPositions = computed(() => playerData.value?.i || [])
const { sorted: sortedPos, toggle: tp, indicator: ip } = useTableSort(posData, 'rr')
const { sorted: sortedTrades, toggle: tt, indicator: it } = useTableSort(tradeData, 'td')

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
const chartLoading = ref(false)

async function renderCurve() {
  if (!curveCanvas.value || !history.value.length) return
  if (curveChart) curveChart.destroy()
  chartLoading.value = true
  try {
    const { Chart, LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, Filler } = await import('chart.js')
    Chart.register(LineController, LineElement, PointElement, CategoryScale, LinearScale, Tooltip, Legend, Filler)
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
  } finally {
    chartLoading.value = false
  }
}

async function loadPlayer(zhId, force = false) {
  loadingDetail.value = true
  try {
    playerData.value = await fetchPlayerDetail(zhId)
    await loadHistoryData(zhId, force)
    renderCurve()
  } catch (e) {
    console.warn('选手详情加载失败:', e.message)
  } finally {
    loadingDetail.value = false
  }
}

watch(() => route.params.zh_id, (newId) => { if (newId) loadPlayer(newId) })

// 全局刷新信号：App.vue 下拉刷新/顶栏刷新后重新拉取选手详情（force 绕过历史缓存）
watch(refreshTick, () => { if (route.params.zh_id) loadPlayer(route.params.zh_id, true) })

onMounted(() => { if (route.params.zh_id) loadPlayer(route.params.zh_id) })
</script>

<template>
  <div v-if="loadingDetail" class="loading-view" style="min-height:40vh;">
    <div class="loading-spinner"></div>
    <p class="loading-text">加载选手数据…</p>
  </div>
  <div v-else-if="!player && !loadingDetail" class="card">
    <div class="empty-state">📭 选手数据加载失败</div>
  </div>
  <div v-else-if="player">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
      <span style="font-size:22px;font-weight:520;color:#111;">{{ player.name || player.id || player.zh_id }}</span>
      <span v-for="r in player.ranks" :key="r" style="font-size:11px;color:#5b6daa;background:rgba(91,109,170,.08);padding:2px 10px;border-radius:100px;">{{ r }}</span>
    </div>


    <div class="grid-2">
      <div class="card">
        <h2>📦 当前持仓 <span class="badge">{{ sortedPos.length }}</span></h2>
        <div v-if="!sortedPos.length" class="empty-state">📭 暂无持仓数据</div>
        <div class="table-wrap" v-else>
        <table>
          <thead><tr>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tp('cp')">成本价{{ ip('cp') }}</th>
            <th style="cursor:pointer;" @click="tp('np')">现价{{ ip('np') }}</th>
            <th style="cursor:pointer;" @click="tp('pr')">盈亏{{ ip('pr') }}</th>
            <th style="cursor:pointer;" @click="tp('rr')">仓位{{ ip('rr') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in sortedPos" :key="x.sc">
              <td class="nowrap"><strong>{{ x.sn }}</strong></td>
              <td class="nowrap" style="color:#999;">{{ x.sc }}</td>
              <td>{{ (x.cp || 0).toFixed(3) }}</td>
              <td>{{ (x.np || 0).toFixed(3) }}</td>
              <td v-html="pct(x.pr)"></td>
              <td>
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, x.rr || 0) + '%' }"></span></span>
                {{ (x.rr || 0).toFixed(1) }}%
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
    <!-- 推测持仓 -->
    <div v-if="inferredPositions.length" class="card" style="margin-bottom:14px;">
      <h2>📎 推测持仓 <span class="badge">{{ inferredPositions.length }}</span></h2>
      <p class="hint">根据买卖记录推算，仅供参考，非 API 原始数据</p>
      <div style="max-height:300px;overflow-y:auto;">
        <div class="table-wrap"><table><thead><tr><th>股票</th><th>代码</th><th>估算仓位</th><th>状态</th><th>买入</th><th>卖出</th></tr></thead>
          <tbody>
            <tr v-for="s in inferredPositions" :key="s.cd">
              <td class="nowrap"><strong>{{ s.sn }}</strong></td>
              <td class="nowrap" style="color:#666;">{{ s.cd }}</td>
              <td style="color:#666;">{{ s.le }}</td>
              <td><span :style="{ color: s.cf === 'mid' ? '#5b6daa' : '#999', fontSize:'12px' }">{{ s.st }}</span></td>
              <td>{{ s.bc }}笔</td>
              <td>{{ s.sc }}笔</td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
    </div>
      <div class="card">
        <h2>🔄 调仓记录 <span class="badge">{{ sortedTrades.length }}</span></h2>
        <div v-if="!sortedTrades.length" class="empty-state">📭 暂无调仓记录</div>
        <div class="table-wrap" v-else style="max-height:400px;overflow-y:auto;">
        <table>
          <thead><tr>
            <th style="cursor:pointer;" @click="tt('td')">日期{{ it('td') }}</th>
            <th style="cursor:pointer;" @click="tt('dr')">方向{{ it('dr') }}</th>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tt('tc')">笔数{{ it('trades_count') }}</th>
            <th style="cursor:pointer;" @click="tt('pr')">价格{{ it('price') }}</th>
            <th>仓位</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in sortedTrades" :key="x.id || x.td + x.sc">
              <td>{{ x.td }}</td>
              <td><span :class="x.dr === '买入' ? 'buy' : 'sell'">{{ x.dr }}</span></td>
              <td class="nowrap"><strong>{{ x.sn }}</strong></td>
              <td class="nowrap" style="color:#999;">{{ x.sc }}</td>
              <td>{{ x.tc || 1 }}笔</td>
              <td style="font-size:12px;color:#888;">{{ (x.pr && x.pr > 0) ? '¥' + x.pr.toFixed(2) : '—' }}</td>
              <td style="font-size:12px;color:#888;">{{ x.rr || '—' }}</td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>

    </div>

  </div>
    <div v-if="player" class="player-meta" style="margin-top:20px;">
      <div class="player-meta-item"><div class="val" :style="{ color: player.total_return >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(player.total_return) }}</div><div class="lbl">总收益</div></div>
      <div class="player-meta-item"><div class="val" :style="{ color: player.daily_return >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(player.daily_return) }}</div><div class="lbl">日收益</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.net_value || 0).toFixed(3) }}</div><div class="lbl">净值</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.max_drawdown || 0).toFixed(1) }}%</div><div class="lbl">最大回撤</div></div>
      <div class="player-meta-item"><div class="val">{{ posLabel(player.total_position ?? player._total_position) }}</div><div class="lbl">当前仓位</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.win_rate || 0).toFixed(1) }}%</div><div class="lbl">胜率</div></div>
      <div class="player-meta-item"><div class="val">{{ player.days || 0 }}天</div><div class="lbl">运行天数</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.followers || 0).toLocaleString() }}</div><div class="lbl">关注人数</div></div>
      <div class="player-meta-item" style="grid-column:span 3;">
        <div style="font-size:13px;color:#666;text-align:left;">{{ player.intro || player.concept || '暂无简介' }}</div>
        <div class="lbl">简介</div>
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
