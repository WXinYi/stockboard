<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction, fetchLianbanBid, fetchToday } from '../data/loader.js'
import { jsonp, secid } from '../utils/eastmoney.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchTianTi, fetchGlobalIndexes, fetchInstitutionIncrease,
  fetchMarketMood, fetchLhbList,
  fetchLimitPool, fetchRiseFall, fetchUnsealedPool,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'
import { loadCycleData, STAGE_COLORS, STAGE_RULES } from '../utils/emotionCycle.js'
import { loadBattleData, candTipOf as candTip, gateSentence, whyNot } from '../utils/leaderBattle.js'
import Hint from './Hint.vue'
import { gateTier, statusWord, isMorning, resolveVerdict, pxLine } from '../utils/stockPicks.js'

defineOptions({ name: 'MarketTab' })

const router = useRouter()

// 竞价: 当日快照, 单次加载不轮询
const auction = ref(null)
const auctionLoading = ref(true)
// 竞价快照拉取失败标记(与"盘外无快照"区分): 见 loadAuction
const auctionError = ref(false)
// 速览/六情绪输入: 30s 轮询(silent)
const ladder = ref(null)
const global = ref(null)
const institution = ref(null)
const mood = ref(null)
const lhb = ref(null)
const discRule = computed(() => STAGE_RULES[cycle.value?.stage] || null)
// 采集日 vs 决策日: 不一致=今天还没有新交易数据(周末/节假日/push触发班次), 明示不猜测原因
const crawlDate = ref('')
const staleDayNote = computed(() => (crawlDate.value && cycleDataDay.value && crawlDate.value !== cycleDataDay.value)
  ? `最新采集 ${crawlDate.value}，决策数据为 ${cycleDataDay.value}（今日暂无新交易数据）` : '')
// ④降级库批次(拍板③): 本班热层失败走了降级链, 基线可能偏旧, 红条明示
const dbRestore = ref(null)
const dbRestoreNote = computed(() => (dbRestore.value?.degraded
  ? `⚠️ 本班为降级库（来源 ${dbRestore.value.source}，库龄 ${dbRestore.value.stale_days ?? '?'} 天），基线可能偏旧，变化类数字以钉钉实盘推送为准` : ''))
// 失败必须可见(2026-09-15 静默审计): 这两条诚实提示的输入(core.json)拿不到时,
// 提示会一起消失 —— 恰好在管线坏掉的时候失去披露, 所以单独给一条错误条。
const coreError = ref(false)
// 周期引擎拉取失败: 此前与"正在计算"共用一句文案, 失败会被读成"永远在加载"
const cycleError = ref(false)

async function loadCore(silent = false) {
  // 采集日( today.json date ): 用于"最新采集晚于决策日"的诚实提示, 不猜测节假日
  try {
    const core = await fetchToday()
    crawlDate.value = core?.date || ''
    dbRestore.value = null
    coreError.value = false
  } catch (e) {
    coreError.value = true
    if (!silent) console.error('[MarketTab core]', e?.message)
  }
}

async function retryCycle() {
  cycleError.value = false
  await loadCycleBattle(false)
}

async function loadCycleBattle(silent = false) {
  try {
    const day = await getLatestTradingDay()
    const dayDash = day ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : ''
    const cd = await loadCycleData({ fetchTianTi, fetchLimitPool, fetchRiseFall, fetchMarketMood }, dayDash)
    cycle.value = cd.cycle
    riseFallToday.value = cd.riseFallToday || null
    cycleDataDay.value = cd.cycle?.date || dayDash
    // 复盘复核/弱转强名单随 P3 原子切换下线（数据源已无生产者），此处只保留连板竞价数据
    const lb = await fetchLianbanBid().catch(() => null)
    battle.value = await loadBattleData({ fetchLimitPool, fetchUnsealedPool }, cd, lb, [])
    loadStrikePx() // 候选实时价(一次批量行情, 供"距买点/止损参考")
    cycleError.value = false
  } catch (e) {
    cycleError.value = true
    if (!silent) console.error('[MarketTab cycle]', e?.message)
  }
}

// ── 候选实时价批量行情(东财 ulist; 供出击卡"距买点/止损参考") ──
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

// ── 情绪周期(选股依据): cycle 供结论头, battle 供出击 ──
const cycle = ref(null)
const battle = ref(null)
const cycleDataDay = ref('')
const riseFallToday = ref(null)

// 跌停家数防守线(2026-09-23): 258 日回放=极值日次日 80% 概率涨停≥50, 故只做"当日防守提示"
// 不进 CYCLE_CFG 次日阶段判定(数据反对, 见 docs/DATA_PIPELINE.md 负结果记录)
const limitDownAlert = computed(() => {
  const dt = riseFallToday.value?.dt
  return dt != null && dt >= 30 ? dt : null
})
// 昨日涨停股实时表现(赚钱效应读数, leaderBattle.computePremium)
const premiumLine = computed(() => {
  const p = battle.value?.premium
  if (!p || !p.total) return ''
  return `昨日涨停${p.total}：今仍涨停${p.still} · 开板${p.open}（红${p.red} 绿${p.green}` +
         `${p.noq ? ` 无行情${p.noq}` : ''}）· 开板股实时溢价均值 ${p.avgPct ?? '-'}%` +
         `（首板 ${p.avgFirst ?? '-'}% / 连板 ${p.avgLian ?? '-'}%）`
})

// ── 选股结论头(三层: 池判定 → 仓位上限 → 一句话结论; 口径见 utils/stockPicks.js / leaderBattle.gateSentence) ──
const verdictTier = computed(() => gateTier(battle.value?.strike?.gate?.cap))
// 闸门开≠有可买: 结论必须与实际达标候选数一致(无达标→「仅观察」, 不误导)
const actCount = computed(() => (battle.value?.strike?.candidates || []).filter(c => statusWord(c.status).txt !== '只看不买').length)
const noCandidate = computed(() => verdictTier.value.verdict !== '禁买' && actCount.value === 0)
const verdictShow = computed(() => resolveVerdict(verdictTier.value, actCount.value))
// 首页只给人话结论: 阶段+矩阵禁买范围+池+是否达标(中间层数值在依据页)
const gateSentenceTxt = computed(() => gateSentence(
  cycle.value?.stage || '', gateMatrix.value?.high, gateMatrix.value?.mid,
  battle.value?.strike?.gate?.cap, noCandidate.value))
// 存档阶段 vs 实时阶段: 仅同一天才标注(跨日不冒充"今日早盘"); 措辞按快照时刻
const morningStage = computed(() => {
  const a = auction.value
  if (!a?.cycle?.stage || !cycleDataDay.value || a.date !== cycleDataDay.value) return ''
  return a.cycle.stage
})
const snapTxt = computed(() => {
  // generated_at="HH:MM"(北京时间, auction_scan crawl_time): 旧代码 slice(11,13) 切空串恒判"早盘"
  const s = String(auction.value?.generated_at || '')
  const h = parseInt(s.includes('T') ? s.slice(11, 13) : s.slice(0, 2))
  return (h || 9) >= 12 ? '盘中曾判' : '早盘曾判'
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
// 09:25 盘前候选(昨日连板·竞价换手前5 优先, 兜底取出击选股榜); 早/盘中换序
const preRows = computed(() => (auction.value?.bidrank || auction.value?.strike || []).slice(0, 5))

// 大模型独立选股(影子): auction.json.llm, 与引擎结论互不影响; 允许空仓输出
const llm = computed(() => auction.value?.llm || null)
// llm 卡时间: llm_review.created_at 是 CI runner 的 UTC ISO, 直显 slice(11,16) 会把北京 10:55 显示成 02:55
const llmTime = computed(() => {
  const s = String(llm.value?.generated_at || '')
  if (!s) return ''
  if (!/T\d{2}:/.test(s)) return s   // 已是短格式(HH:MM)原样
  const d = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`)   // 裸 ISO 按 UTC(CI runner 时区)
  return isNaN(d) ? s.slice(11, 16)
    : d.toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false })
})
const llmRegimeCls = computed(() => ({ '攻击': 'go', '试错': 'warn', '观察': 'watch', '空仓': 'ban' }[llm.value?.regime] || 'plain'))
const showLlm = ref(true)
// 可买性标注(纯展示层, 不改写 LLM 输出, 回测口径不受影响): 引擎闸门禁买或模型自判观察/空仓时,
// 候选照常展示但打「不可买」标签 —— 执行权在引擎纪律, LLM 输出只作对照
const llmSelfBuy = computed(() => ['攻击', '试错'].includes(llm.value?.regime || ''))
const llmBlockTxt = computed(() => {
  if (!llm.value || llm.value.degraded) return ''
  if (verdictTier.value?.verdict === '禁买') return '禁买期'
  if (!llmSelfBuy.value) return `模型自判${llm.value.regime || '观察'}`
  return ''
})
// 密度控制: 默认紧凑 — 依据/回避截断两行、候选理由一行, 点"详情/展开"才放全文
const whyFull = ref(false)
const avoidFull = ref(false)
const llmExp = ref({})
function toggleLlmPick(code) { llmExp.value = { ...llmExp.value, [code]: !llmExp.value[code] } }

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
  loadCore(silent)
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
  } catch (e) { if (!silent) console.error('[MarketTab]', e?.message) }
}

async function loadAuction(silent = false) {
  try {
    auction.value = await fetchAuction()
    auctionError.value = false
  } catch (e) {
    // 盘外无快照是常态, 但"取不到"与"今天没有"不能混为一谈(2026-09-15 审计 E5):
    // 记下失败, 让大模型卡/盘前候选区如实说"加载失败"而不是"非交易日无此数据"。
    auctionError.value = true
    if (!silent) console.error('[MarketTab auction]', e?.message)
  }
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
// ── 行情速览 ──
const showBrowse = ref(false)
const ladderTop5 = computed(() => (ladder.value || []).slice(0, 5))
const ladderLevel = g => { const n = parseInt(g.title); return !isNaN(n) ? n : 0 }
const ladderStocks = g => {
  const rows = g.rows || []
  const names = rows.slice(0, 3).map(r => r.name).join('、')
  return rows.length > 3 ? `${names} 等${rows.length}只` : names
}
const lhbTop3 = computed(() => (lhb.value?.list || []).slice(0, 3))
const instTop3 = computed(() => (institution.value || []).slice(0, 3))
const globalTop3 = computed(() => (global.value?.indexes || []).slice(0, 3))
</script>

<template>
  <div class="mt-page">
    <div class="summary-bar">N6 已接受退化：周期 / 六情绪历史 / 龙头战法历史切片生产者已下线，相关区块仅展示当日可计算部分，历史回看暂不可用。</div>
    <div class="pk-day">决策日 {{ cycleDataDay || auction?.date || '—' }}</div>
    <div v-if="staleDayNote" class="pk-stale">ℹ️ {{ staleDayNote }}</div>
    <div v-if="dbRestoreNote" class="pk-stale" style="color:var(--up);border-color:#e6b8b0;background: rgba(255,77,90,.06);">⚠️ {{ dbRestoreNote }}</div>
    <div v-if="coreError" class="pk-stale" style="color:var(--up);border-color:#e6b8b0;background: rgba(255,77,90,.06);">⚠️ 基础数据(core.json)加载失败：采集日与降级库提示本次不可用 —— 这不代表数据正常，请刷新重试</div>

    <!-- ① 结论头: 当下可否买入(池 → 上限 → 一句话结论), 点击进 cycle 详情 -->
    <div v-if="battle && !battle.empty" class="pk-verdict" :class="'v-' + verdictShow.cls" :style="{ '--sc': STAGE_COLORS[cycle?.stage] || 'var(--ink-3)' }" @click="open('cycle')">
      <div class="pk-row">
        <span class="pk-badge">{{ verdictShow.verdict }}</span>
        <span class="pk-chip pk-cap">仓位上限 {{ discRule?.cap || '—' }}</span>
        <span class="pk-more">选股依据 ›</span>
      </div>
      <div class="pk-sub" v-if="gateSentenceTxt">{{ gateSentenceTxt }}{{ stageShift ? '（' + stageShift + '）' : '' }}</div>
      <div class="pk-sub muted" v-else-if="!cycleError">正在计算今日阶段与池…</div>
      <div v-if="riskSummary" class="pk-risk">
        <span v-if="riskSummary.sw">🔄 {{ riskSummary.sw }}</span>
        <span v-if="riskSummary.names.length">🚨 高标开板: {{ riskSummary.names.join('、') }}</span>
      </div>
      <!-- 输入全空时引擎仍会给出阶段, 必须说明该结论不可作为依据(2026-09-15 审计) -->
      <div v-if="cycle?.inputsEmpty" class="pk-warn">⚠️ 行情输入为空（接口可能失败，或今日非交易日）—— 本阶段结论不可作为依据</div>
    </div>
    <div v-else-if="cycleError" class="pk-verdict pk-load" @click="retryCycle">
      <span class="pk-badge">!</span>
      <span class="pk-sub muted">⚠️ 周期数据加载失败（非「正在加载」）— 点此重试；期间「可否买入」结论不可用</span>
    </div>
    <div v-else class="pk-verdict pk-load" @click="open('cycle')">
      <span class="pk-badge">…</span>
      <span class="pk-sub muted">周期数据加载中,稍候给出「可否买入」结论</span>
    </div>

    <!-- ② 出击 pane(决策流主体; 早盘盘前候选在上, 盘中在下) -->
    <div class="mt-pane">
      <section v-if="isMorning() && preRows.length" class="mt-sec">
        <div class="mt-sec-head"><h3>⚡ 09:25 盘前候选<Hint text="昨日涨停池 2~5 板组 ∩ 今日 09:25 竞价实际换手(竞价额/流通市值)>0, 高换手优先取前五; 开盘后以顶部结论为准。"/></h3><em>昨日连板 · 竞价实际换手前五</em><button class="mt-more" @click="open('auction')">全部 ›</button></div>
        <div v-for="(p, i) in preRows" :key="p.code" class="mt-row" @click="goStock(p)">
          <span class="mt-row-name">{{ p.name }}<span v-if="p.height || p.prev_pid" class="mt-strike-lv" style="margin-left:6px">{{ p.height || p.prev_pid }}板</span></span>
          <span v-if="p.bid_pct != null" class="mt-row-count">竞价 {{ p.bid_pct > 0 ? '+' : '' }}{{ fmt(p.bid_pct, 1) }}%</span>
          <span v-if="p.turnover != null || p.hs != null" class="mt-row-count">换手 {{ fmt(p.turnover != null ? p.turnover : p.hs, 1) }}%</span>
          <span class="mt-row-val" style="width:auto;color:var(--accent)">{{ i + 1 }}</span>
        </div>
      </section>

      <section v-if="battle && !battle.empty" class="mt-sec">
        <div class="mt-sec-head">
          <h3>🎯 今日出击<Hint text="可买 = 按预案直接打 · 待确认 = 分时转强再买 · 只看不买 = 不入场。"/></h3>
          <em>{{ battle.strike.gate.stage }} · 上限 {{ battle.strike.gate.cap || '禁买' }}</em>
          <button class="mt-more" @click="open('cycle')">决策详情 ›</button>
        </div>
        <div class="mt-strike-banner">
          <!-- 剧本维度标注(2026-09-13): banner=怎么买(角色/买法), 与结论副行的梯队资格(板高)是两个维度 -->
          <div>剧本(怎么买) · {{ battle.strike.gate.banner?.split('📐')[0] }}</div>
          <div v-if="battle.strike.relay?.txt" class="sb-mtx">🗡 {{ battle.strike.relay.txt }}</div>
          <div v-if="gateMatrix" class="sb-mtx">📐 高位{{ gateMatrix.high }}×中位{{ gateMatrix.mid }}：{{ gateMatrix.note }}</div>
          <!-- 赚钱效应读数 + 跌停防守线(2026-09-23): 只读展示, 不参与阶段判定(数据反对, 见文档) -->
          <div v-if="premiumLine" class="sb-mtx">💰 {{ premiumLine }}</div>
          <div v-if="limitDownAlert" class="sb-mtx mt-defense">⚠️ 跌停 {{ limitDownAlert }} 家(极值≥30)：当日防守信号——禁止加仓, 持仓减半；次日 80% 概率涨停≥50, 不作次日看空依据</div>
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
            <i>{{ c.score }}</i>
          </div>
          <template v-if="showAllRest">
            <div v-for="c in strikeBeyond" :key="c.code" class="mt-slim" @click="goStock(c)">
              <span class="st" :class="stWord(c).cls">{{ stWord(c).txt }}</span>
              <b class="nm">{{ c.name }}</b>
              <span v-if="c.level" class="lv">{{ c.level }}板</span>
              <span class="nt">{{ c.platesTxt }}</span>
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
          <span class="mt-row-val" style="width:auto;color:var(--accent)">{{ i + 1 }}</span>
        </div>
      </section>

      <!-- 大模型独立选股(影子): 每日 09:25 DeepSeek 只看原始竞价数据独立分析, 与引擎结论互不影响.
           卡片常驻: 无输出日也保留入口(占位说明), 候选不可买时照常展示只打标签, 执行权在引擎纪律 -->
      <section class="mt-sec">
        <div class="mt-sec-head">
          <h3>🤖 大模型选股<Hint text="DeepSeek 独立分析: 只喂 09:25 原始竞价数据(昨日涨停池/炸板/宽度/竞价额), 不参考引擎结论; 允许空仓输出。影子运行仅供对照, 不构成引擎结论; 候选上的「不可买」标签来自引擎闸门/模型自判, 展示层标注, 不影响回测口径。"/></h3>
          <em v-if="llm">{{ llm.model }} · {{ llm.prompt_ver }} · {{ llmTime }}</em>
          <button v-if="llm" class="mt-more" @click="showLlm = !showLlm">{{ showLlm ? '收起 ▲' : '展开 ▾' }}</button>
        </div>
        <template v-if="llm && showLlm && !llm.degraded">
          <div class="mt-llm-head">
            <span :class="['llm-regime', llmRegimeCls]">{{ llm.regime || '—' }}</span>
            <span class="llm-pos">{{ llm.position_today }}</span>
            <span v-if="llmBlockTxt && (llm.picks || []).length" class="rv-tag watch">候选均不可买 · {{ llmBlockTxt }}</span>
          </div>
          <div class="mt-llm-why" :class="{ clamped: !whyFull }" @click="whyFull = !whyFull">{{ llm.why }}</div>
          <div v-if="!whyFull" class="clamp-line" @click="whyFull = true">展开依据 ▾</div>
          <div v-if="!(llm.picks || []).length" class="mt-hold">今日 {{ llm.regime }} —— 空仓等待 ✓（不满足出手条件，空仓即正确）</div>
          <div v-for="k in llm.picks" :key="k.code" class="mt-review" @click="goStock(k)">
            <div class="mt-strike-top">
              <b class="nm">{{ k.name }}</b>
              <span class="llm-action">{{ k.action }}</span>
              <span class="llm-pos-tag">{{ k.pos }}</span>
              <span v-if="llmBlockTxt" class="rv-tag watch">不可买</span>
              <button class="mt-mini" @click.stop="toggleLlmPick(k.code)">{{ llmExp[k.code] ? '收起' : '详情' }}</button>
            </div>
            <div class="wzq-sub" :class="{ clamped: !llmExp[k.code] }">
              {{ k.reason }}<template v-if="llmExp[k.code]"><template v-if="k.entry"> · 买法: {{ k.entry }}</template><template v-if="k.stop"> · 止损: {{ k.stop }}</template></template>
            </div>
          </div>
          <div class="mt-llm-avoid" :class="{ clamped: !avoidFull }" @click="avoidFull = !avoidFull">🚫 回避: {{ llm.avoid }}</div>
          <div v-if="!avoidFull" class="clamp-line" @click="avoidFull = true">展开回避 ▾</div>
        </template>
        <div v-else-if="llm && showLlm && llm.degraded" class="mt-hold">今日无有效输出（模型输出异常，已降级存档待回测）</div>
        <!-- 竞价数据未到时不下"暂无"结论, 防加载闪现误导; llm 存在但收起时三分支全不命中=只留卡头 -->
        <div v-else-if="auctionError" class="mt-hold">⚠️ 竞价快照加载失败 — 与"非交易日无此项"不同，请刷新重试</div>
        <div v-else-if="!auctionLoading && !llm" class="mt-hold">
          暂无模型输出 — 每个交易日 09:25 竞价班自动生成，非交易日/上线前的历史日期无此项
        </div>
      </section>
            <!-- 失败与"加载中"分开说: 引擎拉取失败时不能显示成永远在加载(2026-09-15 静默审计 G4) -->
      <div v-if="cycleError" class="mt-hold" style="padding:20px 0;text-align:center;">
        ⚠️ 周期数据加载失败（非加载中）— <a href="javascript:void(0)" @click="retryCycle">点此重试</a>
      </div>
      <div v-else-if="!battle || battle.empty" class="mt-hold" style="padding:20px 0;text-align:center;">周期数据加载中…</div>
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
            <span class="mt-row-val" :style="{ color: s2.buyIn >= 0 ? 'var(--up)' : 'var(--down)' }">{{ fmtYi(s2.buyIn) }}</span>
          </div>
          <div v-if="!lhbTop3.length" class="mt-hold">—</div>
        </section>
        <section class="mt-sec">
          <div class="mt-sec-head"><h3>🏦 机构 / 🌍 外围</h3><button class="mt-more" @click="open('institution')">全部 ›</button></div>
          <div v-for="g in instTop3" :key="'i'+g.bkCode" class="mt-row" @click="open('institution')">
            <span class="mt-row-name">{{ g.bkName }}</span><span class="mt-row-val" style="color:var(--up)">{{ fmt(g.addAmt, 1) }} 亿</span>
          </div>
          <div v-for="g in globalTop3" :key="'g'+g.code" class="mt-row" @click="open('global')">
            <span class="mt-row-name">{{ g.name }}</span>
            <span class="mt-row-chg" :style="{ color: g.chgPct >= 0 ? 'var(--up)' : 'var(--down)' }">{{ pct(g.chgPct) }}</span>
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
.pk-day { font-size: 10px; color: var(--ink-3); margin-bottom: 6px; }

/* ── 区块通用 ── */
.section { margin-bottom: 12px; }
.mt-sec { background: var(--bg-2); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(17,24,39,.04), 0 4px 14px rgba(17,24,39,.05); }
.mt-sec-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.mt-sec-head h3 { font-size: 13px; font-weight: 600; color: var(--ink); margin: 0; }
.mt-sec-head em { font-size: 11px; color: var(--ink-3); font-style: normal; }
.mt-more { margin-left: auto; font-size: 11px; color: var(--accent); background: none; border: none; padding: 0; cursor: pointer; flex-shrink: 0; }

/* 通用行 */
.mt-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--bg-3); cursor: pointer; }
.mt-row:last-child { border-bottom: none; }
.mt-row-name { flex: 1; min-width: 0; font-size: 13px; color: var(--ink); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.mt-row-chg { width: 56px; flex: none; text-align: right; font-size: 12px; font-variant-numeric: tabular-nums; }
.mt-row-val { width: 64px; flex: none; text-align: right; font-size: 12px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.mt-row-count { font-size: 11px; color: var(--ink-3); flex: none; }
.mt-hold { font-size: 11px; color: var(--ink-3); padding: 8px 0; }

/* ── 结论头 ── */
.pk-verdict { --sc: var(--ink-3); background: var(--bg-2); background-image: linear-gradient(135deg, color-mix(in srgb, var(--sc) 11%, var(--bg-2)) 0%, var(--bg-2) 60%); border: 1px solid color-mix(in srgb, var(--sc) 26%, var(--line)); border-radius: 14px; padding: 11px 13px; margin-bottom: 8px; cursor: pointer; box-shadow: 0 1px 3px rgba(17,24,39,.05); }
.pk-verdict .pk-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.pk-badge { font-size: 15px; font-weight: 900; color: var(--ink); border-radius: 9px; padding: 3px 12px; letter-spacing: 1px; flex: none; }
.v-go .pk-badge { background: var(--accent); color: #16181d; }
.v-ban .pk-badge { background: var(--bg-4); color: var(--ink-2); border: 1px solid var(--line-3); }
.v-warn .pk-badge { background: var(--gold); color: #16181d; }
.v-plain .pk-badge, .pk-load .pk-badge { background: var(--bg-4); color: var(--ink-3); border: 1px solid var(--line-2); }
.pk-chip { font-size: 10.5px; padding: 3px 9px; border-radius: 20px; background: var(--bg-2); border: 1px solid var(--line); color: var(--ink-3); white-space: nowrap; }
.pk-chip.pk-cap { background: var(--bg-3); border-color: var(--line-2); color: var(--accent); font-weight: 700; }
.pk-more { margin-left: auto; font-size: 11px; color: var(--ink-3); flex: none; }
.pk-sub { margin-top: 7px; font-size: 11.5px; color: var(--ink-2); line-height: 1.55; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pk-sub.muted { color: var(--ink-3); }
.pk-touch { font-size: 12px; font-weight: 700; color: var(--ink); background: var(--accent); border-radius: 10px; padding: 7px 13px; margin-bottom: 8px; cursor: pointer; }
.pk-stale { font-size: 11px; color: var(--gold); background: var(--bg-3); border: 1px solid rgba(224,177,92,.30); border-radius: 8px; padding: 5px 10px; margin-bottom: 8px; }
.pk-touch-panel { background: var(--bg-3); border: 1px solid #f2ddce; border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; }
.pk-touch-row { padding: 5px 0; border-bottom: 1px solid #f5e8dd; cursor: pointer; line-height: 1.5; }
.pk-touch-row:last-of-type { border-bottom: none; }
.pk-touch-row b { font-size: 12.5px; color: var(--ink); margin-right: 6px; }
.pk-touch-row.exited { opacity: .62; }
.tk-line { display: inline; font-size: 11px; color: var(--gold); }
.tk-note { display: block; font-size: 10.5px; color: var(--ink-3); }
.pk-touch-toggle { font-size: 10.5px; color: var(--accent); padding: 4px 0 0; cursor: pointer; }
.pk-risk { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 7px; font-size: 11px; color: var(--gold); background: rgba(224,177,92,.08); border-radius: 8px; padding: 5px 9px; line-height: 1.6; }
.pk-warn { margin-top: 7px; font-size: 11px; color: var(--gold); background: rgba(224,177,92,.08); border-radius: 8px; padding: 6px 9px; line-height: 1.6; }
.pk-empty { border: 1px dashed #cbd5e0; border-radius: 12px; background: var(--bg-3); padding: 14px 12px; text-align: center; margin-bottom: 6px; }
.pk-empty b { font-size: 13.5px; color: var(--accent); }
.pk-empty p { font-size: 11px; color: var(--ink-3); margin-top: 4px; }

/* ── 今日出击 ── */
.mt-strike-banner { font-size: 12px; color: var(--ink-3); background: var(--bg-3); border: 1px solid #e8edf3; border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; line-height: 1.6; }
.mt-defense { color: var(--up); font-weight: 520; }
.sb-mtx { margin-top: 5px; padding-top: 6px; border-top: 1px dashed var(--line); color: #2b6cb0; }
.mt-strike { border: 1px solid var(--line); border-left: 3px solid var(--line-2); border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer; background: var(--bg-3); }
.mt-strike.st-go { border-left-color: var(--up); background: rgba(255,77,90,.08); }
.mt-strike.st-alt { border-left-color: var(--gold); background: rgba(224,177,92,.06); }
.mt-strike-top { display: flex; align-items: center; gap: 6px; }
.mt-strike-top b { font-size: 14px; }
.mt-strike-lv { font-size: 11px; color: var(--gold); font-weight: 700; }
.mt-strike-plates { font-size: 11px; color: var(--ink-4); }
.mt-strike-score { margin-left: auto; font-size: 17px; font-weight: 800; color: var(--ink-2); }
.mt-strike-score.hi { color: var(--up); }
.mt-strike-status { font-size: 11px; padding: 1px 6px; border-radius: 4px; background: var(--line); color: var(--ink-3); }
.st-go .mt-strike-status { background: var(--up); color: var(--ink); }
.st-alt .mt-strike-status { background: var(--gold); color: var(--ink); }
.mt-strike-mid { display: flex; gap: 8px; font-size: 11px; color: var(--ink-4); margin: 3px 0; flex-wrap: wrap; align-items: center; }
.lb-role { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 9px; background: rgba(224,177,92,.08); color: var(--gold); flex: none; }
.lb-role.feng { background: var(--bg-3); color: var(--ink-3); }
.lb-role.huo { background: rgba(255,77,90,.08); color: var(--up); }
.lb-bidtop { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 9px; background: var(--bg-3); color: #d4380d; flex: none; cursor: help; }
.mt-strike-mode { color: #2bc4a8; font-weight: 700; }
.mt-strike-logic { font-size: 12px; color: var(--ink-3); }
.mt-strike-tip { font-size: 11px; color: var(--gold); background: rgba(224,177,92,.06); border-radius: 6px; padding: 4px 8px; margin-top: 4px; }
.mt-strike-risk { font-size: 11px; color: var(--up); margin-top: 2px; }
.mt-strike-why { font-size: 10.5px; color: var(--gold); background: rgba(224,177,92,.06); border-radius: 6px; padding: 4px 8px; margin-top: 4px; }
.mt-now { font-size: 11px; color: var(--ink); background: var(--bg-3); border-radius: 6px; padding: 4px 8px; margin: 5px 0 0; line-height: 1.5; }
.mt-slim { display: flex; align-items: center; gap: 7px; background: var(--bg-2); border: 1px solid var(--line); border-radius: 9px; padding: 6px 10px; margin-bottom: 4px; font-size: 12px; cursor: pointer; }
.mt-slim .st { font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 5px; background: var(--line); color: var(--ink-3); flex: none; }
.mt-slim .st.go { background: var(--up); color: var(--ink); }
.mt-slim .st.alt { background: var(--gold); color: var(--ink); }
.mt-slim .nm { font-weight: 600; color: var(--ink); flex: none; }
.mt-slim .lv { font-size: 10px; color: var(--gold); font-weight: 800; flex: none; }
.mt-slim .nt { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-3); font-size: 11px; }
.mt-slim i { font-style: normal; color: var(--ink-3); font-size: 11px; }
.mt-strike-toggle { text-align: center; font-size: 12px; color: var(--ink-3); padding: 8px 0 2px; cursor: pointer; user-select: none; }
.mt-strike-toggle:active { opacity: .6; }
.mt-strike-note { font-size: 10px; color: var(--ink-3); margin-top: 4px; }
.mt-mini { margin-left: auto; font-size: 10px; color: var(--accent); background: none; border: none; padding: 0; cursor: pointer; flex: none; }

/* ── 弱转强/复核(移动端两行布局: 首行 名称+状态+结果徽标, 次行数据整行不挤压) ── */
.wzq-top { flex-wrap: wrap; row-gap: 4px; }
.wzq-top .nm { flex: none; max-width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wzq-top .rv-tag { margin-left: auto; }
.wzq-sub { font-size: 11px; color: #78839a; line-height: 1.6; margin: 3px 0 0; word-break: break-all; }
/* ── 大模型选股(影子) ── */
.mt-llm-head { display: flex; align-items: center; gap: 8px; padding: 6px 0 2px; flex-wrap: wrap; }
.llm-regime { font-size: 12px; font-weight: 600; padding: 2px 10px; border-radius: 10px; flex: none; white-space: nowrap; }
.llm-regime.go { color: #0a7d34; background: rgba(42,168,92,.12); }
.llm-regime.warn { color: #b06a00; background: rgba(230,146,26,.14); }
.llm-regime.watch { color: var(--accent); background: rgba(41,128,185,.1); }
.llm-regime.ban { color: var(--up); background: rgba(192,57,43,.1); }
.llm-regime.plain { color: var(--ink-3); background: rgba(0,0,0,.05); }
.llm-pos { font-size: 11px; color: #78839a; flex: 1 1 auto; min-width: 0; }
/* 中文无空格, flex 收缩会压成一字一列, 不可买标签禁止收缩 */
.mt-llm-head .rv-tag { margin-left: 0; }
.mt-llm-why { font-size: 11px; color: #5b6a85; line-height: 1.6; padding: 4px 0 8px; border-bottom: 0.5px solid rgba(0,0,0,.04); }
.mt-llm-avoid { font-size: 11px; color: #a05a4a; line-height: 1.6; padding: 8px 0 2px; border-top: 0.5px solid rgba(0,0,0,.04); margin-top: 4px; }
/* 文本密度: 默认截断(依据/回避两行, 候选理由一行), 点击文本或下方小字放全文 */
.clamped { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; cursor: pointer; }
.mt-review .wzq-sub.clamped { -webkit-line-clamp: 1; }
.clamp-line { font-size: 10px; color: var(--accent); text-align: right; padding: 2px 2px 6px; cursor: pointer; }
.mt-llm-avoid + .clamp-line { padding-top: 0; }
.llm-action { font-size: 11px; color: var(--accent); font-weight: 600; }
.llm-pos-tag { font-size: 10px; color: var(--ink-3); margin-left: auto; }
.mt-strike-top { flex-wrap: wrap; row-gap: 4px; }
.mt-strike-top .mt-strike-score { margin-left: auto; }

/* ── 弱转强/复核 ── */
.mt-review { background: var(--bg-2); border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; margin-bottom: 8px; cursor: pointer; }
.mt-review-guide { font-size: 11px; color: var(--ink-3); background: var(--bg-3); border-radius: 8px; padding: 6px 10px; margin-bottom: 8px; line-height: 1.6; }
.rv-tag { font-size: 10px; font-weight: 700; padding: 1px 8px; border-radius: 9px; margin-left: auto; flex: none; }
.rv-tag.hold { background: rgba(255,77,90,.10); color: var(--up); }
.rv-tag.warn { background: rgba(224,177,92,.08); color: var(--gold); }
.rv-tag.sell { background: var(--bg-3); color: #1e7e46; }
.rv-tag.watch { background: var(--line); color: var(--ink-3); }
.mt-review-today { font-size: 12px; margin-top: 2px; font-weight: 600; }
.mt-review-today.rv-hold { color: var(--up); }
.mt-review-today.rv-warn { color: var(--gold); }
.mt-review-today.rv-sell { color: #1e7e46; }

/* ── 天梯 ── */
.mt-group-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--bg-3); cursor: pointer; }
.mt-group-row:last-child { border-bottom: none; }
.mt-group-tag { flex: none; border-radius: 16px; padding: 3px 10px; background: var(--bg-3); color: var(--ink-2); font-size: 12px; font-weight: 600; }
.mt-group-tag.hot { background: rgba(255,77,90,.10); color: var(--up); }
.mt-group-count { flex: none; font-size: 11px; color: var(--ink-3); }
.mt-group-stocks { flex: 1; min-width: 0; font-size: 11px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

@media (max-width: 480px) {
  .mt-page { padding-left: 10px; padding-right: 10px; }
}
@media (min-width: 768px) {
  .mt-page { padding: 8px 28px 20px; }
  .mt-sec { padding: 14px; }
}
</style>
