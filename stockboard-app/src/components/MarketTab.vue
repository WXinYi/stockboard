<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction, fetchMyPositions, fetchLianbanBid, fetchStrikeReview, fetchSixHistory } from '../data/loader.js'
import { jsonp, secid } from '../utils/eastmoney.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchTianTi, fetchGlobalIndexes, fetchInstitutionIncrease,
  fetchMarketMood, fetchLhbList,
  fetchLimitPool, fetchRiseFall, fetchUnsealedPool,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'
import { loadCycleData, STAGE_COLORS, STAGE_RULES } from '../utils/emotionCycle.js'
import { loadBattleData, candTipOf as candTip, reviewVerdict, gateSentence, whyNot } from '../utils/leaderBattle.js'
import Hint from './Hint.vue'
import { gateTier, statusWord, holdInfo, isMorning, resolveVerdict, sameDataDay, divergenceNote, pxLine, sortPicksFirst, sortReviewRows } from '../utils/stockPicks.js'
import { normHistory, buildLiveRow, computeSixLive, fetchIndexTrend, parseBidYi } from '../utils/sixEmotion.js'

defineOptions({ name: 'MarketTab' })

const router = useRouter()

// 竞价: 当日快照, 单次加载不轮询
const auction = ref(null)
const auctionLoading = ref(true)
// 速览/六情绪输入: 30s 轮询(silent)
const ladder = ref(null)
const global = ref(null)
const institution = ref(null)
const mood = ref(null)
const lhb = ref(null)
// 持仓快照: 仅用于候选「已持仓」标记(纪律卡已下线 09-07)
const mine = ref(null)
const discRule = computed(() => STAGE_RULES[cycle.value?.stage] || null)
const touchCount = computed(() => (mine.value?.positions || []).filter(p => p.touch).length)

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
    refreshSix()   // 六情绪实时版: 复用本轮已拉取的 KPL 数据, 不新增盘面请求
    loadStrikePx() // 候选实时价(一次批量行情, 供"距买点/止损参考")
  } catch (e) { if (!silent) console.error('[MarketTab cycle]', e?.message) }
}

// ── 昨日可买复核: 前一交易日 9:25 选股的可做名单 → 今日实时涨幅逐只判定 ──
const review = ref(null)
const reviewPct = ref({})
const wzqPct = ref({})
const showReview = ref(false)   // 复盘块默认收起, 不占首屏
const showMoreReview = ref(false)
// 今日竞价弱转强(9:25 口径): 回封(现价触板)则标已回封; 排序=最适合买入在前, 默认前5
const showMoreWzq = ref(false)
const limOf = c => /^(4|8|92)/.test(c) ? 30 : /^(688|689|300|301)/.test(c) ? 20 : 10
const wzqRows = computed(() => {
  const r = review.value
  if (!r?.today_wzq?.length) return []
  const rows = r.today_wzq.map(p => {
    const pct = wzqPct.value[p.code] ?? null
    return { ...p, pct, w: statusWord(p.status), resealed: pct != null && pct >= limOf(p.code) - 0.5 }
  })
  return sortPicksFirst(rows)
})
async function fetchEmPct(codes) {
  const rows = []
  for (let i = 0; i < codes.length; i += 40) {
    const url = `https://push2delay.eastmoney.com/api/qt/ulist.np/get?secids=${codes.slice(i, i + 40).map(secid).join(',')}&fields=f2,f3,f5,f6,f12,f14&fltt=2&invt=2`
    try {
      const j = await jsonp(url, 'cb')
      const diff = j?.data?.diff
      for (const d of (Array.isArray(diff) ? diff : Object.values(diff || {}))) {
        const pct = parseFloat(d.f3), price = parseFloat(d.f2)
        const vol = parseFloat(d.f5), amt = parseFloat(d.f6)
        rows.push({ code: String(d.f12 ?? ''), pct: isNaN(pct) ? null : pct, price: isNaN(price) ? null : price,
                    avg: (!isNaN(vol) && vol > 0 && !isNaN(amt) && amt > 0) ? amt / (vol * 100) : null })
      }
    } catch (e) { /* 单批失败忽略 */ }
  }
  return rows
}
const reviewRows = computed(() => {
  const r = review.value
  if (!r?.picks?.length) return []
  const stage = cycle.value?.stage || r.stage
  const rows = r.picks.map(p => {
    const pct = reviewPct.value[p.code] ?? null
    // 全量输出改版: buyable=false 的昨日候选 → 标「不出手」, 不做买卖判定
    const buyable = p.buyable ?? (statusWord(p.status).txt !== '只看不买')
    if (!buyable) return { ...p, pct, tag: '不出手', cls: 'watch', txt: '纪律禁买 · 仅观察' }
    return { ...p, pct, ...reviewVerdict(pct, p.code, stage) }
  })
  return sortReviewRows(rows)
})
// 防过期: 复核文件日期须与页面数据日一致, 否则视为旧数据不展示
const reviewValid = computed(() => review.value && (!cycle.value?.date || review.value.date === cycle.value.date))

// ── 情绪周期(选股依据): cycle 供结论头, battle 供出击 ──
const cycle = ref(null)
const battle = ref(null)
const cycleDataDay = ref('')

// ── 选股结论头(三层: 池判定 → 仓位上限 → 一句话结论; 口径见 utils/stockPicks.js / leaderBattle.gateSentence) ──
const verdictTier = computed(() => gateTier(battle.value?.strike?.gate?.cap))
const sixDominant = computed(() => {
  const x = review.value?.six
  // 六情绪日终存档: 数据日 ≠ 实时周期日时不冒充当日结论(实时版见 sixLive)
  if (!x?.dominant || !sameDataDay(review.value?.date, cycle.value?.date)) return ''
  return `主导 ${x.dominant}` + (x.note ? ` · ${x.note}` : '')
})
// 闸门开≠有可买: 结论必须与实际达标候选数一致(无达标→「仅观察」, 不误导)
const actCount = computed(() => (battle.value?.strike?.candidates || []).filter(c => statusWord(c.status).txt !== '只看不买').length)
const noCandidate = computed(() => verdictTier.value.verdict !== '禁买' && actCount.value === 0)
const verdictShow = computed(() => resolveVerdict(verdictTier.value, actCount.value))
// 首页只给人话结论: 阶段+矩阵禁买范围+池+是否达标(中间层数值在依据页)
const gateSentenceTxt = computed(() => gateSentence(
  cycle.value?.stage || '', gateMatrix.value?.high, gateMatrix.value?.mid,
  battle.value?.strike?.gate?.cap, noCandidate.value))
const dvg = computed(() => divergenceNote(sixLive.value?.dominant, verdictShow.value, actCount.value))
// 存档阶段 vs 实时阶段: 仅同一天才标注(跨日不冒充"今日早盘"); 措辞按快照时刻
const morningStage = computed(() => {
  const a = auction.value
  if (!a?.cycle?.stage || !cycleDataDay.value || a.date !== cycleDataDay.value) return ''
  return a.cycle.stage
})
const snapTxt = computed(() => {
  const h = parseInt(String(auction.value?.generated_at || '').slice(11, 13)) || 9
  return h >= 12 ? '盘中曾判' : '早盘曾判'
})
const stageShift = computed(() => {
  const m = morningStage.value, live = cycle.value?.stage || ''
  return m && live && m !== live ? `${snapTxt.value}${m}` : ''
})
const gateMatrix = computed(() => battle.value?.strike?.gate?.matrix || null)
// 风险摘要: 主线切换 + 高标开板 top2(盘中"该不该收手"一眼看, 完整明细在依据页)
const mainSwitchNote = computed(() => battle.value?.boardWars?.mainSwitch?.note || '')
const brokenHighsTop = computed(() => (battle.value?.risks?.brokenHighs || []).slice(0, 2))
const riskSummary = computed(() => {
  const names = brokenHighsTop.value.map(b => b.name)
  return (mainSwitchNote.value || names.length) ? { sw: mainSwitchNote.value, names } : null
})
function stWord(c) { return statusWord(c?.status) }
function holdOf(c) { return holdInfo(c, mine.value?.positions || []) }
// 09:25 盘前候选(昨日连板·竞价换手前5 优先, 兜底取出击选股榜); 早/盘中换序
const preRows = computed(() => (auction.value?.bidrank || auction.value?.strike || []).slice(0, 5))

// ── 六情绪实时版(battle 同源输入 + six_history 历史分位), 打开页面/30s 刷新即最新 ──
const sixLive = ref(null)
async function refreshSix() {
  try {
    const hist = await fetchSixHistory()
    const rows = normHistory(hist)
    if (!rows.length) return
    const inp = battle.value?._inputs || {}
    const idxT = await fetchIndexTrend()
    const live = buildLiveRow({
      todayPool: inp.todayPool || [], prevFull: inp.prevFull || [], unsealed: inp.unsealed || [],
      history: rows, mood: { strong: mood.value?.[0]?.strong, df: mood.value?.[0]?.df },
      cycle: cycle.value, bidAmt: parseBidYi(auction.value?.env?.data?.bid_total),
      idxTrend: idxT, date: cycleDataDay.value || '',
    })
    if (live.date) sixLive.value = computeSixLive(rows, live)
  } catch (e) { /* 六情绪失败不影响主结论 */ }
}

// 候选实时价: 东财 ulist 一次批量(≤40只/批), 仅用于"距买点/止损参考"行
const strikePx = ref({})
async function loadStrikePx() {
  const codes = (battle.value?.strike?.candidates || []).map(c => c.code).filter(Boolean)
  if (!codes.length) { strikePx.value = {}; return }
  try {
    const m = {}
    for (const q of await fetchEmPct(codes)) if (q.price != null) m[q.code] = q
    strikePx.value = m
  } catch (e) { /* 行情失败不影响判定 */ }
}
function pxOf(c) { const q = strikePx.value[c?.code]; return q && q.price != null ? q : null }
function pxLineOf(c) { const q = pxOf(c); return q ? pxLine({ mode: c.mode, price: q.price, pct: q.pct ?? 0, code: c.code, avg: q.avg }) : '' }

async function loadAll(silent = false) {
  loadMine(silent)
  try {
    const [l, m, lh, inst, g] = await Promise.all([
      fetchTianTi(silent),
      fetchMarketMood(silent),
      fetchLhbList(silent),
      fetchInstitutionIncrease(getLatestReportDate(), false, silent),
      fetchGlobalIndexes(silent),
    ])
    if (l) ladder.value = l
    if (m) mood.value = m
    if (lh) lhb.value = lh
    if (inst) institution.value = inst
    if (g) global.value = g
    refreshSix()   // mood 就绪后补算一次(与 battle 同源数据, 仍无新增 KPL 请求)
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

// KeepAlive: 轮询必须 onDeactivated 停 / onActivated 恢复(startTimer 有 guard)
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
function fmt(v, d = 2) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—' }
function pct(v) { return (typeof v === 'number' && isFinite(v)) ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }
function fmtYi(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const s = v / 1e8
  return (s >= 0 ? '+' : '') + s.toFixed(2) + '亿'
}

// ── 今日出击 ──
const showObs = ref(false)      // 无达标候选时, 观察卡默认折叠
const expanded = ref({})         // 出击卡详情展开(默认精简: 结论+价+买点/触发/止损)
function toggleExp(code) { expanded.value = { ...expanded.value, [code]: !expanded.value[code] } }
const strikeAll = computed(() => battle.value?.strike?.candidates || [])
const strikeTop2 = computed(() => strikeAll.value.slice(0, 2))
const strikeRest5 = computed(() => strikeAll.value.slice(2, 5))
const strikeBeyond = computed(() => strikeAll.value.slice(5))
const showAllRest = ref(false)
const wzqActionable = computed(() => wzqRows.value.filter(r => r.w.txt !== '只看不买'))
const wzqHold = computed(() => wzqRows.value.filter(r => r.w.txt === '只看不买'))
const wzqTop5 = computed(() => wzqActionable.value.slice(0, 5))
const wzqRest = computed(() => wzqActionable.value.slice(5))
const showWzqHold = ref(false)
const reviewTop5 = computed(() => reviewRows.value.slice(0, 5))
const reviewRest = computed(() => reviewRows.value.slice(5))
// ── 行情速览 ──
const showBrowse = ref(false)
const ladderTop5 = computed(() => (ladder.value || []).slice(0, 5))
const ladderLevel = g => { const n = parseInt(g.title); return !isNaN(n) ? n : 0 }
const ladderStocks = g => (g.rows || []).slice(0, 3).map(r => r.name).join('、')
const lhbTop3 = computed(() => (lhb.value?.list || []).slice(0, 3))
const instTop3 = computed(() => (institution.value || []).slice(0, 3))
const globalTop3 = computed(() => (global.value?.indexes || []).slice(0, 3))
</script>

<template>
  <div class="mt-page">
    <div class="pk-day">决策日 {{ cycleDataDay || auction?.date || '—' }}</div>

    <!-- ① 结论头: 当下可否买入(池 → 上限 → 一句话结论), 点击进 cycle 详情 -->
    <div v-if="battle && !battle.empty" class="pk-verdict" :class="'v-' + verdictShow.cls" :style="{ '--sc': STAGE_COLORS[cycle?.stage] || '#8a97a8' }" @click="open('cycle')">
      <div class="pk-row">
        <span class="pk-badge">{{ verdictShow.verdict }}</span>
        <span class="pk-chip pk-cap">仓位上限 {{ discRule?.cap || '—' }}</span>
        <span class="pk-more">选股依据 ›</span>
      </div>
      <div class="pk-sub" v-if="gateSentenceTxt">{{ gateSentenceTxt }}{{ stageShift ? '（' + stageShift + '）' : '' }}</div>
      <div class="pk-sub muted" v-else>正在计算今日阶段与池…</div>
      <div v-if="riskSummary" class="pk-risk">
        <span v-if="riskSummary.sw">🔄 主线切换 {{ riskSummary.sw }}</span>
        <span v-if="riskSummary.names.length">🚨 高标开板: {{ riskSummary.names.join('、') }}</span>
      </div>
      <div v-if="dvg" class="pk-warn">⚠️ {{ dvg }}</div>
    </div>
    <div v-else class="pk-verdict pk-load" @click="open('cycle')">
      <span class="pk-badge">…</span>
      <span class="pk-sub muted">周期数据加载中,稍候给出「可否买入」结论</span>
    </div>

    <!-- ① 持仓触价风控(纪律卡下线后的轻量回补: 有触价即红字提醒) -->
    <div v-if="touchCount > 0" class="pk-touch">⚠️ {{ touchCount }} 只持仓触价待执行(反抽/破位线命中, 按价执行不撤单)</div>

    <!-- ② 出击 pane(决策流主体; 早盘盘前候选在上, 盘中在下) -->
    <div class="mt-pane">
      <section v-if="isMorning() && preRows.length" class="mt-sec">
        <div class="mt-sec-head"><h3>⚡ 09:25 盘前候选<Hint text="昨日涨停池 2~5 板组 ∩ 今日 09:25 竞价实际换手(竞价额/流通市值)>0, 高换手优先取前五; 开盘后以顶部结论为准。"/></h3><em>昨日连板 · 竞价实际换手前五</em><button class="mt-more" @click="open('auction')">全部 ›</button></div>
        <div v-for="(p, i) in preRows" :key="p.code" class="mt-row" @click="goStock(p)">
          <span class="mt-row-name">{{ p.name }}<span v-if="p.height || p.prev_pid" class="mt-strike-lv" style="margin-left:6px">{{ p.height || p.prev_pid }}板</span></span>
          <span v-if="p.bid_pct != null" class="mt-row-count">竞价 {{ p.bid_pct > 0 ? '+' : '' }}{{ fmt(p.bid_pct, 1) }}%</span>
          <span v-if="p.turnover != null || p.hs != null" class="mt-row-count">换手 {{ fmt(p.turnover != null ? p.turnover : p.hs, 1) }}%</span>
          <span class="mt-row-val" style="width:auto;color:#2980b9">{{ i + 1 }}</span>
        </div>
      </section>

      <section v-if="battle && !battle.empty" class="mt-sec">
        <div class="mt-sec-head">
          <h3>🎯 今日出击<Hint text="可买 = 按预案直接打 · 待确认 = 分时转强再买 · 只看不买 = 不入场。与持仓重叠会标「已持仓」, 只许对了加仓。"/></h3>
          <em>{{ battle.strike.gate.stage }} · 上限 {{ battle.strike.gate.cap || '禁买' }}</em>
          <button class="mt-more" @click="open('cycle')">决策详情 ›</button>
        </div>
        <div class="mt-strike-banner">
          <div>{{ battle.strike.gate.banner?.split('📐')[0] }}</div>
          <div v-if="battle.strike.relay?.txt" class="sb-mtx">🗡 {{ battle.strike.relay.txt }}</div>
          <div v-if="gateMatrix" class="sb-mtx">📐 高位{{ gateMatrix.high }}×中位{{ gateMatrix.mid }}：{{ gateMatrix.note }}</div>
        </div>
        <div v-if="noCandidate" class="pk-empty">
          <b>闸门开 · 今日暂无达标候选</b>
          <p>只做手上持仓、不勉强开仓;明日开盘再看新题材与弱转强名单。</p>
        </div>
        <div v-show="!noCandidate || showObs">
          <div v-for="c in strikeTop2" :key="c.code" class="mt-strike" :class="'st-' + stWord(c).cls" @click="goStock(c)">
            <div class="mt-strike-top">
              <b>{{ c.name }}</b>
              <span v-if="c.level" class="mt-strike-lv">{{ c.level }}板</span>
              <span class="mt-strike-plates">{{ c.platesTxt }}</span>
              <span v-if="holdOf(c)" class="pk-held">{{ holdOf(c).txt }}</span>
              <span class="mt-strike-score" :class="{ hi: c.score >= 75 }">{{ c.score }}</span>
              <span class="mt-strike-status">{{ stWord(c).txt }}</span>
              <button class="mt-mini" @click.stop="toggleExp(c.code)">{{ expanded[c.code] ? '收起' : '详情' }}</button>
            </div>
            <div v-if="pxLineOf(c)" class="mt-now">{{ pxLineOf(c) }}</div>
            <div class="mt-strike-tip">🎯 {{ candTip(c, battle.strike.gate.cap).buy }} ｜ 触发：{{ candTip(c, battle.strike.gate.cap).trigger }} ｜ 止损：{{ candTip(c, battle.strike.gate.cap).stop }} ｜ {{ candTip(c, battle.strike.gate.cap).pos }}</div>
            <template v-if="expanded[c.code]">
              <div class="mt-strike-mid">
                <span v-if="c.bidTop" class="lb-bidtop" :title="`昨日${c.bidTop.prevPid}板 · 竞价实际换手 ${c.bidTop.hs.toFixed(2)}%（昨日连板股第 ${c.bidTop.rank} 名）`">🔥 竞价换手TOP{{ c.bidTop.rank }}</span>
                <span v-if="c.roleTxt" class="lb-role" :class="{ feng: c.roleTxt === '跟风', huo: c.roleTxt === '火种' }">🏷 {{ c.roleTxt }}</span>
                <span class="mt-strike-mode">{{ c.mode }}</span>
                <span v-if="c.sealTxt">{{ c.sealTxt }}</span>
                <span v-if="c.strength">💪 {{ c.strength }}</span>
              </div>
              <div class="mt-strike-logic">{{ c.logic }}</div>
              <div v-if="c.risk" class="mt-strike-risk">⚠️ {{ c.risk }}</div>
              <div v-if="whyNot(c, battle.strike.gate.cap, gateMatrix?.high, gateMatrix?.mid)" class="mt-strike-why">🚫 {{ whyNot(c, battle.strike.gate.cap, gateMatrix?.high, gateMatrix?.mid) }}</div>
            </template>
          </div>
          <div v-for="c in strikeRest5" :key="c.code" class="mt-slim" @click="goStock(c)">
            <span class="st" :class="stWord(c).cls">{{ stWord(c).txt }}</span>
            <b class="nm">{{ c.name }}</b>
            <span v-if="c.level" class="lv">{{ c.level }}板</span>
            <span class="nt">{{ c.platesTxt }}</span>
            <span v-if="holdOf(c)" class="hd">{{ holdOf(c).txt }}</span>
            <i>{{ c.score }}</i>
          </div>
          <template v-if="showAllRest">
            <div v-for="c in strikeBeyond" :key="c.code" class="mt-slim" @click="goStock(c)">
              <span class="st" :class="stWord(c).cls">{{ stWord(c).txt }}</span>
              <b class="nm">{{ c.name }}</b>
              <span v-if="c.level" class="lv">{{ c.level }}板</span>
              <span class="nt">{{ c.platesTxt }}</span>
              <span v-if="holdOf(c)" class="hd">{{ holdOf(c).txt }}</span>
              <i>{{ c.score }}</i>
            </div>
          </template>
        </div>
        <div v-if="noCandidate && strikeAll.length" class="mt-strike-toggle" @click="showObs = !showObs">
          {{ showObs ? '收起观察候选 ▲' : `展开 ${strikeAll.length} 只观察候选 ▾` }}
        </div>
        <div v-else-if="strikeBeyond.length" class="mt-strike-toggle" @click="showAllRest = !showAllRest">
          {{ showAllRest ? '收起 ▲' : `展开其余 ${strikeBeyond.length} 只 ▾` }}
        </div>
        <div v-if="!strikeAll.length" class="mt-hold">本阶段无出击候选（纪律优先）</div>
        <div class="mt-strike-note">{{ battle.strike.disclaimer }}</div>
      </section>

      <section v-if="!isMorning() && preRows.length" class="mt-sec">
        <div class="mt-sec-head"><h3>⚡ 09:25 盘前候选<Hint text="昨日涨停池 2~5 板组 ∩ 今日 09:25 竞价实际换手(竞价额/流通市值)>0, 高换手优先取前五; 开盘后以顶部结论为准。"/></h3><em>早盘依据 · 盘中出手以出击清单为准</em><button class="mt-more" @click="open('auction')">全部 ›</button></div>
        <div v-for="(p, i) in preRows" :key="p.code" class="mt-row" @click="goStock(p)">
          <span class="mt-row-name">{{ p.name }}<span v-if="p.height || p.prev_pid" class="mt-strike-lv" style="margin-left:6px">{{ p.height || p.prev_pid }}板</span></span>
          <span v-if="p.bid_pct != null" class="mt-row-count">竞价 {{ p.bid_pct > 0 ? '+' : '' }}{{ fmt(p.bid_pct, 1) }}%</span>
          <span v-if="p.turnover != null || p.hs != null" class="mt-row-count">换手 {{ fmt(p.turnover != null ? p.turnover : p.hs, 1) }}%</span>
          <span class="mt-row-val" style="width:auto;color:#2980b9">{{ i + 1 }}</span>
        </div>
      </section>

      <!-- 六情绪定性/板块之争/高标开板 已归 cycle 详情页(选股依据), 首页不再重复 -->
      <section v-if="reviewValid && review" class="mt-sec">
        <div class="mt-sec-head">
          <h3>🌅 今日竞价弱转强<Hint text="昨日分歧股 × 今日 9:25 竞价超预期; 站稳分时均价/放量转强再买, 失败 -3% 止损。属今日可买候选。"/></h3>
          <em>{{ review.date }} 9:25 竞价口径</em>
        </div>
        <div v-if="!wzqRows.length" class="mt-hold">今日无弱转强候选（昨日无分歧股, 或竞价未达 +1.5~7%）</div>
        <div v-if="!wzqActionable.length && wzqHold.length" class="mt-hold">今日弱转强全为不出手(禁买期, 仅观察)</div>
        <div v-for="row in wzqTop5" :key="row.code" class="mt-review" @click="goStock(row)">
          <div class="mt-strike-top wzq-top">
            <b class="nm">{{ row.name }}</b>
            <span class="mt-strike-status" :class="'st-' + row.w.cls" style="font-size:10px;padding:1px 6px;">{{ row.w.txt }}</span>
            <span v-if="row.w.txt === '可买'" class="rv-tag" :class="row.resealed ? 'hold' : 'warn'">{{ row.resealed ? '已回封✅' : '待分时确认' }}</span>
            <span v-else class="rv-tag watch">不出手</span>
          </div>
          <div class="wzq-sub">昨日{{ row.tag }}分歧 · 竞价 {{ row.bid_pct }}%<template v-if="row.pct != null"> · 现 {{ row.pct > 0 ? '+' : '' }}{{ row.pct }}%</template></div>
          <div v-if="row.w.txt === '可买'" class="mt-review-today rv-warn">分时确认才上：站稳分时均价/放量转强再买，失败 -3% 止损</div>
          <div v-else class="mt-strike-why">🚫 禁买期弱转强仅观察: 分时转强也不买, 留作情绪回暖观察样本</div>
        </div>
        <template v-if="showMoreWzq">
          <div v-for="row in wzqRest" :key="row.code" class="mt-review" @click="goStock(row)">
            <div class="mt-strike-top wzq-top">
              <b class="nm">{{ row.name }}</b>
              <span class="mt-strike-status" :class="'st-' + row.w.cls" style="font-size:10px;padding:1px 6px;">{{ row.w.txt }}</span>
              <span v-if="row.w.txt === '可买'" class="rv-tag" :class="row.resealed ? 'hold' : 'warn'">{{ row.resealed ? '已回封✅' : '待分时确认' }}</span>
              <span v-else class="rv-tag watch">不出手</span>
            </div>
            <div class="wzq-sub">昨日{{ row.tag }}分歧 · 竞价 {{ row.bid_pct }}%<template v-if="row.pct != null"> · 现 {{ row.pct > 0 ? '+' : '' }}{{ row.pct }}%</template></div>
            <div v-if="row.w.txt === '可买'" class="mt-review-today rv-warn">分时确认才上：站稳分时均价/放量转强再买，失败 -3% 止损</div>
            <div v-else class="mt-strike-why">🚫 禁买期弱转强仅观察: 分时转强也不买, 留作情绪回暖观察样本</div>
          </div>
        </template>
        <div v-if="wzqRest.length" class="mt-strike-toggle" @click="showMoreWzq = !showMoreWzq">
          {{ showMoreWzq ? '收起 ▲' : `展开其余 ${wzqRest.length} 只 ▾` }}
        </div>
        <div v-if="wzqHold.length" class="mt-strike-toggle" @click="showWzqHold = !showWzqHold">
          {{ showWzqHold ? '收起 ▲' : `展开不出手 ${wzqHold.length} 只 ▾` }}
        </div>
        <template v-if="showWzqHold">
          <div v-for="row in wzqHold" :key="'h'+row.code" class="mt-review" @click="goStock(row)">
            <div class="mt-strike-top wzq-top">
              <b class="nm">{{ row.name }}</b>
              <span class="mt-strike-status watch">只看不买</span>
              <span class="rv-tag watch">不出手</span>
            </div>
            <div class="wzq-sub">昨日{{ row.tag }}分歧 · 竞价 {{ row.bid_pct }}%<template v-if="row.pct != null"> · 现 {{ row.pct > 0 ? '+' : '' }}{{ row.pct }}%</template></div>
          </div>
        </template>
      </section>
      <section v-if="reviewValid && review" class="mt-sec">
        <div class="mt-sec-head">
          <h3>📋 昨日可买复核<Hint text="昨日 9:25 选股单逐只对今日行情: 封板=持有 · 涨3%+=兑现 · 平盘弱=减半 · 水下=开盘走; 转退潮/冰点=全清。"/></h3>
          <em>{{ review.prev_day }} 9:25 选股 · {{ review.date }} 执行</em>
          <button class="mt-more" @click="showReview = !showReview">{{ showReview ? '收起 ▲' : (review.picks.length ? `展开 ${review.picks.length} 只 ▾` : '展开 ▾') }}</button>
        </div>
        <template v-if="showReview">
          <div class="mt-review-guide">封板=持有到尾盘 · 涨3%+=冲高兑现 · 平盘弱=先出一半 · 水下=开盘走;今日转退潮/冰点=全清(只卖不买)。</div>
          <div v-if="!review.picks.length" class="mt-hold">{{ review.stage }}期无可买（空仓纪律正确）✓</div>
          <div v-for="row in reviewTop5" :key="row.code" class="mt-review" @click="goStock(row)">
            <div class="mt-strike-top wzq-top">
              <b class="nm">{{ row.name }}</b>
              <span class="mt-strike-lv">{{ row.height }}板</span>
              <span class="rv-tag" :class="row.cls">{{ row.tag }}</span>
            </div>
            <div class="wzq-sub">{{ row.pct == null ? `竞价 ${row.bid_pct ?? '—'}%` : (row.pct > 0 ? '+' : '') + row.pct + '%' }}</div>
            <div class="mt-strike-logic">昨日：{{ row.reason }}</div>
            <div class="mt-review-today" :class="'rv-' + row.cls">今日：{{ row.txt }}</div>
          </div>
          <template v-if="showMoreReview">
            <div v-for="row in reviewRest" :key="row.code" class="mt-review" @click="goStock(row)">
              <div class="mt-strike-top wzq-top">
                <b class="nm">{{ row.name }}</b>
                <span class="mt-strike-lv">{{ row.height }}板</span>
                <span class="rv-tag" :class="row.cls">{{ row.tag }}</span>
              </div>
              <div class="wzq-sub">{{ row.pct == null ? `竞价 ${row.bid_pct ?? '—'}%` : (row.pct > 0 ? '+' : '') + row.pct + '%' }}</div>
              <div class="mt-strike-logic">昨日：{{ row.reason }}</div>
              <div class="mt-review-today" :class="'rv-' + row.cls">今日：{{ row.txt }}</div>
            </div>
          </template>
          <div v-if="reviewRest.length" class="mt-strike-toggle" @click="showMoreReview = !showMoreReview">
            {{ showMoreReview ? '收起 ▲' : `展开其余 ${reviewRest.length} 只 ▾` }}
          </div>
        </template>
        <div v-else-if="!review.picks.length" class="mt-hold">{{ review.stage }}期无可买（空仓纪律正确）✓</div>
      </section>
      <div v-if="!battle || battle.empty" class="mt-hold" style="padding:20px 0;text-align:center;">周期数据加载中…</div>
    </div>

    <!-- 行情速览(收起态, 顶替原 6 Tab 的看盘入口) -->
    <div class="section">
      <div class="mt-sec-head" style="cursor:pointer" @click="showBrowse = !showBrowse">
        <h3>📈 行情速览</h3><em>涨停 · 龙虎 · 机构 · 外围</em>
        <button class="mt-more">{{ showBrowse ? '收起 ▲' : '展开 ▾' }}</button>
      </div>
      <div v-show="showBrowse">
        <section class="mt-sec">
          <div class="mt-sec-head"><h3>🪜 涨停天梯</h3><em>连板梯队</em><button class="mt-more" @click="open('ladder')">全部 ›</button></div>
          <div v-for="g in ladderTop5" :key="g.title" class="mt-group-row" @click="open('ladder')">
            <span class="mt-group-tag" :class="{ hot: ladderLevel(g) >= 3 }">{{ g.title }}</span>
            <span class="mt-group-count">{{ g.rows.length }} 只</span>
            <span class="mt-group-stocks">{{ ladderStocks(g) }}</span>
          </div>
          <div v-if="!ladderTop5.length" class="mt-hold">—</div>
        </section>
        <section class="mt-sec">
          <div class="mt-sec-head"><h3>🐉 龙虎榜</h3><em>净买 TOP</em><button class="mt-more" @click="open('lhb')">全部 ›</button></div>
          <div v-for="(s2, i) in lhbTop3" :key="s2.code" class="mt-row" @click="goStock(s2)">
            <span class="mt-row-name">{{ s2.name }}</span>
            <span class="mt-row-val" :style="{ color: s2.buyIn >= 0 ? '#c0392b' : '#27ae60' }">{{ fmtYi(s2.buyIn) }}</span>
          </div>
          <div v-if="!lhbTop3.length" class="mt-hold">—</div>
        </section>
        <section class="mt-sec">
          <div class="mt-sec-head"><h3>🏦 机构 / 🌍 外围</h3><button class="mt-more" @click="open('institution')">全部 ›</button></div>
          <div v-for="g in instTop3" :key="'i'+g.bkCode" class="mt-row" @click="open('institution')">
            <span class="mt-row-name">{{ g.bkName }}</span><span class="mt-row-val" style="color:#c0392b">{{ fmt(g.addAmt, 1) }} 亿</span>
          </div>
          <div v-for="g in globalTop3" :key="'g'+g.code" class="mt-row" @click="open('global')">
            <span class="mt-row-name">{{ g.name }}</span>
            <span class="mt-row-chg" :style="{ color: g.chgPct >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(g.chgPct) }}</span>
          </div>
          <div v-if="!instTop3.length && !globalTop3.length" class="mt-hold">—</div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mt-page { padding: 4px 14px 12px; overflow-x: clip; }

/* ── 决策日 ── */
.pk-day { font-size: 10px; color: #a6afbd; margin-bottom: 6px; }

/* ── 区块通用 ── */
.section { margin-bottom: 12px; }
.mt-sec { background: #fff; border: 1px solid #eef1f5; border-radius: 12px; padding: 10px 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(17,24,39,.04), 0 4px 14px rgba(17,24,39,.05); }
.mt-sec-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mt-sec-head h3 { font-size: 13px; font-weight: 600; color: #111; margin: 0; }
.mt-sec-head em { font-size: 11px; color: #999; font-style: normal; }
.mt-more { margin-left: auto; font-size: 11px; color: #2980b9; background: none; border: none; padding: 0; cursor: pointer; flex-shrink: 0; }

/* 通用行 */
.mt-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.mt-row:last-child { border-bottom: none; }
.mt-row-name { flex: 1; min-width: 0; font-size: 13px; color: #333; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-row-chg { width: 56px; flex: none; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; }
.mt-row-val { width: 64px; flex: none; text-align: right; font-size: 12px; color: #666; font-variant-numeric: tabular-nums; }
.mt-row-count { font-size: 11px; color: #999; flex: none; }
.mt-hold { font-size: 11px; color: #bbb; padding: 8px 0; }

/* ── 结论头 ── */
.pk-verdict { --sc: #8a97a8; background: #fff; background-image: linear-gradient(135deg, color-mix(in srgb, var(--sc) 11%, #fff) 0%, #fff 60%); border: 1px solid color-mix(in srgb, var(--sc) 26%, #e9edf3); border-radius: 14px; padding: 11px 13px; margin-bottom: 8px; cursor: pointer; box-shadow: 0 1px 3px rgba(17,24,39,.05); }
.pk-verdict .pk-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.pk-badge { font-size: 15px; font-weight: 900; color: #fff; border-radius: 9px; padding: 3px 12px; letter-spacing: 1px; flex: none; }
.v-go .pk-badge { background: #c0392b; }
.v-ban .pk-badge { background: #9b6bde; }
.v-warn .pk-badge { background: #f5a623; }
.v-plain .pk-badge, .pk-load .pk-badge { background: #8a97a8; }
.pk-chip { font-size: 10.5px; padding: 3px 9px; border-radius: 20px; background: #fff; border: 1px solid #e9edf3; color: #5a6472; white-space: nowrap; }
.pk-chip.pk-cap { background: #f0f3fa; border-color: #d8dff0; color: #5b6daa; font-weight: 700; }
.pk-more { margin-left: auto; font-size: 11px; color: #b7c0cc; flex: none; }
.pk-sub { margin-top: 7px; font-size: 11.5px; color: #43505e; line-height: 1.55; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pk-sub.muted { color: #a6afbd; }
.pk-touch { font-size: 12px; font-weight: 700; color: #fff; background: #e67e22; border-radius: 10px; padding: 7px 13px; margin-bottom: 8px; }
.pk-risk { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 7px; font-size: 11px; color: #b9770e; background: #fff7e8; border-radius: 8px; padding: 5px 9px; line-height: 1.6; }
.pk-warn { margin-top: 7px; font-size: 11px; color: #b06020; background: #fff7e8; border-radius: 8px; padding: 6px 9px; line-height: 1.6; }
.pk-empty { border: 1px dashed #cbd5e0; border-radius: 12px; background: #f7f9fc; padding: 14px 12px; text-align: center; margin-bottom: 6px; }
.pk-empty b { font-size: 13.5px; color: #5b6daa; }
.pk-empty p { font-size: 11px; color: #8a97a8; margin-top: 4px; }

/* ── 已持仓标 ── */
.pk-held { font-size: 9.5px; font-weight: 800; color: #fff; background: #f5a623; border-radius: 5px; padding: 1px 6px; flex: none; white-space: nowrap; }

/* ── 今日出击 ── */
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
.mt-strike-risk { font-size: 11px; color: #ff5a5a; margin-top: 2px; }
.mt-strike-why { font-size: 10.5px; color: #8a6d3b; background: #faf6ec; border-radius: 6px; padding: 4px 8px; margin-top: 4px; }
.mt-now { font-size: 11px; color: #2c3e50; background: #eef6fb; border-radius: 6px; padding: 4px 8px; margin: 5px 0 0; line-height: 1.5; }
.mt-slim { display: flex; align-items: center; gap: 7px; background: #fff; border: 1px solid #e9edf3; border-radius: 9px; padding: 6px 10px; margin-bottom: 4px; font-size: 12px; cursor: pointer; }
.mt-slim .st { font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 5px; background: #eef1f5; color: #667; flex: none; }
.mt-slim .st.go { background: #c0392b; color: #fff; }
.mt-slim .st.alt { background: #f5a623; color: #fff; }
.mt-slim .nm { font-weight: 600; color: #333; flex: none; }
.mt-slim .lv { font-size: 10px; color: #b8860b; font-weight: 800; flex: none; }
.mt-slim .nt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #8a97a8; font-size: 11px; }
.mt-slim .hd { font-size: 9.5px; font-weight: 800; color: #fff; background: #f5a623; border-radius: 5px; padding: 1px 6px; flex: none; }
.mt-slim i { font-style: normal; color: #8a97a8; font-size: 11px; }
.mt-strike-toggle { text-align: center; font-size: 12px; color: #667; padding: 8px 0 2px; cursor: pointer; user-select: none; }
.mt-strike-toggle:active { opacity: .6; }
.mt-strike-note { font-size: 10px; color: #a0aab8; margin-top: 4px; }
.mt-mini { margin-left: auto; font-size: 10px; color: #2980b9; background: none; border: none; padding: 0; cursor: pointer; flex: none; }

/* ── 弱转强/复核(移动端两行布局: 首行 名称+状态+结果徽标, 次行数据整行不挤压) ── */
.wzq-top { flex-wrap: wrap; row-gap: 4px; }
.wzq-top .nm { flex: none; max-width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wzq-top .rv-tag { margin-left: auto; }
.wzq-sub { font-size: 11px; color: #78839a; line-height: 1.6; margin: 3px 0 0; word-break: break-all; }
.mt-strike-top { flex-wrap: wrap; row-gap: 4px; }
.mt-strike-top .mt-strike-score { margin-left: auto; }

/* ── 弱转强/复核 ── */
.mt-review { background: #fff; border: 1px solid #eceff3; border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; cursor: pointer; }
.mt-review-guide { font-size: 11px; color: #667; background: #f7f8fa; border-radius: 8px; padding: 6px 10px; margin-bottom: 8px; line-height: 1.6; }
.rv-tag { font-size: 10px; font-weight: 700; padding: 1px 8px; border-radius: 9px; margin-left: auto; flex: none; }
.rv-tag.hold { background: #fdecea; color: #c0392b; }
.rv-tag.warn { background: #fdf3e7; color: #b06020; }
.rv-tag.sell { background: #e8f6ee; color: #1e7e46; }
.rv-tag.watch { background: #eef1f5; color: #8a97a8; }
.mt-review-today { font-size: 12px; margin-top: 2px; font-weight: 600; }
.mt-review-today.rv-hold { color: #c0392b; }
.mt-review-today.rv-warn { color: #b06020; }
.mt-review-today.rv-sell { color: #1e7e46; }

/* ── 天梯 ── */
.mt-group-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.mt-group-row:last-child { border-bottom: none; }
.mt-group-tag { flex: none; border-radius: 16px; padding: 3px 10px; background: #f0f2f5; color: #666; font-size: 12px; font-weight: 600; }
.mt-group-tag.hot { background: #fdecea; color: #c0392b; }
.mt-group-count { flex: none; font-size: 11px; color: #999; }
.mt-group-stocks { flex: 1; min-width: 0; font-size: 11px; color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 480px) {
  .mt-page { padding-left: 10px; padding-right: 10px; }
}
@media (min-width: 768px) {
  .mt-page { padding: 8px 28px 20px; }
  .mt-sec { padding: 14px; }
}
</style>
