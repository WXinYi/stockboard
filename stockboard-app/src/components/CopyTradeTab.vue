<script setup>
import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'

const showAlerts = ref(false)
const showSuspects = ref(false)
const router = useRouter()
const { copyTradeSignals: signals, playerNameMap: playerIds, tradeAlerts, suspectedClears } = inject('stockData')
const { positionChanges: posCh } = inject('stockHistory')
function goPlayer(nameOrId) { router.push('/player/' + (playerIds.value[nameOrId] || nameOrId)) }

const coreData = computed(() => signals.value.coreHoldings)
const { sorted: sortedCore, toggle: tog, indicator: ind } = useTableSort(coreData, 'totalPosition')

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}
</script>

<template>
  <!-- 今日卖出预警 -->
  <div v-if="tradeAlerts.length" class="alert-banner" @click="showAlerts = !showAlerts" style="cursor:pointer;">
    📉 <strong>{{ tradeAlerts.length }}只股票 · {{ tradeAlerts.reduce((s,x)=>s+x.players.length,0) }}人卖出</strong>
    <span style="font-size:11px;color:#999;margin-left:4px;">{{ showAlerts ? '收起' : '展开' }}</span>
    <div v-if="showAlerts" style="margin-top:6px;">
      <div v-for="s in tradeAlerts" :key="s.stock_code" style="margin-bottom:4px;font-size:12px;">
        <strong>{{ s.stock_name }}</strong>：
        <template v-for="(p, i) in s.players" :key="p.zh_id">
          <span v-if="i>0">、</span>
          <span class="player-chip" @click.stop="goPlayer(p.zh_id)">{{ p.name }}</span>
        </template>
      </div>
    </div>
  </div>

  <!-- 疑似清仓 -->
  <div v-if="suspectedClears.length" class="alert-banner" style="background:#fff8e1;border-color:#f0d060;color:#b8860b;cursor:pointer;" @click="showSuspects = !showSuspects">
    🔍 <strong>疑似清仓 · {{ suspectedClears.length }}条</strong>
    <span style="font-size:11px;color:#999;margin-left:4px;">{{ showSuspects ? '收起' : '展开' }}</span>
    <div v-if="showSuspects" style="margin-top:6px;font-size:12px;">
      <div v-for="s in suspectedClears" :key="s.zh_id+s.stock_code" style="margin-bottom:3px;">
        <span class="player-chip" @click.stop="goPlayer(s.zh_id)">{{ s.player_name }}</span>
        → {{ s.stock_name }}
        <span style="color:#999;">({{ s.buyDate }} 买{{ s.level }} → {{ s.sellDate }} 卖{{ s.level }})</span>
      </div>
    </div>
  </div>

  <!-- 持仓变更摘要 -->
  <div v-if="posCh.hasHistory" class="summary-bar">
    📌 {{ posCh.today }} · +{{ posCh.added.length }}新进 -{{ posCh.cleared.length }}清仓 {{ posCh.changes.length }}笔变动
  </div>
  <div v-else class="summary-bar" style="color:#aaa;">📌 需要至少2天数据 · 下个交易日 09:45 自动采集</div>

  <!-- 置顶总结 -->
  <div class="card" style="margin-bottom:20px;background:linear-gradient(135deg,#fef9e7 0%,#fff 100%);border:1px solid #f0d060;">
    <h2 style="border:none;margin-bottom:8px;">💡 今日抄作业指南</h2>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap;">
      <span style="background:#e8f4fd;color:#2980b9;padding:4px 12px;border-radius:8px;font-size:13px;font-weight:600;">🏅 高手定义</span>
      <span style="font-size:12px;color:#666;">运营 <b>≥ 200天</b>，风险调整得分 ≥ 0.15</span>
      <span style="font-size:11px;color:#aaa;">得分 =（年收益×0.6 + 近端×0.4）÷ 最大回撤，近端 = 月×0.5 + 周×0.3 + 日×0.2</span>
    </div>
    <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:12px;">
      <div><span style="font-size:28px;font-weight:700;color:#e74c3c;">{{ signals.buySignals.length }}</span><span style="font-size:13px;color:#888;"> 只股票有高手买入</span></div>
      <div><span style="font-size:28px;font-weight:700;color:#2980b9;">{{ signals.coreHoldings.length }}</span><span style="font-size:13px;color:#888;"> 只股票被高手重仓</span></div>
      <div><span style="font-size:28px;font-weight:700;color:#27ae60;">{{ signals.sellWarnings.length }}</span><span style="font-size:13px;color:#888;"> 只股票被高手卖出</span></div>
      <div><span style="font-size:28px;font-weight:700;color:#8e44ad;">{{ signals.highQuality.length }}</span><span style="font-size:13px;color:#888;"> 只股票综合高分</span></div>
    </div>
  </div>

  <div class="grid-2">
    <!-- 高手买入信号 -->
    <div class="card">
      <h2>🔥 高手正在买入 <span class="badge">{{ signals.buySignals.length }}</span></h2>
      <p class="hint">有高质量选手今日买入的股票，按信号强度排序</p>
      <div style="max-height:500px;overflow-y:auto;">
        <div v-if="!signals.buySignals.length" class="empty-state">📭 今日暂无高手买入信号</div>
        <div v-for="s in signals.buySignals" :key="s.code" style="padding:10px;border-bottom:1px solid #f0f0f0;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>{{ s.name }}</strong>
            <span style="font-size:11px;color:#999;">{{ s.code }}</span>
          </div>
          <div style="margin-top:4px;font-size:12px;">
            <span class="buy">🟢 {{ s.buyers.length }}人买入</span>
            <span v-if="s.sellers.length" class="sell" style="margin-left:8px;">🔴 {{ s.sellers.length }}人卖出</span>
            <span style="color:#888;margin-left:8px;">仓位 {{ s.totalPosition.toFixed(0) }}%</span>
            <span style="color:#2980b9;margin-left:8px;font-weight:600;">信号 {{ s.score.toFixed(1) }}</span>
          </div>
          <div style="font-size:11px;color:#888;margin-top:2px;">买入: <template v-for="(p, idx) in s.buyers" :key="'b'+p"><span v-if="idx>0">、</span><span class="player-chip" @click.stop="goPlayer(p)">{{ p }}</span></template></div>
          <div v-if="s.sellers.length" style="font-size:11px;color:#888;">卖出: <template v-for="(p, idx) in s.sellers" :key="'s'+p"><span v-if="idx>0">、</span><span class="player-chip" @click.stop="goPlayer(p)">{{ p }}</span></template></div>
        </div>
      </div>
    </div>

    <!-- 卖出预警 -->
    <div class="card">
      <h2>⚠️ 高手正在卖出 <span class="badge">{{ signals.sellWarnings.length }}</span></h2>
      <p class="hint">高质量选手正在出货的股票，如果你持有建议关注风险</p>
      <div style="max-height:500px;overflow-y:auto;">
        <div v-if="!signals.sellWarnings.length" class="empty-state">✅ 今日暂无高手集中卖出</div>
        <div v-for="s in signals.sellWarnings" :key="s.code" style="padding:10px;border-bottom:1px solid #f0f0f0;">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>{{ s.name }}</strong>
            <span style="font-size:11px;color:#999;">{{ s.code }}</span>
          </div>
          <div style="margin-top:4px;font-size:12px;">
            <span class="sell">🔴 {{ s.sellers.length }}人卖出</span>
            <span style="color:#888;margin-left:8px;">仓位 {{ s.totalPosition.toFixed(0) }}%</span>
          </div>
          <div style="font-size:11px;color:#888;margin-top:2px;"><template v-for="(p, idx) in s.sellers" :key="'se'+p"><span v-if="idx>0">、</span><span class="player-chip" @click.stop="goPlayer(p)">{{ p }}</span></template></div>
        </div>
      </div>
    </div>
  </div>

  <!-- 核心重仓 -->
  <div class="card" style="margin-top:20px;">
    <h2>💎 高手核心重仓 <span class="badge">Top 20</span></h2>
    <p class="hint">多个高质量选手重仓持有的股票，代表经过深度研究的方向</p>
    <div class="grid-2">
      <div style="max-height:500px;overflow-y:auto;">
        <table><thead><tr>
          <th>#</th><th>股票</th><th>代码</th>
          <th style="cursor:pointer;" @click="tog('holderCount')">高手数{{ ind('holderCount') }}</th>
          <th style="cursor:pointer;" @click="tog('totalPosition')">总仓位{{ ind('totalPosition') }}</th>
          <th style="cursor:pointer;" @click="tog('score')">信号分{{ ind('score') }}</th>
        </tr></thead>
          <tbody>
            <tr v-for="(s, i) in sortedCore.slice(0,20)" :key="s.code">
              <td>{{ i + 1 }}</td>
              <td><strong>{{ s.name }}</strong></td>
              <td style="color:#999;">{{ s.code }}</td>
              <td>{{ s.holderCount }}人</td>
              <td>
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, s.totalPosition/s.holderCount) + '%' }"></span></span>
                {{ s.totalPosition.toFixed(0) }}%
              </td>
              <td><span :style="{ color: s.score >= 0 ? '#e74c3c' : '#27ae60', fontWeight: 600 }">{{ s.score.toFixed(1) }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div>
        <p style="font-size:13px;color:#666;margin-bottom:10px;">📌 持有这些股票的高手:</p>
        <div style="max-height:500px;overflow-y:auto;">
          <div v-for="s in sortedCore.slice(0,10)" :key="'h'+s.code" style="padding:8px;border-bottom:1px solid #f0f0f0;">
            <strong style="font-size:13px;">{{ s.name }}</strong>
            <span style="font-size:10px;color:#999;margin-left:4px;">{{ s.code }}</span>
            <div style="font-size:11px;color:#888;margin-top:2px;"><template v-for="(p, idx) in s.holders" :key="'h'+p"><span v-if="idx>0">、</span><span class="player-chip" @click.stop="goPlayer(p)">{{ p }}</span></template></div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
