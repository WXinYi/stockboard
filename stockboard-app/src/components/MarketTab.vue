<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction, fetchMyPositions, fetchLianbanBid, fetchStrikeReview } from '../data/loader.js'
import { jsonp, secid } from '../utils/eastmoney.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchFengKou, fetchTianTi, fetchMarketLimitReasons, fetchNewHighTrend,
  fetchGlobalIndexes, fetchInstitutionIncrease,
  fetchMarketMood, fetchMarketHighlights, fetchLhbList,
  fetchBoardAnnotations, fetchMoneyEffect,
  fetchLimitPool, fetchRiseFall, fetchUnsealedPool,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'
import { loadCycleData, STAGE_COLORS, STAGE_RULES } from '../utils/emotionCycle.js'
import { loadBattleData, candTipOf as candTip, reviewVerdict } from '../utils/leaderBattle.js'

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
// 今日决策(情绪周期+龙头博弈): cycle 供决策卡, battle 供出击Tab
const cycle = ref(null)
const battle = ref(null)
const cycleDataDay = ref('')
// 我的纪律卡入口: 持仓价位表(管线收盘口径) + 阶段→仓位上限
const mine = ref(null)
const discTouchN = computed(() => (mine.value?.positions || []).filter(p => p.touch).length)
const discRule = computed(() => STAGE_RULES[cycle.value?.stage] || null)
const isBan = computed(() => ['退潮', '冰点', '分歧'].includes(cycle.value?.stage))
const discOpsTxt = computed(() => {
  const o = mine.value?.ops_review
  if (o?.items?.length) return `最新交易日 ${o.items.length} 笔：❌${o.bad} ⚠️${o.warn} ✅${o.ok}（${o.stage}期）`
  return mineSummary.value
})
const mineSummary = computed(() => {
  const m = mine.value
  if (!m) return '持仓价位表 · 冰点进度 · 每日三行卡'
  return `持仓 ${m.positions?.length ?? 0} 只 · ${m.weekly_focus || ''}`
})
const rbrStars = computed(() => {
  const bp = mine.value?.battle_plan
  if (!bp?.stage) return ''
  const base = { 退潮: 1, 冰点: 2, 分歧: 2, 高潮: 2, 启动: 4, 发酵: 4 }[bp.stage] ?? 2
  let stars = base
  if (bp.licenses?.trial) stars = Math.max(stars, 3)
  if (bp.licenses?.recover) stars = 5
  return '★'.repeat(stars) + '☆'.repeat(5 - stars)
})
const yjTxt = computed(() => STAGE_RULES[cycle.value?.stage]?.yj || '')

async function loadMine(silent = false) {
  try { mine.value = await fetchMyPositions() } catch (e) { if (!silent) console.error('[MarketTab mine]', e?.message) }
}

async function loadCycleBattle(silent = false) {
  try {
    const day = await getLatestTradingDay()
    const dayDash = day ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : ''
    const cd = await loadCycleData({ fetchTianTi, fetchLimitPool, fetchRiseFall, fetchMarketMood }, dayDash)
    cycle.value = cd.cycle
    cycleDataDay.value = cd.cycle?.date || dayDash
    // 复核文件与竞价换手并行预取: review 的 prev_broken 供引擎标记"弱转强·炸板回封"
    const [lb, rv] = await Promise.all([fetchLianbanBid().catch(() => null), fetchStrikeReview().catch(() => null)])
    review.value = rv
    if (rv?.picks?.length) {
      const m = {}
      for (const q of await fetchEmPct(rv.picks.map(p => p.code))) m[q.code] = q.pct
      reviewPct.value = m
    }
    if (rv?.today_wzq?.length) {
      const m2 = {}
      for (const q of await fetchEmPct(rv.today_wzq.map(p => p.code))) m2[q.code] = q.pct
      wzqPct.value = m2
    }
    battle.value = await loadBattleData({ fetchLimitPool, fetchUnsealedPool }, cd, lb, (rv?.prev_broken || []).map(p => p.code))
  } catch (e) { if (!silent) console.error('[MarketTab cycle]', e?.message) }
}

// ── 昨日可买复核: 前一交易日 9:25 选股的可做名单 → 今日实时涨幅逐只判定 持有/减半/开盘走/清仓 ──
const review = ref(null)
const reviewPct = ref({})
const wzqPct = ref({})
// 今日竞价弱转强(9:25 口径): 昨日分歧+竞价超预期, 分时确认才上; 回封(现价触板)则标已回封
const limOf = c => /^(4|8|92)/.test(c) ? 30 : /^(688|689|300|301)/.test(c) ? 20 : 10
const wzqRows = computed(() => {
  const r = review.value
  if (!r?.today_wzq?.length) return []
  return r.today_wzq.map(p => {
    const pct = wzqPct.value[p.code] ?? null
    return { ...p, pct, resealed: pct != null && pct >= limOf(p.code) - 0.5 }
  })
})
async function fetchEmPct(codes) {
  const rows = []
  for (let i = 0; i < codes.length; i += 40) {
    const url = `https://push2delay.eastmoney.com/api/qt/ulist.np/get?secids=${codes.slice(i, i + 40).map(secid).join(',')}&fields=f2,f3,f12,f14&fltt=2&invt=2`
    try {
      const j = await jsonp(url, 'cb')
      const diff = j?.data?.diff
      for (const d of (Array.isArray(diff) ? diff : Object.values(diff || {}))) {
        const pct = parseFloat(d.f3)
        rows.push({ code: String(d.f12 ?? ''), pct: isNaN(pct) ? null : pct })
      }
    } catch (e) { /* 单批失败忽略 */ }
  }
  return rows
}
const reviewRows = computed(() => {
  const r = review.value
  if (!r?.picks?.length) return []
  const stage = cycle.value?.stage || r.stage
  return r.picks.map(p => {
    const pct = reviewPct.value[p.code] ?? null
    return { ...p, pct, ...reviewVerdict(pct, p.code, stage) }
  })
})
// 防过期: 复核文件日期须与页面数据日一致, 否则视为旧数据不展示
const reviewValid = computed(() => review.value && (!cycle.value?.date || review.value.date === cycle.value.date))
// 涅槃六情绪定性(市场/投机/板块×短期/整体): 主导条件决定战术偏向
const sixTxt = computed(() => {
  const x = review.value?.six
  if (!x?.dominant) return ''
  return `市场${x.market ?? '—'} 投机${x.spec ?? '—'} 板块${x.sector ?? '—'}（整体 ${x.m_market ?? '—'}/${x.m_spec ?? '—'}/${x.m_sector ?? '—'}）→ 主导: ${x.dominant} · ${x.note}`
})

async function loadAll(silent = false) {
  loadMine(silent)
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
    loadCycleBattle(true)
  }, 30000)
}
function stopTimer() { clearInterval(timer); timer = null }
function onVisibility() {
  if (document.hidden) { stopTimer(); return }
  if (!isTradingTime()) return
  startTimer(); loadAll(true)
}

// 下拉刷新: 仅当前激活页面响应(usePullRefresh 按激活态过滤)
usePullRefresh(() => { loadAll(); loadAuction(); loadCycleBattle() })

// KeepAlive: onUnmounted 不触发 → 轮询必须 onDeactivated 停 / onActivated 恢复, 防泄漏防重复(startTimer 有 guard)
let inited = false
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
})
onActivated(() => {
  if (!inited) { inited = true; loadAll(); loadAuction(); loadCycleBattle() } else if (isTradingTime()) { loadAll(true); loadCycleBattle(true) }
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

// ── 分段 Tab(出击优先) ──
const tab = ref('strike')
const TABS = [
  { key: 'strike', label: '🎯 出击' },
  { key: 'board', label: '板块' },
  { key: 'live', label: '异动' },
  { key: 'zt', label: '涨停' },
  { key: 'lhb', label: '龙虎榜' },
  { key: 'more', label: '更多' },
]

// ── 今日决策(情绪周期+龙头博弈) ──
const stageName = computed(() => cycle.value?.stage || '')
const stageColor = computed(() => STAGE_COLORS[stageName.value] || '#8a97a8')
const gateTxt = computed(() => {
  const g = battle.value?.strike?.gate
  if (!g) return ''
  if (g.cap === 0) return '禁买'
  if (g.cap >= 100) return '可出击'
  return `限${g.cap}分`
})
const strikeTop3 = computed(() => (battle.value?.strike?.candidates || []).filter(c => c.status.startsWith('出击') || c.status.startsWith('备选')).slice(0, 3))
const strikeAll = computed(() => battle.value?.strike?.candidates || [])
// 出击Tab默认只展开前3只(按评分排序), 其余折叠
const showAllStrike = ref(false)
const strikeShown = computed(() => (showAllStrike.value ? strikeAll.value : strikeAll.value.slice(0, 3)))
const gateBanner = computed(() => (battle.value?.strike?.gate?.banner || '').split('📐')[0].trim())
const gateMatrix = computed(() => battle.value?.strike?.gate?.matrix || null)
const warTop4 = computed(() => (battle.value?.boardWars?.wars || []).slice(0, 4))
const mainSwitchNote = computed(() => battle.value?.boardWars?.mainSwitch?.note || '')
const brokenHighsTop = computed(() => (battle.value?.risks?.brokenHighs || []).slice(0, 4))

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
// 比例条: 大赚占(赚+亏)比例 → 主页红绿条(左红右绿, 一眼看多空力量)
const effectBar = computed(() => {
  const total = effectGain.value + effectLoss.value
  return total ? (effectGain.value / total * 100).toFixed(1) + '%' : '50%'
})

// ── 板块 Tab: 最强风口 + 板块标注 ──
const windTop8 = computed(() => (wind.value || []).slice(0, 8))
const windBarMax = computed(() => Math.max(...(wind.value || []).map(w => w.strength || 0), 1))
const windBar = w => ({ width: (w.strength / windBarMax.value * 100).toFixed(1) + '%', background: '#e67e22' })
const annotationsTop = computed(() => [...(annotations.value || [])].reverse().slice(0, 10))   // 接口按时间正序 → 最新在上
// 标注情绪色: 1=利多红 / -1=利空绿 / 0=中性灰(接口 Color 字段)
function annColor(c) { return c === '1' ? '#e74c3c' : c === '-1' ? '#27ae60' : '#d5dbe3' }
const fmtAmt = v => (typeof v === 'number' && isFinite(v) && v > 0) ? (v / 1e8).toFixed(1) + '亿' : ''
// 风口/标注是盘中实时接口: 盘外(非交易时段)恒空, 提示等待开盘
const windHold = computed(() => isTradingTime() ? '暂无数据' : '盘外暂无 · 开盘后自动更新')

// ── 异动 Tab: 盘面亮点时间线(接口正序 → 反转最新在上) ──
const liveTop = computed(() => [...(live.value || [])].reverse().slice(0, 10))
const liveCount = computed(() => (live.value || []).length)
// 数据日期提示: 亮点接口可能返回前一日数据(数据源滞后) → 标题标注, 避免误认当天
const liveDataDate = computed(() => {
  const t = live.value?.[0]?.time
  if (!t) return ''
  const d = new Date(t * 1000)
  const now = new Date()
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
})

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
// KPL 接口时间是真实 Unix 秒(绝对时间点, 用本地时区显示); 数据可能滞后(非当天) → 带日期, 避免误认当天
function fmtTime(t) {
  if (!t) return '—'
  const d = new Date(t * 1000)
  const now = new Date()
  const hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0')
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) return hm
  const md = String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0')
  return d.getFullYear() === now.getFullYear() ? `${md} ${hm}` : `${d.getFullYear()}-${md} ${hm}`
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
            <span class="mt-hero-range">/100</span>
            <span class="mt-hero-label" :style="{ color: moodColor(moodToday.strong) }">{{ moodLabel(moodToday.strong) }}</span>
            <span v-if="moodDelta !== null" class="mt-delta" :style="{ color: moodDelta >= 0 ? '#e74c3c' : '#27ae60' }" title="较上一交易日">较昨日 {{ moodDelta >= 0 ? '+' : '' }}{{ moodDelta }}</span>
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
      <div v-if="effect" class="mt-hero-effect" @click="open('mood')" title="大赚/大亏 = 按当日涨跌幅分档统计的家数(5档为最极端), 非全市场涨跌家数">
        <span class="me-tag">赚钱效应</span>
        <span class="me-gain-num">大赚 {{ effectGain }}</span>
        <span class="me-bar"><i class="me-bar-g" :style="{ width: effectBar }"></i></span>
        <span class="me-loss-num">大亏 {{ effectLoss }}</span>
        <span class="me-net" :style="{ color: effectNet >= 0 ? '#c0392b' : '#27ae60' }">{{ effectNet >= 0 ? '净赚' : '净亏' }} {{ Math.abs(effectNet) }} 家</span>
      </div>

      <!-- 今日决策卡: 阶段+闸门+出击top3+警示(点击进 cycle 详情) -->
      <div class="mt-decide" :style="{ '--sc': stageColor }" @click="open('cycle')">
        <template v-if="stageName && battle && !battle.empty">
          <div class="mtd-top">
            <span class="mtd-stage" :style="{ color: stageColor }">{{ stageName }}</span>
            <span class="mtd-gate" :class="{ ban: gateTxt === '禁买' }">{{ gateTxt }}</span>
            <span class="mtd-more">决策详情 ›</span>
          </div>
          <div v-if="strikeTop3.length" class="mtd-strikes">
            <span v-for="c in strikeTop3" :key="c.code" class="mtd-stock" :class="{ go: c.status.startsWith('出击') }" @click.stop="goStock(c)">
              <b>{{ c.name }}</b><i>{{ c.score }}</i>
            </span>
            <span v-if="strikeAll.length > strikeTop3.length" class="mtd-plus">+{{ strikeAll.length - strikeTop3.length }}</span>
          </div>
          <div v-else class="mtd-none">本阶段无出击候选（纪律优先）</div>
          <div v-if="mainSwitchNote || brokenHighsTop.length" class="mtd-alert">
            <span v-if="mainSwitchNote">🔄 {{ mainSwitchNote }}</span>
            <span v-if="brokenHighsTop.length">🚨 高标开板: {{ brokenHighsTop.map(b => b.name).join('、') }}</span>
          </div>
          <div v-if="yjTxt" class="mtd-yj">⚖️ {{ rbrStars }} ｜ 💬 {{ yjTxt }}</div>
        </template>
        <template v-else>
          <div class="mtd-top">
            <span class="mtd-stage" style="color:#8a97a8">🧭 情绪周期</span>
            <span class="mtd-more">决策详情 ›</span>
          </div>
          <div class="mtd-none">{{ cycleDataDay ? `${cycleDataDay} 数据加载中…` : '打开时实时计算六段阶段 · 出击清单' }}</div>
        </template>
      </div>

      <!-- 我的纪律卡入口: 阶段→仓位上限 + 持仓触价提醒 -->
      <div class="mt-decide disc-entry" @click="open('discipline')">
        <div class="mtd-top">
          <span class="mtd-stage" style="font-size:16px">🧭 纪律卡</span>
          <span class="mtd-gate" :class="{ ban: isBan || discTouchN }">{{ discRule?.cap || '—' }}</span>
          <span class="mtd-more">持仓价位表 ›</span>
        </div>
        <div class="mtd-none" :style="discTouchN ? 'color:#e67e22;font-weight:700' : ''">
          {{ discTouchN ? `⚠️ ${discTouchN} 只持仓触价待执行` : discOpsTxt }}
        </div>
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

    <!-- ④ 出击: 今日出击清单 + 板块之争精简 + 高标开板 -->
    <div v-show="tab === 'strike'" class="mt-pane">
      <section v-if="battle && !battle.empty" class="mt-sec">
        <div class="mt-sec-head">
          <h3>🎯 今日出击</h3>
          <em>{{ battle.strike.gate.stage }} · 上限 {{ battle.strike.gate.cap || '禁买' }}</em>
          <button class="mt-more" @click="open('cycle')">决策详情 ›</button>
        </div>
        <div class="mt-strike-banner">
          <div>{{ gateBanner }}</div>
          <div v-if="battle.strike.relay?.txt" class="sb-mtx">🗡 {{ battle.strike.relay.txt }}</div>
          <div v-if="gateMatrix" class="sb-mtx">📐 高位{{ gateMatrix.high }}×中位{{ gateMatrix.mid }}：{{ gateMatrix.note }}</div>
        </div>
        <div v-for="c in strikeShown" :key="c.code" class="mt-strike" :class="'st-' + (c.status.startsWith('出击') ? 'go' : c.status.startsWith('备选') ? 'alt' : 'watch')" @click="goStock(c)">
          <div class="mt-strike-top">
            <b>{{ c.name }}</b>
            <span v-if="c.level" class="mt-strike-lv">{{ c.level }}板</span>
            <span class="mt-strike-plates">{{ c.platesTxt }}</span>
            <span class="mt-strike-score" :class="{ hi: c.score >= 75 }">{{ c.score }}</span>
            <span class="mt-strike-status">{{ c.status }}</span>
          </div>
          <div class="mt-strike-mid">
            <span v-if="c.bidTop" class="lb-bidtop" :title="`昨日${c.bidTop.prevPid}板 · 竞价实际换手 ${c.bidTop.hs.toFixed(2)}%（昨日连板股第 ${c.bidTop.rank} 名）`">🔥 竞价换手TOP{{ c.bidTop.rank }}</span>
            <span v-if="c.roleTxt" class="lb-role" :class="{ feng: c.roleTxt === '跟风', huo: c.roleTxt === '火种' }">🏷 {{ c.roleTxt }}</span>
            <span class="mt-strike-mode">{{ c.mode }}</span>
            <span v-if="c.sealTxt">{{ c.sealTxt }}</span>
            <span v-if="c.strength">💪 {{ c.strength }}</span>
          </div>
          <div class="mt-strike-logic">{{ c.logic }}</div>
          <div class="mt-strike-tip">🎯 {{ candTip(c, battle.strike.gate.cap).buy }} ｜ 触发：{{ candTip(c, battle.strike.gate.cap).trigger }} ｜ 止损：{{ candTip(c, battle.strike.gate.cap).stop }} ｜ {{ candTip(c, battle.strike.gate.cap).pos }}</div>
          <div v-if="c.risk" class="mt-strike-risk">⚠️ {{ c.risk }}</div>
        </div>
        <div v-if="strikeAll.length > 3" class="mt-strike-toggle" @click="showAllStrike = !showAllStrike">
          {{ showAllStrike ? '收起 ▲' : `展开全部 ${strikeAll.length} 只 ▼` }}
        </div>
        <div v-if="!strikeAll.length" class="mt-hold">本阶段无出击候选（纪律优先）</div>
        <div class="mt-strike-note">{{ battle.strike.disclaimer }}</div>
      </section>
      <section v-if="reviewValid && review?.six" class="mt-sec">
        <div class="mt-sec-head">
          <h3>🌡 六情绪定性</h3>
          <em>涅槃框架 · 0-100 历史分位</em>
        </div>
        <div class="mt-review-guide">🌡 {{ sixTxt }}</div>
      </section>
      <section v-if="reviewValid && review" class="mt-sec">
        <div class="mt-sec-head">
          <h3>🌅 今日竞价弱转强</h3>
          <em>{{ review.date }} 9:25 竞价口径</em>
        </div>
        <div v-if="!wzqRows.length" class="mt-hold">今日无竞价弱转强（{{ review.stage }}期不开放，仅启动/发酵/分歧）</div>
        <div v-for="row in wzqRows" :key="row.code" class="mt-review" @click="goStock(row)">
          <div class="mt-strike-top">
            <b>{{ row.name }}</b>
            <span class="mt-row-count">昨日{{ row.tag }}分歧 · 竞价 {{ row.bid_pct }}%<template v-if="row.pct != null"> · 现 {{ row.pct > 0 ? '+' : '' }}{{ row.pct }}%</template></span>
            <span class="rv-tag" :class="row.resealed ? 'hold' : 'warn'">{{ row.resealed ? '已回封✅' : '待分时确认' }}</span>
          </div>
          <div class="mt-review-today rv-warn">分时确认才上：站稳分时均价/放量转强再买，失败 -3% 止损</div>
        </div>
      </section>
      <section v-if="reviewValid && review" class="mt-sec">
        <div class="mt-sec-head">
          <h3>📋 昨日可买复核</h3>
          <em>{{ review.prev_day }} 9:25 选股 · {{ review.date }} 执行 · {{ review.src || '重算' }}</em>
        </div>
        <div class="mt-review-guide">怎么用：昨天 9:25 选股单上的可买票，今天按实时涨幅逐只执行——封板=持有到尾盘一致转分歧兑现；涨3%以上=冲高兑现（卖在一致）；平盘弱=先出一半（弱于预期）；水下=开盘走（低于预期即卖）；今日周期转退潮/冰点=全部清仓（只卖不买）。</div>
        <div v-if="!review.picks.length" class="mt-hold">{{ review.stage }}期无可买（空仓纪律正确）✓</div>
        <div v-for="row in reviewRows" :key="row.code" class="mt-review" @click="goStock(row)">
          <div class="mt-strike-top">
            <b>{{ row.name }}</b>
            <span class="mt-strike-lv">{{ row.height }}板</span>
            <span class="mt-row-count">{{ row.pct == null ? `竞价 ${row.bid_pct ?? '—'}%` : (row.pct > 0 ? '+' : '') + row.pct + '%' }}</span>
            <span class="rv-tag" :class="row.cls">{{ row.tag }}</span>
          </div>
          <div class="mt-strike-logic">昨日：{{ row.reason }}</div>
          <div class="mt-review-today" :class="'rv-' + row.cls">今日：{{ row.txt }}</div>
        </div>
      </section>
      <section v-if="warTop4.length" class="mt-sec">
        <div class="mt-sec-head">
          <h3>⚔️ 板块之争</h3>
          <em v-if="mainSwitchNote" style="color:#e67e22">{{ mainSwitchNote }}</em>
          <button class="mt-more" @click="open('cycle')">全部 ›</button>
        </div>
        <div v-for="w in warTop4" :key="w.board" class="mt-war-row" @click="open('cycle')">
          <span class="mt-war-tag" :class="{ hot: w.tag === '主线' }">{{ w.tag }}</span>
          <span class="mt-row-name">{{ w.board }}</span>
          <span class="mt-row-count">今 {{ w.count }} 只</span>
          <span class="mt-war-delta" :class="w.dCount >= 0 ? 'up' : 'dn'">{{ w.dCount >= 0 ? '+' : '' }}{{ w.dCount }}</span>
          <span class="mt-war-sub">昨 {{ w.prevCount }} · 最高{{ w.maxH }}板</span>
        </div>
      </section>
      <section v-if="brokenHighsTop.length" class="mt-sec mt-sec-plain">
        <div class="mt-sec-head">
          <h3>🚨 高标开板</h3>
          <em>盘中分歧信号</em>
          <button class="mt-more" @click="open('cycle')">全部 ›</button>
        </div>
        <div v-for="b in brokenHighsTop" :key="b.code" class="mt-row" @click="goStock(b)">
          <span class="mt-row-name">{{ b.name }}</span>
          <span class="mt-war-tag">{{ b.level }}板</span>
          <span class="mt-war-delta dn">{{ b.pct.toFixed(1) }}%</span>
          <span class="mt-war-sub">{{ b.note }}</span>
        </div>
      </section>
      <div v-if="!battle || battle.empty" class="mt-hold" style="padding:20px 0;text-align:center;">周期数据加载中…</div>
    </div>

    <!-- ⑤ 板块: 最强风口 + 板块标注 -->
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
            <span class="mt-ann-bar" :style="{ background: annColor(a.color) }" title="标注性质: 利多/利空/中性"></span>
            <span class="mt-tl-time">{{ fmtTime(a.time) }}</span>
            <span class="mt-row-name">{{ a.text }}</span>
            <span v-if="a.bkName" class="mt-bk-tag">{{ a.bkName }}</span>
            <span v-if="fmtAmt(a.je)" class="mt-ann-je">{{ fmtAmt(a.je) }}</span>
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
          <em v-if="liveDataDate" class="mt-lag">{{ liveDataDate }} 数据</em>
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
            <span v-if="s.joinNum" class="mt-badge-jg" title="有机构席位">机构×{{ s.joinNum }}</span>
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
.mt-hero { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 12px 14px 10px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.03); }
.mt-hero-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.mt-hero-strength { display: flex; align-items: baseline; gap: 6px; cursor: pointer; }
.mt-hero-val { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; line-height: 1; }
.mt-hero-range { font-size: 11px; color: #bbb; }   /* 0-100 评分锚点 */
.mt-hero-label { font-size: 13px; font-weight: 600; }
.mt-hero-right { flex-shrink: 0; }
.mt-hero-effect { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #555; margin-top: 7px; cursor: pointer; }
.me-tag { color: #999; flex-shrink: 0; }
.me-gain-num { color: #e74c3c; font-weight: 600; font-variant-numeric: tabular-nums; }
.me-loss-num { color: #27ae60; font-weight: 600; font-variant-numeric: tabular-nums; }
.me-net { font-weight: 700; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.me-bar { flex: 1; height: 8px; min-width: 40px; max-width: 110px; border-radius: 4px; background: #27ae60; overflow: hidden; }
.me-bar-g { display: block; height: 100%; background: #e74c3c; }

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
.mt-sec { background: #fff; border: 1px solid #eef1f5; border-radius: 12px; padding: 10px 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(17,24,39,.04), 0 4px 14px rgba(17,24,39,.05); }
.mt-sec-plain { padding: 4px 12px; }
.mt-sec-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mt-sec-head h3 { font-size: 13px; font-weight: 600; color: #111; margin: 0; }
.mt-sec-head em { font-size: 11px; color: #999; font-style: normal; }
.mt-sec-head em.mt-lag { color: #e67e22; }   /* 数据滞后提示(非当天数据) */
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
.mt-ann-bar { flex: none; width: 3px; height: 14px; border-radius: 2px; }
.mt-ann-je { flex: none; font-size: 10px; color: #888; font-variant-numeric: tabular-nums; }
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

/* 情绪周期入口 */
/* 今日决策卡: 阶段色渐变 hero(--sc 由模板注入) */
.mt-decide { --sc: #8a97a8; background: #fff; background-image: linear-gradient(135deg, color-mix(in srgb, var(--sc) 12%, #fff) 0%, #fff 58%); border: 1px solid #e5e9f0; border-color: color-mix(in srgb, var(--sc) 24%, #e5e9f0); border-radius: 12px; padding: 12px; margin: 10px 0 0; cursor: pointer; box-shadow: 0 4px 14px color-mix(in srgb, var(--sc) 14%, rgba(17,24,39,.04)); }
.disc-entry { --sc: #e67e22; }
.mtd-top { display: flex; align-items: center; gap: 8px; }
.mtd-stage { font-size: 22px; font-weight: 800; letter-spacing: 2px; }
.mtd-gate { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; background: #ff5a5a; color: #fff; }
.mtd-gate.ban { background: #9b6bde; }
.mtd-more { margin-left: auto; font-size: 11px; color: #bbb; }
.mtd-strikes { display: flex; align-items: center; gap: 6px; margin-top: 9px; padding-top: 9px; border-top: 1px dashed #e5e9f0; border-top-color: color-mix(in srgb, var(--sc) 25%, #e5e9f0); flex-wrap: wrap; }
.mtd-stock { display: inline-flex; align-items: center; gap: 5px; border: 1px solid #eef1f5; border-radius: 8px; padding: 4px 8px; font-size: 13px; background: #fff; }
.mtd-stock.go { border-color: #ff5a5a; background: #fff5f5; }
.mtd-stock i { font-style: normal; font-weight: 800; color: #ff5a5a; }
.mtd-plus { font-size: 11px; color: #999; }
.mtd-none { font-size: 12px; color: #999; margin-top: 8px; }
.mtd-alert { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; margin-top: 9px; font-size: 11px; color: #b9770e; }
.mtd-yj { margin-top: 8px; padding-top: 8px; border-top: 1px dashed color-mix(in srgb, var(--sc) 25%, #e5e9f0); font-size: 11px; color: #8a6d3b; }
.mtd-alert span { background: #fff7e8; border-radius: 6px; padding: 3px 8px; line-height: 1.5; }
/* 出击 Tab */
.mt-strike-banner { font-size: 12px; color: #556; background: #f8fafc; border: 1px solid #e8edf3; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; line-height: 1.6; }
.sb-mtx { margin-top: 5px; padding-top: 6px; border-top: 1px dashed #dfe6ee; color: #2b6cb0; }
.mt-strike { border: 1px solid #e9edf3; border-left: 3px solid #cfd8e3; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer; background: #fbfcfe; }
.mt-strike.st-go { border-left-color: #ff5a5a; background: #fff5f5; }
.mt-strike.st-alt { border-left-color: #f5a623; background: #fffbf0; }
.mt-strike-top { display: flex; align-items: center; gap: 6px; }
.mt-strike-top b { font-size: 14px; }
.mt-strike-lv { font-size: 11px; color: #b8860b; font-weight: 700; }
.mt-strike-plates { font-size: 11px; color: #778; }
.mt-strike-score { margin-left: auto; font-size: 17px; font-weight: 800; color: #8a97a8; }
.mt-strike-score.hi { color: #ff5a5a; }
.mt-strike-status { font-size: 11px; padding: 1px 6px; border-radius: 4px; background: #eef1f5; color: #667; }
.st-go .mt-strike-status { background: #ff5a5a; color: #fff; }
.st-alt .mt-strike-status { background: #f5a623; color: #fff; }
.mt-strike-mid { display: flex; gap: 8px; font-size: 11px; color: #778; margin: 3px 0; flex-wrap: wrap; align-items: center; }
.lb-role { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 9px; background: #fdf3e7; color: #b06020; flex: none; }
.lb-role.feng { background: #f0f0f2; color: #999; }
.lb-role.huo { background: #fdeaea; color: #c04848; }
.lb-bidtop { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 9px; background: #fff1f0; color: #d4380d; flex: none; cursor: help; }
.mt-strike-mode { color: #2bc4a8; font-weight: 700; }
.mt-strike-logic { font-size: 12px; color: #556; }
.mt-strike-tip { font-size: 11px; color: #8a6d3b; background: #faf6ec; border-radius: 6px; padding: 4px 8px; margin-top: 4px; }
.mt-strike-toggle { text-align: center; font-size: 12px; color: #667; padding: 8px 0 2px; cursor: pointer; user-select: none; }
.mt-strike-toggle:active { opacity: .6; }
.mt-review { background: #fff; border: 1px solid #eceff3; border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; cursor: pointer; }
.mt-review-guide { font-size: 11px; color: #667; background: #f7f8fa; border-radius: 8px; padding: 6px 10px; margin-bottom: 8px; line-height: 1.6; }
.rv-tag { font-size: 10px; font-weight: 700; padding: 1px 8px; border-radius: 9px; margin-left: auto; flex: none; }
.rv-tag.hold { background: #fdecea; color: #c0392b; }
.rv-tag.warn { background: #fdf3e7; color: #b06020; }
.rv-tag.sell { background: #e8f6ee; color: #1e7e46; }
.mt-review-today { font-size: 12px; margin-top: 2px; font-weight: 600; }
.mt-review-today.rv-hold { color: #c0392b; }
.mt-review-today.rv-warn { color: #b06020; }
.mt-review-today.rv-sell { color: #1e7e46; }
.mt-strike-risk { font-size: 11px; color: #ff5a5a; margin-top: 2px; }
.mt-strike-note { font-size: 10px; color: #a0aab8; margin-top: 4px; }
.mt-war-row { display: flex; align-items: center; gap: 8px; font-size: 13px; padding: 5px 0; border-bottom: 1px dashed #eef1f5; cursor: pointer; }
.mt-war-tag { font-size: 10px; padding: 0 5px; border-radius: 4px; background: #eef1f5; color: #667; }
.mt-war-tag.hot { background: #ff5a5a; color: #fff; }
.mt-row-count { font-size: 12px; color: #556; }
.mt-war-delta { font-size: 11px; font-weight: 700; }
.mt-war-delta.up { color: #ff5a5a; }
.mt-war-delta.dn { color: #4cd964; }
.mt-war-sub { margin-left: auto; font-size: 11px; color: #999; }
</style>
