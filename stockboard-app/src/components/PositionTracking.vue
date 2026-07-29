<script setup>
import { inject } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const { positionChanges: changes } = inject('stockHistory')
function goPlayer(id) { router.push('/player/' + id) }

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`
}
</script>

<template>
  <div v-if="!changes.hasHistory" class="card">
    <h2>📅 持仓变化追踪</h2>
    <div class="empty-state">
      <p style="font-size:28px;margin-bottom:8px;">📭</p>
      <p>需要至少 2 天数据才能对比持仓变化</p>
      <p style="font-size:12px;color:#aaa;">每天运行数据采集，历史数据自动累积</p>
    </div>
  </div>

  <template v-else>
    <div class="card" style="margin-bottom:20px;background:linear-gradient(135deg,#f0f7ff 0%,#fff 100%);border:1px solid #b8d4f0;">
      <h2 style="border:none;margin-bottom:4px;">📅 持仓变化追踪</h2>
      <p class="hint">{{ changes.yesterday }} → {{ changes.today }}，共 {{ changes.changes.length }} 笔变动 · <span style="color:#e67e22;">⚠️ 仅基于前2大持仓，仅供参考</span></p>
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:8px;">
        <div><span style="font-size:24px;font-weight:700;color:#2980b9;">{{ changes.added.length }}</span><span style="font-size:13px;color:#888;"> 只新进</span></div>
        <div><span style="font-size:24px;font-weight:700;color:#e74c3c;">{{ changes.cleared.length }}</span><span style="font-size:13px;color:#888;"> 只清仓</span></div>
      </div>
    </div>

    <!-- 新进 -->
    <div class="card" style="margin-bottom:20px;" v-if="changes.added.length">
      <h2>🆕 新进持仓 <span class="badge">{{ changes.added.length }}</span></h2>
      <div style="max-height:300px;overflow-y:auto;">
      <table><thead><tr><th>股票</th><th>代码</th><th>选手</th><th>仓位</th></tr></thead>
        <tbody>
          <tr v-for="c in changes.added" :key="c[0]+c[2]">   <!-- zh_id+stock_code -->
            <td><strong>{{ c[3] }}</strong></td>              <!-- stock_name -->
            <td style="color:#999;">{{ c[2] }}</td>           <!-- stock_code -->
            <td><span class="player-chip" @click="goPlayer(c[0])">{{ c[1] }}</span></td>  <!-- zh_id, player_name -->
            <td><span class="positive">{{ c[8].toFixed(1) }}%</span></td>  <!-- todayRatio -->
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <!-- 清仓 -->
    <div class="card" style="margin-bottom:20px;" v-if="changes.cleared.length">
      <h2>🚫 清仓 <span class="badge">{{ changes.cleared.length }}</span></h2>
      <div style="max-height:300px;overflow-y:auto;">
      <table><thead><tr><th>股票</th><th>代码</th><th>选手</th><th>原仓位</th></tr></thead>
        <tbody>
          <tr v-for="c in changes.cleared" :key="c[0]+c[2]">
            <td><strong>{{ c[3] }}</strong></td>
            <td style="color:#999;">{{ c[2] }}</td>
            <td><span class="player-chip" @click="goPlayer(c[0])">{{ c[1] }}</span></td>
            <td><span class="negative">{{ c[7].toFixed(1) }}%</span></td>  <!-- yesterdayRatio -->
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <!-- 全部变动 -->
    <div class="card">
      <h2>📋 全部仓位变动</h2>
      <div style="max-height:500px;overflow-y:auto;">
        <table><thead><tr><th>类型</th><th>股票</th><th>代码</th><th>选手</th><th>变动</th><th>变化</th></tr></thead>
          <tbody>
            <tr v-for="c in changes.changes" :key="c[0]+c[2]+c[4]">  <!-- zh_id+stock_code+type -->
              <td>{{ c[5] }} {{ c[4] }}</td>                     <!-- emoji + type -->
              <td><strong>{{ c[3] }}</strong></td>               <!-- stock_name -->
              <td style="color:#999;">{{ c[2] }}</td>            <!-- stock_code -->
              <td><span class="player-chip" @click="goPlayer(c[0])">{{ c[1] }}</span></td>  <!-- zh_id + player_name -->
              <td>{{ c[7].toFixed(1) }}% → {{ c[8].toFixed(1) }}%</td>  <!-- yesterdayRatio → todayRatio -->
              <td v-html="pct(c[6])"></td>                       <!-- delta -->
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </template>
</template>
