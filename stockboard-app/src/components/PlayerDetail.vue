<script setup>
/**
 * P3 选手详情：零静态 players/{zh}.json 依赖。
 * 基础读数来自 players.json.prevClose（盘中不变），今日操作来自 today.json，
 * 持仓/收益/调仓三块由 EM rtV2 按 userId 独立直连，失败诚实回退并显示原因。
 */
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useEmPlayer, BLOCKS, BLOCK_LABELS } from '../composables/useEmPlayer.js'
import DataBadge from './DataBadge.vue'

const route = useRoute()
const router = useRouter()
const { playerLookup, emContract, tradedPlayerIds, sliceErrors } = inject('stockData')
const refreshTick = inject('refreshTick', ref(0))

const { blocks, load, requestCount } = useEmPlayer()
const loading = ref(false)
const showRaw = ref({})

const zhId = computed(() => String(route.params.zh_id || ''))
const player = computed(() => playerLookup.value[zhId.value] || null)
const traded = computed(() => tradedPlayerIds.value.has(zhId.value))

function num(v, d = 2) {
  const n = typeof v === 'number' ? v : parseFloat(v)
  return isFinite(n) ? n.toFixed(d) : '—'
}
function retColor(v) {
  if (v == null || !isFinite(v)) return 'var(--ink-3)'
  return v >= 0 ? 'var(--up)' : 'var(--down)'
}
const pctText = v => (v == null || !isFinite(v)) ? '—' : (v >= 0 ? `+${num(v)}%` : `${num(v)}%`)

const overall = computed(() => {
  const list = BLOCKS.map(b => blocks.value[b])
  const bad = list.find(x => x.state === 'error')
  if (bad) return { state: 'error', reason: bad.reason, detail: bad.detail, at: '' }
  const live = list.find(x => x.state === 'live')
  if (live) return { state: 'live', reason: '', detail: null, at: live.at }
  return { state: 'stale', reason: emContract.value?.ok === false ? '东财接口策略变更，全站回退历史数据' : '数据时点', detail: null, at: '' }
})

const rows = computed(() => BLOCKS.map(b => ({
  key: b, label: BLOCK_LABELS[b], ...blocks.value[b],
  count: Array.isArray(blocks.value[b].data) ? blocks.value[b].data.length
    : (blocks.value[b].data ? Object.keys(blocks.value[b].data).length : 0),
})))

async function loadEm() {
  if (!player.value?.userId) return
  loading.value = true
  try { await load(player.value.userId, zhId.value, emContract.value) }
  finally { loading.value = false }
}

watch([zhId, () => player.value?.userId, emContract], () => { loadEm() })
watch(refreshTick, () => { loadEm() })
onMounted(() => { loadEm() })

function openMarket() { router.push('/market') }
</script>

<template>
  <div v-if="!player" class="card">
    <h2>选手详情</h2>
    <div class="empty-state">
      <template v-if="sliceErrors.players">选手榜单加载失败，无法读取该选手档案</template>
      <template v-else>该选手不在今日榜单（ID {{ zhId }}），暂无档案</template>
    </div>
  </div>

  <div v-else>
    <div class="card">
      <h2>选手档案 <span class="badge">{{ zhId }}</span></h2>
      <div class="pd-head">
        <div>
          <div class="pd-name">{{ player.name }}</div>
          <div class="pd-id">userId {{ player.userId || '—' }}<span v-if="player.watched" class="pd-watched">关注</span></div>
        </div>
        <span v-if="traded" class="pd-traded">今日有操作</span>
      </div>
      <div class="pd-badge"><DataBadge v-bind="overall" /></div>
    </div>

    <!-- 前一交易日收盘校准值：盘中不变，15:15 后才刷新 -->
    <div class="player-meta">
      <div class="player-meta-item"><span class="val" :style="{ color: retColor(player.total_return) }">{{ pctText(player.total_return) }}</span><span class="lbl">总收益</span></div>
      <div class="player-meta-item"><span class="val" :style="{ color: retColor(player.daily_return) }">{{ pctText(player.daily_return) }}</span><span class="lbl">日收益</span></div>
      <div class="player-meta-item"><span class="val">{{ num(player.net_value, 3) }}</span><span class="lbl">净值</span></div>
    </div>

    <div class="card">
      <h2>今日操作</h2>
      <div v-if="player.today" class="pd-today">
        <span class="pd-today-time">{{ player.today.updatedAt }}</span>
        <span class="pd-today-count">{{ player.today.tradeCount ?? 0 }} 笔调仓</span>
        <span v-if="traded" class="trade-dot" aria-label="今日有调仓"></span>
      </div>
      <div v-else class="empty-state">今日 11:30 前尚无调仓快照</div>
    </div>

    <div v-for="r in rows" :key="r.key" class="card">
      <h2>{{ r.label }} <span class="badge">实时</span></h2>
      <div class="pd-badge"><DataBadge :state="r.state" :at="r.at" :reason="r.reason" :detail="r.detail" /></div>
      <div v-if="r.state === 'live'" class="pd-live">
        <span>已获取 {{ r.count }} 条{{ r.label }}记录</span>
        <button type="button" class="pd-raw-btn" :aria-expanded="String(!!showRaw[r.key])"
                @click="showRaw[r.key] = !showRaw[r.key]">{{ showRaw[r.key] ? '收起' : '查看原始数据' }}</button>
      </div>
      <div v-else class="empty-state">实时数据暂不可用，已回退至静态快照</div>
      <pre v-if="showRaw[r.key] && r.data" class="pd-raw">{{ JSON.stringify(r.data, null, 2) }}</pre>
    </div>

    <div class="card">
      <div class="pd-foot">静态快照只含榜单与收盘校准值；实时明细由接口按区块独立拉取，任一失败不影响其它区块。</div>
      <button type="button" class="pd-back" @click="openMarket()">返回盘面</button>
    </div>
  </div>
</template>

<style scoped>
.pd-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.pd-name { font-size: 20px; font-weight: 700; color: var(--ink); letter-spacing: -.02em; }
.pd-id { margin-top: 5px; font-family: var(--mono); font-size: 10px; color: var(--ink-4); letter-spacing: .05em; }
.pd-watched { margin-left: 8px; color: var(--accent); border: 1px solid var(--accent-line); padding: 0 5px; border-radius: 2px; }
.pd-traded { font-family: var(--mono); font-size: 10px; color: var(--gold); border: 1px solid rgba(224,177,92,.3); padding: 3px 7px; border-radius: 2px; flex: none; }
.pd-badge { margin-top: 12px; }
.pd-today { display: flex; align-items: center; gap: 12px; font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.pd-today-time { color: var(--ink); }
.pd-live { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
.pd-raw-btn, .pd-back {
  background: var(--bg-3); color: var(--ink-2);
  border: 1px solid var(--line-2); border-radius: 2px;
  padding: 4px 9px; font: inherit; font-family: var(--mono); font-size: 10px;
  cursor: pointer;
}
.pd-raw-btn:hover, .pd-back:hover { color: var(--accent); border-color: var(--accent-line); }
.pd-raw { margin-top: 10px; max-height: 260px; overflow: auto; padding: 10px; background: var(--bg-1); border: 1px solid var(--line); border-radius: 2px; font-family: var(--mono); font-size: 10px; color: var(--ink-3); line-height: 1.5; }
.pd-foot { font-size: 11px; color: var(--ink-4); line-height: 1.6; margin-bottom: 10px; }
</style>
