<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction } from '../data/loader.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchFengKou, fetchTianTi, fetchMarketLimitReasons, fetchNewHighTrend,
  fetchGlobalIndexes, fetchInstitutionIncrease,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'

defineOptions({ name: 'MarketTab' })

const router = useRouter()

// 竞价卡: 当日快照, 单次加载不轮询
const auction = ref(null)
const auctionLoading = ref(true)
// 实时摘要: 30s 轮询(silent)
const wind = ref(null)
const ladder = ref(null)
const reasons = ref(null)
const newHighs = ref(null)
const global = ref(null)
const institution = ref(null)

async function loadAll(silent = false) {
  try {
    const day = await getLatestTradingDay()
    const dayDash = day ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : ''
    const [w, l, r, nh, g, inst] = await Promise.all([
      fetchFengKou(silent),
      fetchTianTi(silent),
      dayDash ? fetchMarketLimitReasons(dayDash, silent) : null,
      fetchNewHighTrend(silent),
      fetchGlobalIndexes(silent),
      fetchInstitutionIncrease(getLatestReportDate(), false, silent),
    ])
    if (w) wind.value = w
    if (l) ladder.value = l
    if (r) reasons.value = r
    if (nh) newHighs.value = nh
    if (g) global.value = g
    if (inst) institution.value = inst
  } catch (e) { if (!silent) console.error('[MarketTab]', e?.message) }
}

async function loadAuction() {
  try { auction.value = await fetchAuction() } catch (e) { /* 盘外无快照 */ }
  auctionLoading.value = false
}

// 30s 轮询(交易时段)
let timer = null
function startTimer() {
  if (timer || !isTradingTime()) return
  timer = setInterval(() => {
    if (!isTradingTime()) { stopTimer(); return }
    loadAll(true)
  }, 30000)
}
function stopTimer() { clearInterval(timer); timer = null }
function onVisibility() {
  if (document.hidden) { stopTimer(); return }
  if (!isTradingTime()) return
  startTimer(); loadAll(true)
}

// 下拉刷新: 仅当前激活页面响应(usePullRefresh 按激活态过滤)
usePullRefresh(() => { loadAll(); loadAuction() })

// KeepAlive: onUnmounted 不触发 → 轮询必须 onDeactivated 停 / onActivated 恢复, 防泄漏防重复(startTimer 有 guard)
// 首次挂载 onMounted+onActivated 双触发 → 加载只在 onActivated 做(inited 分支)
let inited = false
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
})
onActivated(() => {
  if (!inited) { inited = true; loadAll(); loadAuction() } else if (isTradingTime()) loadAll(true)
  startTimer()
})
onDeactivated(() => { stopTimer() })
onUnmounted(() => {
  stopTimer()
  document.removeEventListener('visibilitychange', onVisibility)
})

function open(section) { router.push('/market/' + section) }
function fmt(v, d = 2) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—' }
function pct(v) { return typeof v === 'number' ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }

// ── 卡片摘要派生 ──
const auctionBadge = computed(() => {
  if (!auction.value) return ''
  return auction.value.env?.pass ? '可出手' : '空仓观望'
})
const auctionReason = computed(() => {
  const r = auction.value?.env?.reasons
  return r && r.length ? r[0] : ''
})
const windTop = computed(() => (wind.value || []).slice(0, 3))
const ladderTop = computed(() => (ladder.value && ladder.value.length ? ladder.value[0] : null))   // 最高板组
const ztCount = computed(() => (reasons.value?.nums?.ZT ?? '—'))
const reasonTop = computed(() => (reasons.value?.groups || []).slice(0, 2))
const newHighToday = computed(() => {
  const a = newHighs.value || []
  return a.length ? a[a.length - 1].count : '—'
})
const nhPoints = computed(() => {
  const pts = (newHighs.value || []).slice(-30)
  if (pts.length < 2) return ''
  const vals = pts.map(p => p.count)
  const max = Math.max(...vals) || 1
  return pts.map((p, i) => `${(i / (pts.length - 1) * 100).toFixed(1)},${(32 - p.count / max * 28).toFixed(1)}`).join(' ')
})
const globalTop = computed(() => (global.value?.indexes || []).slice(0, 3))
const instTop = computed(() => (institution.value || []).slice(0, 3))
const auctionCandidates = computed(() => (auction.value?.candidates || []).slice(0, 2))
</script>

<template>
  <div class="mt-page">
    <p class="mt-sub">盘中概览 · 点击卡片进入详情（30s 自动刷新）</p>
    <div class="mt-cards">
      <!-- 竞价抢筹 -->
      <div class="mt-card" @click="open('auction')">
        <div class="mt-card-head">
          <span class="mt-title">⏰ 竞价抢筹</span>
          <span v-if="auctionBadge" class="mt-badge" :class="auctionBadge === '可出手' ? 'ok' : 'no'">{{ auctionBadge }}</span>
        </div>
        <div v-if="auctionLoading" class="mt-hold">加载中…</div>
        <template v-else-if="auction">
          <p class="mt-line">{{ auctionReason || '—' }}</p>
          <div v-for="c in auctionCandidates" :key="c.code" class="mt-line small">
            <span class="mt-tag">{{ c.tier === 'core' ? '核心' : '备选' }}</span>
            {{ c.name }} · 分 {{ c.score }}
          </div>
        </template>
        <div v-else class="mt-hold">暂无快照</div>
      </div>

      <!-- 最强风口 -->
      <div class="mt-card" @click="open('wind')">
        <div class="mt-card-head"><span class="mt-title">🔥 最强风口</span></div>
        <template v-if="windTop.length">
          <div v-for="w in windTop" :key="w.code" class="mt-line small">
            <span class="mt-bk">{{ w.name }}</span>
            <span class="mt-val">{{ fmt(w.strength, 0) }}</span>
          </div>
        </template>
        <div v-else class="mt-hold">—</div>
      </div>

      <!-- 涨停天梯 -->
      <div class="mt-card" @click="open('ladder')">
        <div class="mt-card-head"><span class="mt-title">🪜 涨停天梯</span></div>
        <template v-if="ladderTop">
          <p class="mt-line">最高连板：<b class="mt-hot">{{ ladderTop.title }}</b></p>
          <p class="mt-line small">{{ ladderTop.rows.length }} 只 · 板内个股见详情</p>
        </template>
        <div v-else class="mt-hold">—</div>
      </div>

      <!-- 涨停原因 -->
      <div class="mt-card" @click="open('reasons')">
        <div class="mt-card-head"><span class="mt-title">📌 涨停原因</span></div>
        <template v-if="reasons">
          <p class="mt-line">今日涨停 <b class="mt-hot">{{ ztCount }}</b> 家</p>
          <div v-for="g in reasonTop" :key="g.bkCode" class="mt-line small">
            <span class="mt-bk">{{ g.bkName }}</span>
            <span class="mt-val">{{ g.stocks.length }} 家</span>
          </div>
        </template>
        <div v-else class="mt-hold">—</div>
      </div>

      <!-- 百日新高 -->
      <div class="mt-card" @click="open('newhighs')">
        <div class="mt-card-head"><span class="mt-title">📈 百日新高</span></div>
        <template v-if="newHighs">
          <p class="mt-line">今日 <b class="mt-hot">{{ newHighToday }}</b> 家</p>
          <svg viewBox="0 0 100 36" preserveAspectRatio="none" class="mt-svg">
            <line v-for="y in [9, 18, 27]" :key="y" x1="0" :x2="100" :y1="y" :y2="y" stroke="#f0f0f0" stroke-width="1"/>
            <polyline :points="nhPoints" fill="none" stroke="#e67e22" stroke-width="1.5"/>
          </svg>
        </template>
        <div v-else class="mt-hold">—</div>
      </div>

      <!-- 外围市场 -->
      <div class="mt-card" @click="open('global')">
        <div class="mt-card-head"><span class="mt-title">🌍 外围市场</span></div>
        <template v-if="globalTop.length">
          <div v-for="g in globalTop" :key="g.code" class="mt-line small">
            <span class="mt-bk">{{ g.name }}</span>
            <span class="mt-val" :style="{ color: g.chgPct >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(g.chgPct) }}</span>
          </div>
        </template>
        <div v-else class="mt-hold">—</div>
      </div>

      <!-- 机构增仓 -->
      <div class="mt-card" @click="open('institution')">
        <div class="mt-card-head"><span class="mt-title">🏦 机构增仓</span></div>
        <template v-if="instTop.length">
          <div v-for="g in instTop" :key="g.bkCode" class="mt-line small">
            <span class="mt-bk">{{ g.bkName }}</span>
            <span class="mt-val">{{ fmt(g.addAmt, 1) }}亿</span>
          </div>
        </template>
        <div v-else class="mt-hold">—</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mt-page { padding: 4px 14px 12px; }
.mt-sub { font-size: 11px; color: #999; margin: 0 0 10px; }
.mt-cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
.mt-card { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 12px; cursor: pointer; box-shadow: 0 1px 3px rgba(0,0,0,.03); transition: transform .15s; }
.mt-card:active { transform: scale(.98); background: #fafbfd; }
.mt-card-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.mt-title { font-size: 13px; font-weight: 600; color: #111; }
.mt-badge { font-size: 10px; padding: 2px 8px; border-radius: 20px; }
.mt-badge.ok { background: #fdecea; color: #c0392b; }
.mt-badge.no { background: #eafaf1; color: #27ae60; }
.mt-line { font-size: 12px; color: #555; margin: 3px 0; line-height: 1.6; }
.mt-line.small { font-size: 11px; }
.mt-line.small:last-child { margin-bottom: 0; }
.mt-hold { font-size: 11px; color: #bbb; padding: 8px 0; }
.mt-bk { color: #333; margin-right: 6px; }
.mt-val { color: #666; font-size: 11px; }
.mt-tag { display: inline-block; background: #f0f2f5; color: #666; font-size: 10px; padding: 0 5px; border-radius: 4px; margin-right: 4px; }
.mt-hot { color: #c0392b; font-weight: 600; }
.mt-svg { width: 100%; height: 36px; display: block; margin-top: 4px; }

@media (max-width: 480px) {
  .mt-page { padding-left: 10px; padding-right: 10px; }
  .mt-cards { grid-template-columns: repeat(2, 1fr); gap: 8px; }
}
@media (min-width: 768px) {
  .mt-page { padding: 8px 28px 20px; }
  .mt-cards { grid-template-columns: repeat(3, 1fr); gap: 12px; }
}
</style>
