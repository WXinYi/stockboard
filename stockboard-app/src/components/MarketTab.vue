<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction } from '../data/loader.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchFengKou, fetchTianTi, fetchMarketLimitReasons, fetchNewHighTrend,
  fetchGlobalIndexes, fetchInstitutionIncrease,
  fetchMarketMood, fetchMarketHighlights, fetchLhbList,
  fetchBoardAnnotations, fetchMoneyEffect,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'

defineOptions({ name: 'MarketTab' })

const router = useRouter()

// 竞价: 当日快照, 单次加载不轮询
const auction = ref(null)
const auctionLoading = ref(true)
// 实时摘要: 30s 轮询(silent)
const wind = ref(null)
const ladder = ref(null)
const reasons = ref(null)
const newHighs = ref(null)
const global = ref(null)
const institution = ref(null)
const mood = ref(null)
const live = ref(null)
const lhb = ref(null)
const annotations = ref(null)   // 板块标注(#9 GetPoint)
const effect = ref(null)        // 赚钱效应(#29 GetMoneyDetail)

async function loadAll(silent = false) {
  try {
    const day = await getLatestTradingDay()
    const dayDash = day ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : ''
    const [w, l, r, nh, g, inst, m, hl, lh, ann, eff] = await Promise.all([
      fetchFengKou(silent),
      fetchTianTi(silent),
      dayDash ? fetchMarketLimitReasons(dayDash, silent) : null,
      fetchNewHighTrend(silent),
      fetchGlobalIndexes(silent),
      fetchInstitutionIncrease(getLatestReportDate(), false, silent),
      fetchMarketMood(silent),
      fetchMarketHighlights(silent),
      fetchLhbList(silent),
      fetchBoardAnnotations(silent),
      dayDash ? fetchMoneyEffect(dayDash, silent) : null,
    ])
    if (w) wind.value = w
    if (l) ladder.value = l
    if (r) reasons.value = r
    if (nh) newHighs.value = nh
    if (g) global.value = g
    if (inst) institution.value = inst
    if (m) mood.value = m
    if (hl) live.value = hl
    if (lh) lhb.value = lh
    if (ann) annotations.value = ann
    if (eff) effect.value = eff
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
function goStock(row) { router.push({ path: '/stock/' + row.code, query: { name: row.name } }) }
function goBoard(bk) { if (bk.bkCode) router.push({ path: '/board/' + bk.bkCode, query: { name: bk.bkName } }) }
function fmt(v, d = 2) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—' }
function pct(v) { return (typeof v === 'number' && isFinite(v)) ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }

// ── 分段 Tab(板块优先) ──
const tab = ref('board')
const TABS = [
  { key: 'board', label: '板块' },
  { key: 'live', label: '异动' },
  { key: 'zt', label: '涨停' },
  { key: 'lhb', label: '龙虎榜' },
  { key: 'more', label: '更多' },
]

// ── 竞价(情绪条徽标) ──
const auctionBadge = computed(() => {
  if (!auction.value) return ''
  return auction.value.env?.pass ? '可出手' : '空仓观望'
})
const auctionReason = computed(() => {
  const r = auction.value?.env?.reasons
  return r && r.length ? r[0] : ''
})
const auctionCandidates = computed(() => (auction.value?.candidates || []).slice(0, 2))

// ── 市场情绪 ──
const moodToday = computed(() => (mood.value || [])[0] || null)   // info 按 Day 倒序, 最新在前
const moodPrev = computed(() => (mood.value || [])[1] || null)
const moodDelta = computed(() => moodToday.value && moodPrev.value ? moodToday.value.strong - moodPrev.value.strong : null)
// 强度分级色(0-100 评分): 亢奋/活跃/中性/偏弱/冰点
function moodColor(v) {
  if (v >= 75) return '#c0392b'
  if (v >= 60) return '#e74c3c'
  if (v >= 45) return '#e67e22'
  if (v >= 30) return '#27ae60'
  return '#1e8449'
}
const GAUGE_COLORS = ['#1e8449', '#27ae60', '#e67e22', '#e74c3c', '#c0392b']   // 温度计: 左冰点 → 右亢奋
function moodLevel(v) { return v >= 75 ? 'hot' : v >= 60 ? 'warm' : v >= 45 ? 'mid' : v >= 30 ? 'cool' : 'cold' }
const MOOD_LABELS = { hot: '亢奋', warm: '活跃', mid: '中性', cool: '偏弱', cold: '冰点' }
function moodLabel(v) { return MOOD_LABELS[moodLevel(v)] }
// 赚钱效应: 赚=sum(gain 5档), 亏=sum(loss 5档), 净赚=差
const effectGain = computed(() => (effect.value?.gain || []).reduce((a, b) => a + b, 0))
const effectLoss = computed(() => (effect.value?.loss || []).reduce((a, b) => a + b, 0))
const effectNet = computed(() => effectGain.value - effectLoss.value)

// ── 板块 Tab: 最强风口 + 板块标注 ──
const windTop8 = computed(() => (wind.value || []).slice(0, 8))
const windBarMax = computed(() => Math.max(...(wind.value || []).map(w => w.strength || 0), 1))
const windBar = w => ({ width: (w.strength / windBarMax.value * 100).toFixed(1) + '%', background: '#e67e22' })
const annotationsTop = computed(() => [...(annotations.value || [])].reverse().slice(0, 10))   // 接口按时间正序 → 最新在上
// 风口/标注是盘中实时接口: 盘外(非交易时段)恒空, 提示等待开盘
const windHold = computed(() => isTradingTime() ? '暂无数据' : '盘外暂无 · 开盘后自动更新')

// ── 异动 Tab: 盘面亮点时间线(接口正序 → 反转最新在上) ──
const liveTop = computed(() => [...(live.value || [])].reverse().slice(0, 10))
const liveCount = computed(() => (live.value || []).length)

// ── 涨停 Tab: 天梯 + 原因 + 新高 ──
const ladderTop5 = computed(() => (ladder.value || []).slice(0, 5))
const ladderLevel = g => { const n = parseInt(g.title); return !isNaN(n) ? n : 0 }
const ladderStocks = g => (g.rows || []).slice(0, 3).map(r => r.name).join('、')
const ztCount = computed(() => (reasons.value?.nums?.ZT ?? '—'))
const reasonTop5 = computed(() => (reasons.value?.groups || []).slice(0, 5))
const reasonStocks = g => (g.stocks || []).slice(0, 2).map(s => s.name).join('、')
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

// ── 龙虎榜 Tab: TOP10 条形榜(接口已按净买降序) ──
const lhbTop10 = computed(() => (lhb.value?.list || []).slice(0, 10))
const lhbCount = computed(() => (lhb.value?.list || []).length)
function fmtYi(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const s = v / 1e8
  return (s >= 0 ? '+' : '') + s.toFixed(2) + '亿'
}
function fmtTime(t) {
  if (!t) return '—'
  const d = new Date(t * 1000)
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
}
const RANK_NOS = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩']
// 条形: 宽度按全部净买额绝对值最大值归一, 正红负绿
const lhbBarMax = computed(() => Math.max(...(lhb.value?.list || []).map(s => Math.abs(s.buyIn)), 1))
const lhbBar = s => ({
  width: (Math.abs(s.buyIn) / lhbBarMax.value * 100).toFixed(1) + '%',
  background: s.buyIn >= 0 ? '#e74c3c' : '#27ae60',
})

// ── 更多 Tab: 外围 + 机构 + 竞价兜底 ──
const globalIndexes = computed(() => (global.value?.indexes || []))
const instTop8 = computed(() => (institution.value || []).slice(0, 8))
</script>

<template>
  <div class="mt-page">
    <!-- ① 情绪总览条: 强度 + 温度计 + KPI + 赚钱效应 -->
    <div class="mt-hero">
      <div class="mt-hero-top">
        <div class="mt-hero-strength" @click="open('mood')">
          <template v-if="moodToday">
            <span class="mt-hero-val" :style="{ color: moodColor(moodToday.strong) }">{{ moodToday.strong }}</span>
            <span class="mt-hero-label" :style="{ color: moodColor(moodToday.strong) }">{{ moodLabel(moodToday.strong) }}</span>
            <span v-if="moodDelta !== null" class="mt-delta" :style="{ color: moodDelta >= 0 ? '#e74c3c' : '#27ae60' }">{{ moodDelta >= 0 ? '↗' : '↘' }} {{ Math.abs(moodDelta) }}</span>
          </template>
          <span v-else class="mt-hold">情绪加载中…</span>
        </div>
      </div>
      <div v-if="moodToday" class="mt-gauge" @click="open('mood')">
        <div class="mt-gauge-bar">
          <div v-for="(c, i) in GAUGE_COLORS" :key="i" class="mt-gauge-seg" :style="{ background: c }"></div>
          <div class="mt-gauge-dot" :style="{ left: moodToday.strong + '%' }"></div>
        </div>
        <div class="mt-gauge-scale"><span>冰点</span><span>亢奋</span></div>
      </div>
      <div v-if="moodToday" class="mt-kpis" @click="open('mood')">
        <div class="mt-kpi"><b class="k-up">{{ moodToday.zt }}</b><span>涨停</span></div>
        <div class="mt-kpi"><b class="k-up">{{ moodToday.lbgd }}</b><span>连板</span></div>
        <div class="mt-kpi"><b class="k-down">{{ moodToday.df }}</b><span>跌停</span></div>
      </div>
      <div v-if="effect" class="mt-hero-effect" @click="open('mood')">
        赚钱效应：赚 <b class="k-up">{{ effectGain }}</b> 家 · 亏 <b class="k-down">{{ effectLoss }}</b> 家 · <b :style="{ color: effectNet >= 0 ? '#c0392b' : '#27ae60' }">{{ effectNet >= 0 ? '净赚' : '净亏' }} {{ Math.abs(effectNet) }} 家</b>
      </div>
    </div>

    <!-- ② 竞价抢筹 (常驻, 主页面直接可见) -->
    <div class="mt-auction" @click="open('auction')">
      <template v-if="!auctionLoading && auction">
        <div class="mt-auction-row">
          <span class="mt-badge" :class="auctionBadge === '可出手' ? 'ok' : 'no'">{{ auctionBadge }}</span>
          <span class="mt-auction-reason">{{ auctionReason || '—' }}</span>
          <span class="mt-more">查看全部 ›</span>
        </div>
        <div v-if="auctionCandidates.length" class="mt-auction-row">
          <span v-for="c in auctionCandidates" :key="c.code" class="mt-auction-stock">
            <em class="mt-tag">{{ c.tier === 'core' ? '核心' : '备选' }}</em>{{ c.name }}<i>{{ c.score }}分</i>
          </span>
        </div>
      </template>
      <div v-else class="mt-auction-empty">
        <span class="mt-badge no">暂无快照</span>
        <span class="mt-auction-reason">{{ auctionLoading ? '加载中…' : '盘前 09:25 后更新' }}</span>
      </div>
    </div>

    <!-- ③ 分段 Tab -->
    <div class="mt-tabs">
      <button v-for="t in TABS" :key="t.key" class="mt-tab" :class="{ on: tab === t.key }" @click="tab = t.key">{{ t.label }}</button>
    </div>

    <!-- ④ 板块: 最强风口 + 板块标注 -->
    <div v-show="tab === 'board'" class="mt-pane">
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>🔥 最强风口</h3>
          <em>强度评分</em>
          <button class="mt-more" @click="open('wind')">查看全部 ›</button>
        </div>
        <div v-if="windTop8.length">
          <div v-for="w in windTop8" :key="w.code" class="mt-row" @click="goStock(w)">
            <span class="mt-row-name">{{ w.name }}</span>
            <span class="mt-bar-track"><span class="mt-bar" :style="windBar(w)"></span></span>
            <span class="mt-row-val" style="width:34px;text-align:right;">{{ fmt(w.strength, 0) }}</span>
            <span class="mt-row-chg" :style="{ color: w.chgPct >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(w.chgPct) }}</span>
          </div>
        </div>
        <div v-else class="mt-hold">{{ windHold }}</div>
      </section>
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>🗺️ 板块标注</h3>
          <em>最新在上</em>
          <button class="mt-more" @click="open('live')">查看全部 ›</button>
        </div>
        <div v-if="annotationsTop.length">
          <div v-for="(a, i) in annotationsTop" :key="i" class="mt-row" @click="goBoard(a)">
            <span class="mt-tl-time">{{ fmtTime(a.time) }}</span>
            <span class="mt-row-name">{{ a.text }}</span>
            <span v-if="a.bkName" class="mt-bk-tag">{{ a.bkName }}</span>
            <span class="mt-row-chg" :style="{ color: a.zdf === null ? '#999' : (a.zdf >= 0 ? '#e74c3c' : '#27ae60') }">{{ pct(a.zdf) }}</span>
          </div>
        </div>
        <div v-else class="mt-hold">{{ windHold }}</div>
      </section>
    </div>

    <!-- ⑤ 异动: 盘面亮点时间线 -->
    <div v-show="tab === 'live'" class="mt-pane">
      <section class="mt-sec mt-sec-plain">
        <div class="mt-sec-head">
          <h3>📣 盘面亮点</h3>
          <em v-if="liveCount">{{ liveCount }} 条</em>
          <button class="mt-more" @click="open('live')">查看全部 ›</button>
        </div>
        <div v-if="liveTop.length" class="mt-timeline">
          <div v-for="(h, i) in liveTop" :key="i" class="mt-tl-item" @click="goBoard(h)">
            <span class="mt-tl-dot"></span>
            <span class="mt-tl-time">{{ fmtTime(h.time) }}</span>
            <span v-if="h.tagName" class="mt-tl-tag">{{ h.tagName }}</span>
            <span class="mt-tl-text">{{ h.detail }}</span>
          </div>
        </div>
        <div v-else class="mt-hold">—</div>
      </section>
    </div>

    <!-- ⑥ 涨停: 天梯 + 原因 + 新高 -->
    <div v-show="tab === 'zt'" class="mt-pane">
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>🪜 涨停天梯</h3>
          <em>连板梯队</em>
          <button class="mt-more" @click="open('ladder')">查看全部 ›</button>
        </div>
        <div v-if="ladderTop5.length">
          <div v-for="g in ladderTop5" :key="g.title" class="mt-group-row" @click="open('ladder')">
            <span class="mt-group-tag" :class="{ hot: ladderLevel(g) >= 3 }">{{ g.title }}</span>
            <span class="mt-group-count">{{ g.rows.length }} 只</span>
            <span class="mt-group-stocks">{{ ladderStocks(g) }}</span>
          </div>
        </div>
        <div v-else class="mt-hold">—</div>
      </section>
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>📌 涨停原因</h3>
          <em>今日 {{ ztCount }} 家</em>
          <button class="mt-more" @click="open('reasons')">查看全部 ›</button>
        </div>
        <div v-if="reasonTop5.length">
          <div v-for="g in reasonTop5" :key="g.bkCode" class="mt-row" @click="open('reasons')">
            <span class="mt-row-name">{{ g.bkName }}</span>
            <span class="mt-row-str">{{ reasonStocks(g) }}</span>
            <span class="mt-row-count">{{ g.stocks.length }} 家</span>
          </div>
        </div>
        <div v-else class="mt-hold">—</div>
      </section>
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>📈 百日新高</h3>
          <em>今日 {{ newHighToday }} 家</em>
          <button class="mt-more" @click="open('newhighs')">查看全部 ›</button>
        </div>
        <template v-if="newHighs">
          <svg viewBox="0 0 100 36" preserveAspectRatio="none" class="mt-svg" @click="open('newhighs')">
            <line v-for="y in [9, 18, 27]" :key="y" x1="0" :x2="100" :y1="y" :y2="y" stroke="#f0f0f0" stroke-width="1"/>
            <polyline :points="nhPoints" fill="none" stroke="#e67e22" stroke-width="1.5"/>
          </svg>
        </template>
        <div v-else class="mt-hold">—</div>
      </section>
    </div>

    <!-- ⑦ 龙虎榜: TOP10 条形榜 -->
    <div v-show="tab === 'lhb'" class="mt-pane">
      <section class="mt-sec mt-sec-plain">
        <div class="mt-sec-head">
          <h3>🐉 龙虎榜</h3>
          <em v-if="lhbCount">{{ lhbCount }} 家</em>
          <button class="mt-more" @click="open('lhb')">查看全部 ›</button>
        </div>
        <div v-if="lhbTop10.length">
          <div v-for="(s, i) in lhbTop10" :key="s.code" class="mt-rank" @click="goStock(s)">
            <span class="mt-rank-no">{{ RANK_NOS[i] }}</span>
            <span class="mt-row-name">{{ s.name }}</span>
            <span v-if="s.joinNum" class="mt-badge-jg" title="有机构席位">机构</span>
            <span v-if="s.buyIn >= 3e8" class="mt-badge-key" title="净买≥3亿">重点</span>
            <span class="mt-bar-track"><span class="mt-bar" :style="lhbBar(s)"></span></span>
            <span class="mt-rank-val" :style="{ color: s.buyIn >= 0 ? '#c0392b' : '#27ae60' }">{{ fmtYi(s.buyIn) }}</span>
            <span class="mt-rank-chg hide-xs" :style="{ color: s.chgPct >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(s.chgPct) }}</span>
          </div>
        </div>
        <div v-else class="mt-hold">—</div>
      </section>
    </div>

    <!-- ⑧ 更多: 外围 + 机构 -->
    <div v-show="tab === 'more'" class="mt-pane">
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>🌍 外围市场</h3>
          <button class="mt-more" @click="open('global')">查看全部 ›</button>
        </div>
        <div v-if="globalIndexes.length">
          <div v-for="g in globalIndexes" :key="g.code" class="mt-row" @click="open('global')">
            <span class="mt-row-name">{{ g.name }}</span>
            <span class="mt-row-val">{{ fmt(g.last) }}</span>
            <span class="mt-row-chg" :style="{ color: g.chgPct >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(g.chgPct) }}</span>
          </div>
        </div>
        <div v-else class="mt-hold">—</div>
      </section>
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>🏦 机构增仓</h3>
          <button class="mt-more" @click="open('institution')">查看全部 ›</button>
        </div>
        <div v-if="instTop8.length">
          <div v-for="g in instTop8" :key="g.bkCode" class="mt-row" @click="open('institution')">
            <span class="mt-row-name">{{ g.bkName }}</span>
            <span class="mt-row-str" style="color:#c0392b;">{{ fmt(g.addAmt, 1) }} 亿</span>
          </div>
        </div>
        <div v-else class="mt-hold">—</div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.mt-page { padding: 4px 14px 12px; overflow-x: clip; }

/* ── ① 情绪总览条 ── */
.mt-hero { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 12px 14px 10px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.03); position: sticky; top: 52px; z-index: 30; }
.mt-hero-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.mt-hero-strength { display: flex; align-items: baseline; gap: 6px; cursor: pointer; }
.mt-hero-val { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1; }
.mt-hero-label { font-size: 13px; font-weight: 600; }
.mt-hero-right { flex-shrink: 0; }
.mt-hero-effect { font-size: 12px; color: #555; margin-top: 7px; cursor: pointer; }
.mt-hero-effect b { font-weight: 600; }

/* ── ② 分段 Tab ── */
/* ── ② 竞价抢筹 (常驻) ── */
.mt-auction { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 9px 14px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.03); cursor: pointer; }
.mt-auction-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.mt-auction-row + .mt-auction-row { margin-top: 6px; }
.mt-auction-reason { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; color: #333; }
.mt-auction-stock { display: inline-flex; align-items: center; gap: 3px; font-size: 13px; color: #333; background: #f7f9fc; border-radius: 6px; padding: 3px 8px; white-space: nowrap; }
.mt-auction-stock i { font-style: normal; color: #2980b9; font-size: 11px; }
.mt-auction-empty { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #999; }
.mt-tabs { display: flex; gap: 4px; background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 4px; margin-bottom: 10px; }
.mt-tab { flex: 1; padding: 8px 0; font-size: 13px; font-weight: 500; color: #666; background: #f0f2f5; border: none; border-radius: 8px; cursor: pointer; }
.mt-tab.on { background: #2980b9; color: #fff; font-weight: 600; }

/* ── ③ 区块 ── */
.mt-pane { margin-bottom: 12px; }
.mt-sec { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 10px 12px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.03); }
.mt-sec-plain { padding: 4px 12px; }
.mt-sec-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mt-sec-head h3 { font-size: 13px; font-weight: 600; color: #111; margin: 0; }
.mt-sec-head em { font-size: 11px; color: #999; font-style: normal; }
.mt-more { margin-left: auto; font-size: 11px; color: #2980b9; background: none; border: none; padding: 0; cursor: pointer; flex-shrink: 0; }

/* ── 通用行: 名称列 flex:1+min-width:0 防溢出 ── */
.mt-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.mt-row:last-child { border-bottom: none; }
.mt-row-name { flex: 1; min-width: 0; font-size: 13px; color: #333; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-row-chg { width: 56px; flex: none; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; }
.mt-row-val { width: 64px; flex: none; text-align: right; font-size: 12px; color: #666; font-variant-numeric: tabular-nums; }
.mt-row-count { font-size: 11px; color: #999; flex: none; }
.mt-row-str { flex: none; font-size: 12px; color: #888; font-weight: 500; max-width: 45%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-hold { font-size: 11px; color: #bbb; padding: 8px 0; }
.mt-line { font-size: 12px; color: #555; margin: 4px 0; line-height: 1.6; }
.mt-line.small { font-size: 11px; }
.mt-tag { display: inline-block; background: #f0f2f5; color: #666; font-size: 10px; padding: 0 5px; border-radius: 4px; margin-right: 4px; }

/* ── 强度/净买条形 ── */
.mt-bar-track { flex: 1; min-width: 30px; height: 6px; background: #f5f5f5; border-radius: 3px; overflow: hidden; }
.mt-bar { display: block; height: 100%; border-radius: 3px; }

/* ── 徽标 ── */
.mt-badge { font-size: 10px; padding: 2px 8px; border-radius: 20px; }
.mt-badge.ok { background: #fdecea; color: #c0392b; }
.mt-badge.no { background: #eafaf1; color: #27ae60; }
.mt-badge-jg { flex: none; background: #fdecea; color: #c0392b; font-size: 9px; padding: 1px 4px; border-radius: 4px; }
.mt-badge-key { flex: none; background: #c0392b; color: #fff; font-size: 9px; padding: 1px 4px; border-radius: 4px; }
.mt-bk-tag { flex: none; font-size: 10px; color: #2980b9; background: #eaf6fb; padding: 1px 6px; border-radius: 10px; }
.mt-hot { color: #c0392b; font-weight: 600; }
.mt-delta { font-size: 11px; font-weight: 600; margin-left: 2px; }
.mt-tl-time { font-size: 10px; color: #bbb; flex: none; font-variant-numeric: tabular-nums; }

/* ── 情绪温度计: 5档色带 + 指针(位置=强度值) ── */
.mt-gauge { margin: 8px 0; cursor: pointer; }
.mt-gauge-bar { position: relative; display: flex; height: 8px; border-radius: 4px; }
.mt-gauge-seg { flex: 1; }
.mt-gauge-seg:first-child { border-radius: 4px 0 0 4px; }
.mt-gauge-seg:last-child { border-radius: 0 4px 4px 0; }
.mt-gauge-dot { position: absolute; top: -3px; width: 14px; height: 14px; border-radius: 50%; background: #fff; border: 2.5px solid #333; transform: translateX(-50%); }
.mt-gauge-scale { display: flex; justify-content: space-between; font-size: 9px; color: #bbb; margin-top: 3px; }

/* ── 涨停/连板/跌停 KPI 块 ── */
.mt-kpis { display: flex; gap: 6px; cursor: pointer; }
.mt-kpi { flex: 1; background: #f8fafc; border-radius: 8px; padding: 5px 0 4px; text-align: center; }
.mt-kpi b { display: block; font-size: 15px; font-weight: 700; line-height: 1.2; }
.mt-kpi span { font-size: 10px; color: #999; }
.k-up { color: #e74c3c; }
.k-down { color: #27ae60; }

/* ── 时间线 ── */
.mt-timeline { margin-left: 6px; padding-left: 18px; border-left: 2px solid #f0f0f0; }
.mt-tl-item { position: relative; display: flex; align-items: center; gap: 6px; padding: 8px 0; cursor: pointer; }
.mt-tl-dot { position: absolute; left: -22.5px; top: 14px; width: 8px; height: 8px; border-radius: 50%; background: #2980b9; }
.mt-tl-tag { flex: none; font-size: 9px; color: #c0392b; background: #fdecea; padding: 1px 5px; border-radius: 4px; }
.mt-tl-text { flex: 1; min-width: 0; font-size: 12px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── 涨停天梯 ── */
.mt-group-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.mt-group-row:last-child { border-bottom: none; }
.mt-group-tag { flex: none; border-radius: 16px; padding: 3px 10px; background: #f0f2f5; color: #666; font-size: 12px; font-weight: 600; }
.mt-group-tag.hot { background: #fdecea; color: #c0392b; }
.mt-group-count { flex: none; font-size: 11px; color: #999; }
.mt-group-stocks { flex: 1; min-width: 0; font-size: 11px; color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ── 龙虎榜行: 名次 + 条形 + 净买(+涨幅 <480px 隐藏) ── */
.mt-rank { display: flex; align-items: center; gap: 6px; padding: 8px 0; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.mt-rank:last-child { border-bottom: none; }
.mt-rank-no { font-size: 10px; color: #999; width: 14px; flex: none; }
.mt-rank-val { width: 60px; flex: none; text-align: right; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }
.mt-rank-chg { width: 52px; flex: none; text-align: right; font-size: 11px; font-variant-numeric: tabular-nums; }

/* ── 折线 ── */
.mt-svg { width: 100%; height: 36px; display: block; margin-top: 4px; cursor: pointer; }

@media (max-width: 480px) {
  .mt-page { padding-left: 10px; padding-right: 10px; }
  .hide-xs { display: none; }
}
@media (min-width: 768px) {
  .mt-page { padding: 8px 28px 20px; }
  .mt-sec { padding: 14px; }
}
</style>
