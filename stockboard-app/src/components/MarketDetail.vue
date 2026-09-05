<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AuctionTab from './AuctionTab.vue'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchFengKou, fetchTianTi, fetchMarketLimitReasons,
  fetchNewHighBoards, fetchNewHighStocks, fetchNewHighTrend,
  fetchGlobalIndexes, fetchInstitutionIncrease,
  fetchMarketMood, fetchMoneyEffect, fetchMarketHighlights, fetchBoardAnnotations, fetchLhbList,
  fetchLimitPool, fetchRiseFall, fetchUnsealedPool,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'
import { loadCycleData, STAGES, STAGE_COLORS, STAGE_RULES } from '../utils/emotionCycle.js'
import { loadBattleData } from '../utils/leaderBattle.js'
import { fetchMyPositions } from '../data/loader.js'
import { jsonp, secid } from '../utils/eastmoney.js'

defineOptions({ name: 'MarketDetail' })

const route = useRoute()
const router = useRouter()
const section = computed(() => route.params.section)

const SECTION_TITLES = {
  auction: '竞价抢筹', wind: '最强风口', ladder: '涨停天梯', reasons: '涨停原因',
  newhighs: '百日新高', global: '外围市场', institution: '机构增仓',
  mood: '市场情绪', live: '盘面动态', lhb: '龙虎榜', cycle: '情绪周期',
  discipline: '我的纪律卡',
}
const title = computed(() => SECTION_TITLES[section.value] || '盘面详情')

const loading = ref(true)
const error = ref(false)
const data = ref(null)
const dayLabel = ref('')

// 百日新高: 板块|个股 子切换
const nhMode = ref('stocks')
// 机构增仓: 含北向|过滤北向
const isBX = ref(false)
// 涨停原因: 行级全文展开
const openIdx = ref(null)

// 情绪周期: JS 引擎实时计算(双实现 — 推送/回测以 emotion_cycle.py 为准, 见 utils/emotionCycle.js 头注)
const cycle = computed(() => data.value?.cycle || null)
// 龙头博弈+今日出击: 纯规则引擎(utils/leaderBattle.js), 出击规则与 stage_candidates.py 成对维护
const battle = computed(() => data.value?.battle || null)
const cyColor = computed(() => STAGE_COLORS[cycle.value?.stage] || '#8a97a8')
const cycleStages = STAGES
const cycleFetchedAt = ref('')
const pctTxt = v => (v === null || v === undefined) ? '无数据' : Math.round(v * 100) + '%'
const yiTxt = v => !v ? '0' : v >= 1e8 ? (v / 1e8).toFixed(1) + '亿' : v >= 1e4 ? (v / 1e4).toFixed(0) + '万' : String(Math.round(v))
const tagCls = t => ({ '主线': 'hot', '卡位上位': 'rise', '扩容': 'up', '萎缩·被抽血': 'fade' }[t] || 'plain')
// goStock 复用下方既有函数(签名 row, 取 row.code)
// cycle 页布局: 决策区(出击默认top3) + 三个分析Tab
const cyTab = ref('war')
const CY_TABS = [
  { key: 'war', label: '⚔️ 博弈' },
  { key: 'leader', label: '👑 龙头' },
  { key: 'cycle2', label: '📐 周期' },
]
const MATRIX_DESC = {
  '强|强': '上升前期 · 最适合做接力，龙头战法最暴力',
  '强|平衡': '上升中后期 · 资金抱团龙头/妖股，开始转低切',
  '强|弱': '情绪末端 · 中位核按钮频发，抱团龙头或转低切',
  '平衡|强': '大周期分歧 · 高位打开赚钱效应，中低位非常活跃',
  '平衡|平衡': '混沌盘面 · 中位均出现分歧，甚至出现退潮风险',
  '平衡|弱': '退潮期 · 情绪很差，低位套利空间不大',
  '弱|强': '大周期分歧 · 空间受压，中位强势补涨',
  '弱|平衡': '试探期 · 高位情绪不佳，整体情绪较差',
  '弱|弱': '全面退潮 · 寸草不生',
}
const matrixCells = computed(() => {
  const h = cycle.value?.matrix.high, m = cycle.value?.matrix.mid
  return ['强', '平衡', '弱'].flatMap(mid => ['强', '平衡', '弱'].map(high => {
    const key = `${high}|${mid}`
    return { k: key, high, mid, on: !!h && !!m && h === high && m === mid, d: MATRIX_DESC[key] }
  }))
})

// ── 纪律卡: 定性→仓位上限(拷问立法版) / 冰点四菜单 / 持仓触价 / 每日三行卡 ──
const mine = computed(() => data.value?.mine || null)
const discMood = computed(() => data.value?.mood || [])
const discRf = computed(() => data.value?.rf || null)
const discCycle = computed(() => data.value?.cycle || null)
const discLive = computed(() => data.value?.live || {})
const discPremium = computed(() => data.value?.premium || null)

const discRule = computed(() => STAGE_RULES[discCycle.value?.stage] || { cap: '—', act: '周期计算中…', hot: '' })

// 冰点四菜单: A跌停萎缩 B昨日涨停溢价>0 C高度板梯队(≥3且未降) D情绪指标≥50
const discMenu = computed(() => {
  const m0 = discMood.value[0], m1 = discMood.value[1]
  const cy = discCycle.value
  const a = !!(m0 && m1 && m0.df < m1.df)
  const b = !!(discPremium.value && discPremium.value.avg > 0)
  const c = !!(cy?.metrics && cy.metrics.height >= 3 && cy.metrics.height >= (cy.metrics.heightPrev ?? 0))
  const d = !!(m0 && m0.strong >= 50)
  return {
    a: { ok: a, txt: m0 && m1 ? `跌停 ${m0.df} 家(昨 ${m1.df})` : '—' },
    b: { ok: b, txt: discPremium.value ? `平均 ${discPremium.value.avg > 0 ? '+' : ''}${discPremium.value.avg.toFixed(2)}%(近似·${discPremium.value.n}只)` : '—' },
    c: { ok: c, txt: cy?.metrics ? `高度 ${cy.metrics.height}B(昨 ${cy.metrics.heightPrev ?? '?'})` : '—' },
    d: { ok: d, txt: m0 ? `情绪 ${m0.strong}` : '—' },
    trial: a && b,   // 试错许可 → 允许 1 成试主线首板
    recover: c && d, // 仓位恢复 → 上限回 5-6 成
  }
})

// 持仓行: 实时价覆盖管线收盘价, 客户端重算触价(秒级)
const discPositions = computed(() => {
  const list = mine.value?.positions || []
  return list.map(p => {
    const live = discLive.value[p.code]
    const price = live?.price ?? p.price ?? null
    const pct = live?.pct ?? p.pct ?? null
    const rb = p.exit_rebound, brk = p.exit_break
    let touch = null
    if (price != null) {
      if (rb && price >= rb[0] && price <= rb[1]) touch = 'rebound'
      else if (brk && price <= brk) touch = 'break'
    }
    const profit = price && p.cost ? Math.round((price / p.cost - 1) * 10000) / 100 : (p.profit_pct ?? null)
    return { ...p, livePrice: price, livePct: pct, liveProfit: profit, touch: touch ?? p.touch, gone: p.auto_status === 'exited?' }
  })
})
const discTouchCount = computed(() => discPositions.value.filter(p => p.touch).length)
const discOps = computed(() => mine.value?.ops_review || null)

// 每日判决: 聚合当日逐笔执法 → 一句判决 + 新增操作标记(对比上次访问)
const DISC_VISIT_KEY = 'sb-discipline-visit'
const discLastVisit = ref('')
const discDaily = computed(() => {
  const o = mine.value?.ops_review
  if (!o) return null
  const n = o.items?.length || 0
  const stage = o.stage || discCycle.value?.stage || ''
  const ruleCnt = {}
  for (const it of (o.items || [])) {
    if (it.verdict === 'bad') for (const r of (it.rules || [])) ruleCnt[r] = (ruleCnt[r] || 0) + 1
  }
  const badSummary = Object.entries(ruleCnt).map(([r, c]) => `${r}×${c}`).join('、')
  let grade, text
  if (!n) {
    grade = 'ok'
    text = ['退潮', '冰点'].includes(stage) ? '退潮期零操作——空仓就是满分答卷' : '今日无操作，保持观察'
  } else if ((o.bad || 0) > 0) {
    grade = 'bad'
    text = `${o.bad} 笔违规（${badSummary}）——罚则：明日执行「${STAGE_RULES[stage]?.cap || '≤2成'}」，违规项写进三行卡复盘`
  } else if ((o.warn || 0) > 0) {
    grade = 'warn'
    text = `${o.warn} 笔警示（追高/止损偏晚），无硬违规——明日把入场分位压到 70% 以下`
  } else {
    grade = 'ok'
    text = `${n} 笔全部合规——卖出端纪律保持，买入端等绿灯`
  }
  let newN = 0, maxTs = ''
  for (const it of (o.items || [])) {
    const ts = `${o.date} ${it.time}`
    if (discLastVisit.value && ts > discLastVisit.value) newN++
    if (ts > maxTs) maxTs = ts
  }
  return { grade, text, n, newN, maxTs, bad: o.bad || 0, warn: o.warn || 0, ok: o.ok || 0 }
})
function discMarkVisit() {
  const maxTs = discDaily.value?.maxTs
  if (!maxTs) return
  try {
    const cur = localStorage.getItem(DISC_VISIT_KEY) || ''
    if (maxTs > cur) localStorage.setItem(DISC_VISIT_KEY, maxTs)
  } catch { /* 隐私模式丢弃 */ }
}

// 养家层: 风险收益比仪表("基于对市场情绪的揣摩, 判断风险收益比的比较") + 龙头解
const discRbr = computed(() => {
  const bp = mine.value?.battle_plan
  if (!bp?.stage) return null
  const base = { 退潮: 1, 冰点: 2, 分歧: 2, 高潮: 2, 启动: 4, 发酵: 4 }[bp.stage] ?? 2
  let stars = base
  if (bp.licenses?.trial) stars = Math.max(stars, 3)
  if (bp.licenses?.recover) stars = 5
  const txt = stars <= 1 ? '风险收益比极差——错过也是盈利'
    : stars === 2 ? '风险收益比不划算——小仓试错或观望'
      : stars === 3 ? '出现可试错结构——限 1 成，错了就走'
        : stars === 4 ? '值得做多的区间——仓位跟赢面走'
          : '赢面最大的阶段——仓位与赢面成正比'
  return { stars, txt }
})
const yjLeader = computed(() => {
  const l = discCycle.value?.leaders?.[0]
  const st = mine.value?.battle_plan?.stage || discCycle.value?.stage
  if (!l) return null
  const m = {
    高潮: `${l.name} ${l.pid}板一致加速——超级高手在卖出龙头，持有不加`,
    退潮: `${l.name}(${l.pid}板) 势走股走——龙头反抽是逃命波，不是买点`,
    冰点: `${l.name} 若能逆市连板，就是新周期的火种`,
    分歧: `${l.name} 的分歧承接是市场最后的防线`,
    启动: `${l.name} 是新周期龙头候选——分歧转一致时上车`,
    发酵: `${l.name} 加速段拿住——强者恒强，直到一致见顶`,
  }
  return { txt: m[st] || `${l.name} ${l.pid}板 空间锚` }
})
const yjQuote = computed(() => STAGE_RULES[(mine.value?.battle_plan?.stage) || (discCycle.value?.stage)]?.yj || '')



// 盘前五数
const discFive = computed(() => {
  const m0 = discMood.value[0], m1 = discMood.value[1]
  const cy = discCycle.value?.metrics, rf = discRf.value
  return [
    { k: '涨停', v: cy ? `${cy.zt} 只` : '—', sub: cy?.ztMa5 ? `均值 ${Math.round(cy.ztMa5)}` : '', good: cy && cy.ztMa5 ? cy.zt >= cy.ztMa5 : null },
    { k: '连板高度', v: cy ? `${cy.height}B` : '—', sub: cy ? `昨 ${cy.heightPrev ?? '?'}` : '', good: cy && cy.heightPrev != null ? cy.height >= cy.heightPrev : null },
    { k: '跌停', v: m0 ? `${m0.df} 家` : '—', sub: m1 ? `昨 ${m1.df}` : '', good: m0 && m1 ? m0.df <= m1.df : null },
    { k: '炸板率', v: rf?.today ? `${rf.today.brokeRate.toFixed(1)}%` : '—', sub: cy?.brokeMa5 ? `均值 ${cy.brokeMa5.toFixed(1)}%` : '', good: rf?.today && cy?.brokeMa5 ? rf.today.brokeRate <= cy.brokeMa5 : null },
    { k: '情绪分', v: m0 ? `${m0.strong}` : '—', sub: m1 ? `昨 ${m1.strong}` : '', good: m0 && m1 ? m0.strong >= m1.strong : null },
  ]
})

// 东财 ulist 批量行情(JSONP 绕 CORS): 持仓实时价 + 昨日涨停溢价
async function fetchEmBatch(codes) {
  const rows = []
  for (let i = 0; i < codes.length; i += 40) {
    const secids = codes.slice(i, i + 40).map(secid).join(',')
    const url = `https://push2delay.eastmoney.com/api/qt/ulist.np/get?secids=${secids}&fields=f2,f3,f12,f14&fltt=2&invt=2`
    try {
      const j = await jsonp(url, 'cb')
      const diff = j?.data?.diff
      const arr = Array.isArray(diff) ? diff : Object.values(diff || {})
      for (const d of arr) {
        const price = parseFloat(d.f2), pct = parseFloat(d.f3)
        rows.push({ code: String(d.f12 ?? ''), price: isNaN(price) ? null : price, pct: isNaN(pct) ? null : pct, name: d.f14 || '' })
      }
    } catch (e) { /* 单批失败忽略 */ }
  }
  return rows
}
async function fetchEmBatchPct(codes) {
  const rows = await fetchEmBatch(codes)
  return rows.map(r => r.pct).filter(p => p != null && !isNaN(p))
}

// 每日三行卡(localStorage, 留 14 天)
const DISC_KEY = 'sb-discipline-log'
const showDiscHist = ref(false)
const discLog = ref([])
const discForm = ref({ verdict: '', cap: '', plan: '', checks: { read: false, verdict: false, orders: false, review: false } })
const todayKey = () => new Date().toLocaleDateString('sv-SE')
const discSuggest = computed(() => {
  const cy = discCycle.value, m0 = discMood.value[0]
  if (!cy?.stage || !cy.metrics) return ''
  const nums = [`涨停${cy.metrics.zt}只${cy.metrics.ztMa5 ? '=' + Math.round(cy.metrics.zt / cy.metrics.ztMa5 * 100) + '%均值' : ''}`, `炸板${cy.metrics.brokeRate ?? '?'}%`]
  if (m0) nums.push(`情绪${m0.strong}`)
  return `${cy.stage}（${nums.join('·')}）→ 上限${discRule.value.cap}`
})
function discLoad() {
  try { discLog.value = JSON.parse(localStorage.getItem(DISC_KEY) || '[]') } catch { discLog.value = [] }
  const t = discLog.value.find(x => x.date === todayKey())
  discForm.value = t
    ? { verdict: t.verdict || '', cap: t.cap || '', plan: t.plan || '', checks: { read: false, verdict: false, orders: false, review: false, ...t.checks } }
    : { verdict: discSuggest.value, cap: discRule.value.cap === '—' ? '' : discRule.value.cap, plan: '原样执行', checks: { read: false, verdict: false, orders: false, review: false } }
}
function discSave() {
  const entry = { date: todayKey(), ...discForm.value, suggest: discSuggest.value, ts: Date.now() }
  discLog.value = [entry, ...discLog.value.filter(x => x.date !== todayKey())].slice(0, 14)
  try { localStorage.setItem(DISC_KEY, JSON.stringify(discLog.value)) } catch { /* 隐私模式丢弃 */ }
}


async function load(silent = false) {
  const s = section.value
  if (s === 'auction') { loading.value = false; return }
  loading.value = true
  if (!silent) error.value = false
  try {
    const day = await getLatestTradingDay()
    const dayDash = day ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : ''
    dayLabel.value = dayDash
    let res = null
    if (s === 'wind') res = await fetchFengKou(silent)
    else if (s === 'ladder') res = await fetchTianTi(silent)
    else if (s === 'reasons') res = dayDash ? await fetchMarketLimitReasons(dayDash, silent) : null
    else if (s === 'newhighs') res = {
      trend: await fetchNewHighTrend(silent),
      boards: await fetchNewHighBoards(silent),
      stocks: await fetchNewHighStocks(silent),
    }
    else if (s === 'global') res = await fetchGlobalIndexes(silent)
    else if (s === 'institution') res = await fetchInstitutionIncrease(getLatestReportDate(), isBX.value, silent)
    else if (s === 'mood') res = {
      mood: await fetchMarketMood(silent),
      effect: dayDash ? await fetchMoneyEffect(dayDash, silent) : null,   // 赚钱效应按最近交易日展开
    }
    else if (s === 'cycle') {
      const cd = await loadCycleData({ fetchTianTi, fetchLimitPool, fetchRiseFall, fetchMarketMood }, dayDash)
      const [battle, mine] = await Promise.all([
        loadBattleData({ fetchLimitPool, fetchUnsealedPool }, cd).catch(() => null),
        fetchMyPositions().catch(() => null),
      ])
      res = { cycle: cd.cycle, battle, mine }
    }
    else if (s === 'discipline') {
      const cd = await loadCycleData({ fetchTianTi, fetchLimitPool, fetchRiseFall, fetchMarketMood }, dayDash)
      const [mine, mood, rf] = await Promise.all([
        fetchMyPositions().catch(() => null),
        fetchMarketMood(silent).catch(() => null),
        fetchRiseFall(silent).catch(() => null),
      ])
      const battle = await loadBattleData({ fetchLimitPool, fetchUnsealedPool }, cd).catch(() => null)
      // B.昨日涨停溢价(近似): 昨日涨停池代码 → 东财 ulist 批量行情平均涨幅
      let premium = null
      try {
        const codes = [...new Set((cd.prevFull || []).map(r => r.code))]
        const pcts = await fetchEmBatchPct(codes)
        if (pcts.length) premium = {
          avg: pcts.reduce((a, b) => a + b, 0) / pcts.length,
          upRatio: pcts.filter(p => p > 0).length / pcts.length, n: pcts.length,
        }
      } catch (e) { /* 溢价缺数据 → B 项显示 — */ }
      // 持仓实时价(批量)
      let live = {}
      try {
        const rows = await fetchEmBatch((mine?.positions || []).map(p => p.code))
        for (const r of rows) if (r.code) live[r.code] = r
      } catch (e) { /* 行情失败 → 用管线收盘价 */ }
      res = { cycle: cd.cycle, ladder: cd.ladderRows, mine, mood, rf, premium, live, battle }
    }
    else if (s === 'live') res = {
      highlights: await fetchMarketHighlights(silent),
      annotations: await fetchBoardAnnotations(silent),
    }
    else if (s === 'lhb') res = await fetchLhbList(silent)
    if (res) data.value = res
    else if (!silent) error.value = true
    if (s === 'cycle' && res) cycleFetchedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    if (s === 'discipline' && !silent) {
      discLoad()
      try { discLastVisit.value = localStorage.getItem(DISC_VISIT_KEY) || '' } catch { discLastVisit.value = '' }
      setTimeout(discMarkVisit, 10000)  // NEW 标记展示 10s 后更新访问水位
    }
  } catch (e) {
    if (!silent) error.value = true
  } finally {
    loading.value = false
  }
}

// 30s 轮询(交易时段; auction 不轮询)
let timer = null
function startTimer() {
  if (timer || !isTradingTime() || section.value === 'auction') return
  timer = setInterval(() => {
    if (!isTradingTime() || section.value === 'auction') { stopTimer(); return }
    load(true)
  }, 30000)
}
function stopTimer() { clearInterval(timer); timer = null }
function onVisibility() {
  if (document.hidden) { stopTimer(); return }
  if (!isTradingTime() || section.value === 'auction') return
  startTimer(); load(true)
}

watch(section, () => {
  // 路由离开盘面二级页(section 变 undefined)时不重载
  if (!section.value) return
  openIdx.value = null
  load()
  startTimer()
})
watch(isBX, () => { load() })

// 下拉刷新: 仅当前激活页面响应(usePullRefresh 按激活态过滤)
usePullRefresh(() => { load(true) })

// KeepAlive: 返回保留状态; onActivated 恢复轮询, onDeactivated 停
// 首次挂载 onMounted+onActivated 双触发 → 加载只在 onActivated 做(inited 分支)
let inited = false
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
})
onActivated(() => {
  if (!inited) { inited = true; load() } else if (isTradingTime() && section.value !== 'auction') load(true)
  startTimer()
})
onDeactivated(() => { stopTimer() })
onUnmounted(() => {
  stopTimer()
  document.removeEventListener('visibilitychange', onVisibility)
})

function goBack() { router.back() }
function goStock(row) { router.push({ path: '/stock/' + row.code, query: { name: row.name } }) }
// 机构增仓/风口是板块维度 → 跳板块详情页(不能当股票跳)
function goBoard(bk) { router.push({ path: '/board/' + bk.bkCode, query: { name: bk.bkName } }) }
// 百日新高板块 → 板块详情, 但用新高口径(该板块新高股列表, 非竞价成分)
function goNewHighBoard(bk) { router.push({ path: '/board/' + bk.bkCode, query: { name: bk.bkName, src: 'nh' } }) }
function fmt(v, d = 2) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—' }
function pct(v) { return (typeof v === 'number' && isFinite(v)) ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }
const up = v => (typeof v === 'number' ? v >= 0 : false)

// 百日新高趋势折线(最近 60 天)
const nhTrendPoints = computed(() => {
  const pts = (data.value?.trend || []).slice(-60)
  if (pts.length < 2) return ''
  const vals = pts.map(p => p.count)
  const max = Math.max(...vals) || 1
  return pts.map((p, i) => `${(i / (pts.length - 1) * 340).toFixed(1)},${(110 - p.count / max * 100).toFixed(1)}`).join(' ')
})
const nhToday = computed(() => {
  const t = data.value?.trend || []
  return t.length ? t[t.length - 1].count : '—'
})

// ── 市场情绪 (mood) ──
const moodToday = computed(() => (data.value?.mood || [])[0] || null)   // info 按 Day 倒序, 最新在前
const moodPrev = computed(() => (data.value?.mood || [])[1] || null)
const moodDelta = computed(() => moodToday.value && moodPrev.value ? moodToday.value.strong - moodPrev.value.strong : null)
// 强度分级色(0-100 评分): 亢奋/活跃/中性/偏弱/冰点
function moodColor(v) {
  if (v >= 75) return '#c0392b'
  if (v >= 60) return '#e74c3c'
  if (v >= 45) return '#e67e22'
  if (v >= 30) return '#27ae60'
  return '#1e8449'
}
// 情绪形容词 + 档位
function moodLevel(v) { return v >= 75 ? 'hot' : v >= 60 ? 'warm' : v >= 45 ? 'mid' : v >= 30 ? 'cool' : 'cold' }
const MOOD_LABELS = { hot: '亢奋', warm: '活跃', mid: '中性', cool: '偏弱', cold: '冰点' }
function moodLabel(v) { return MOOD_LABELS[moodLevel(v)] }
// 半圆仪表盘: 5 档色弧(左冰点→右亢奋) + 指针圆点(位置=强度 0-100)
const GAUGE_SEGS = [
  { c: '#1e8449', lo: 0, hi: 20 }, { c: '#27ae60', lo: 20, hi: 40 }, { c: '#e67e22', lo: 40, hi: 60 },
  { c: '#e74c3c', lo: 60, hi: 80 }, { c: '#c0392b', lo: 80, hi: 100 },
]
function gaugeArc(a1, a2) {
  const r = 80, cx = 100, cy = 100
  const p = a => { const t = a * Math.PI / 180; return [cx + r * Math.cos(t), cy - r * Math.sin(t)] }
  const [x1, y1] = p(a1), [x2, y2] = p(a2)
  return `M ${x1.toFixed(1)} ${y1.toFixed(1)} A ${r} ${r} 0 0 0 ${x2.toFixed(1)} ${y2.toFixed(1)}`
}
const gaugeSegs = GAUGE_SEGS.map(g => ({ color: g.c, d: gaugeArc(180 - g.hi / 100 * 180, 180 - g.lo / 100 * 180) }))
const gaugeDot = computed(() => {
  const v = moodToday.value?.strong ?? 0
  const t = (180 - v / 100 * 180) * Math.PI / 180
  return { x: (100 + 80 * Math.cos(t)).toFixed(1), y: (100 - 80 * Math.sin(t)).toFixed(1) }
})
const GAUGE_LABELS = [ { t: '冰点', x: 22 }, { t: '偏弱', x: 61 }, { t: '中性', x: 100 }, { t: '活跃', x: 139 }, { t: '亢奋', x: 178 } ]
const GAUGE_COLORS = GAUGE_SEGS.map(g => g.c)   // 5 档色(左冰点→右亢奋), 热力 legend 复用
// 30 日情绪热力(时间正序, 今日在右端): SVG 折线, 高度=强度 0-100, 点色=当日分级色
const heatDays = computed(() => (data.value?.mood || []).slice(0, 30).reverse())
const heatX = i => (heatDays.value.length > 1 ? i / (heatDays.value.length - 1) : 0) * 360
const heatY = v => 14 + (1 - Math.max(0, Math.min(100, v)) / 100) * 48   // 100→y14, 0→y62
const heatLine = computed(() => heatDays.value.map((d, i) => `${heatX(i).toFixed(1)},${heatY(d.strong).toFixed(1)}`).join(' '))
const heatArea = computed(() => {
  const n = heatDays.value.length
  if (!n) return ''
  return `${heatDays.value.map((d, i) => `${heatX(i).toFixed(1)},${heatY(d.strong).toFixed(1)}`).join(' ')} ${heatX(n - 1).toFixed(1)},62 0,62`
})
const heatTodayColor = computed(() => moodColor(moodToday.value?.strong ?? 0))
const meGain = computed(() => (data.value?.effect?.gain || []).reduce((a, b) => a + b, 0))
const meLoss = computed(() => (data.value?.effect?.loss || []).reduce((a, b) => a + b, 0))
const meMax = computed(() => {
  const e = data.value?.effect
  if (!e) return 1
  return Math.max(...[...e.loss, ...e.gain], 1)
})
const meWidth = v => `${((v || 0) / meMax.value * 100).toFixed(1)}%`   // 分布条宽按全档最大值归一
// ── 盘面动态 (live) ──
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
// 接口按时间正序(最早在前) → 反转, 最新在上; 亮点 30 条默认折叠 10 条
const showAllLive = ref(false)
const liveAnnotations = computed(() => [...(data.value?.annotations || [])].reverse())
const liveHighlights = computed(() => {
  const list = [...(data.value?.highlights || [])].reverse()
  return showAllLive.value ? list : list.slice(0, 10)
})
// 亮点数据日期提示: 接口可能返回前一日数据(数据源滞后) → 组头标注, 避免误认当天
const liveDataDate = computed(() => {
  const t = data.value?.highlights?.[0]?.time
  if (!t) return ''
  const d = new Date(t * 1000)
  const now = new Date()
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()) return ''
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
})
// ── 龙虎榜 (lhb) ──
function fmtYi(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const s = v / 1e8
  return (s >= 0 ? '+' : '') + s.toFixed(2) + '亿'
}
// 净买条形: 宽度按全部净买额绝对值最大值归一, 正红负绿
const lhbBarMax = computed(() => {
  const l = data.value?.list || []
  return Math.max(...l.map(s => Math.abs(s.buyIn)), 1)
})
const lhbBarW = v => (Math.abs(v) / lhbBarMax.value * 100).toFixed(1) + '%'
// 标注情绪色: 1=利多红 / -1=利空绿 / 0=中性灰(接口 Color 字段)
function annColor(c) { return c === '1' ? '#e74c3c' : c === '-1' ? '#27ae60' : '#d5dbe3' }
const fmtAmt = v => (typeof v === 'number' && isFinite(v) && v > 0) ? (v / 1e8).toFixed(1) + '亿' : ''
// 龙虎榜排序切换: 净买(默认)/机构家数/振幅/换手率
const lhbSort = ref('buyIn')
const LHB_SORTS = [
  { key: 'buyIn', label: '净买' },
  { key: 'joinNum', label: '机构' },
  { key: 'amplitude', label: '振幅' },
  { key: 'turnoverRatio', label: '换手' },
]
const lhbSorted = computed(() => {
  const key = lhbSort.value
  return [...(data.value?.list || [])].sort((a, b) => ((b[key] || 0)) - ((a[key] || 0)))
})
</script>

<template>
  <div class="md-page">
    <!-- 顶部导航由 App header 统一提供(返回+标题), 此处仅显示数据日期 -->
    <p v-if="dayLabel" class="md-dayline">数据日期 {{ dayLabel }}</p>

    <!-- 竞价: 完整复用现有 AuctionTab -->
    <AuctionTab v-if="section === 'auction'" />

    <div v-else-if="loading" class="sd-loading">加载中…</div>
    <div v-else-if="error" class="sd-error">
      加载失败
      <button class="sd-retry" @click="load()">重试</button>
    </div>

    <!-- 最强风口: 个股行列表(个股维度, 点击进个股详情) -->
    <template v-else-if="section === 'wind'">
      <div class="md-list">
        <div class="md-list-head">
          <span class="md-row-name">个股</span>
          <span class="md-tags">标签</span>
          <span class="md-row-chg">涨幅</span>
        </div>
        <div v-for="w in data" :key="w.code" class="md-row" @click="goStock(w)">
          <span class="md-row-name">{{ w.name }}</span>
          <span v-if="w.tags && w.tags.length" class="md-tags">{{ w.tags.slice(0, 2).join('/') }}</span>
          <span class="md-row-chg" :style="{ color: up(w.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(w.chgPct) }}</span>
        </div>
        <div v-if="!data || !data.length" class="md-empty">暂无数据</div>
      </div>
    </template>

    <!-- 涨停天梯: 连板分组 -->
    <template v-else-if="section === 'ladder'">
      <div v-for="g in data" :key="g.key" class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag" :style="{ background: g.level >= 3 ? '#fdecea' : '#f0f2f5', color: g.level >= 3 ? '#c0392b' : '#666' }">{{ g.title }}</span>
          <span class="md-group-count">{{ g.rows.length }} 只</span>
        </div>
        <div class="md-group-body">
          <div v-for="r in g.rows" :key="r.code" class="md-stock-row" @click="goStock(r)">
            <span class="md-stock-name">{{ r.name }}</span>
            <span class="md-level">{{ r.label || r.level + '板' }}</span>
            <span class="md-stock-bk">{{ r.bkName }}</span>
            <span class="md-stock-go">›</span>
          </div>
        </div>
      </div>
    </template>

    <!-- 涨停原因: 家数 + 板块分组个股 + 原因展开 -->
    <template v-else-if="section === 'reasons'">
      <div class="md-summary">今日涨停 <b class="md-hot">{{ data.nums.ZT ?? '—' }}</b> 家</div>
      <div v-for="(g, gi) in data.groups" :key="g.bkCode" class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">{{ g.bkName }}</span>
          <span class="md-group-count">{{ g.stocks.length }} 家</span>
        </div>
        <div class="md-group-body">
          <div v-for="(r, i) in g.stocks" :key="r.code" class="md-stock-row" @click="goStock(r)">
            <div class="md-stock-main">
              <div class="md-stock-line1">
                <span class="md-stock-name">{{ r.name }}</span>
                <span class="md-stock-chg" :style="{ color: up(r.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(r.chgPct) }}</span>
                <span v-if="r.level" class="md-level">{{ r.level }}</span>
                <span class="md-stock-toggle" @click.stop="openIdx = openIdx === (gi + '-' + i) ? null : (gi + '-' + i)">{{ openIdx === gi + '-' + i ? '收起' : '原因' }}</span>
              </div>
              <p v-if="openIdx === gi + '-' + i" class="md-reason">{{ r.reason }}</p>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 百日新高: 趋势 + 板块/个股切换 -->
    <template v-else-if="section === 'newhighs'">
      <div class="md-summary">今日新高 <b class="md-hot">{{ nhToday }}</b> 家</div>
      <div class="md-svg-block">
        <svg viewBox="0 0 340 120" preserveAspectRatio="none" class="md-svg">
          <line v-for="y in [30, 60, 90]" :key="y" x1="0" :x2="340" :y1="y" :y2="y" stroke="#f0f0f0" stroke-width="1"/>
          <polyline :points="nhTrendPoints" fill="none" stroke="#e67e22" stroke-width="1.5"/>
        </svg>
        <div class="md-svg-scale"><span>近 60 天每日新高数</span><span>今日 {{ nhToday }}</span></div>
      </div>
      <div class="sd-tabs md-subtabs">
        <button :class="['sd-tab', 'small', { on: nhMode === 'stocks' }]" @click="nhMode = 'stocks'">个股</button>
        <button :class="['sd-tab', 'small', { on: nhMode === 'boards' }]" @click="nhMode = 'boards'">板块</button>
      </div>
      <!-- 个股视图: 新高个股行, 点击跳个股 -->
      <div v-if="nhMode === 'stocks'" class="md-list">
        <div class="md-list-head">
          <span class="md-row-name">个股</span>
          <span class="md-tags">标签</span>
          <span class="md-row-chg">涨幅</span>
        </div>
        <div v-for="r in data.stocks" :key="r.code" class="md-row" @click="goStock(r)">
          <span class="md-row-name">{{ r.name }}</span>
          <span v-if="r.tags && r.tags.length" class="md-tags">{{ r.tags.slice(0, 2).join('/') }}</span>
          <span class="md-row-chg" :style="{ color: up(r.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(r.chgPct) }}</span>
        </div>
        <div v-if="!data.stocks.length" class="md-empty">暂无数据</div>
      </div>
      <!-- 板块视图: 板块名 + 新高家数, 点击跳板块详情(新高口径 src=nh, 与竞价成分区分) -->
      <div v-else class="md-list">
        <div class="md-list-head">
          <span class="md-row-name">板块</span>
          <span class="md-row-str">新高家数</span>
        </div>
        <div v-for="r in data.boards" :key="r.bkCode" class="md-row" @click="goNewHighBoard(r)">
          <span class="md-row-name">{{ r.bkName }}</span>
          <span class="md-row-str" style="color:#e67e22">{{ r.count }} 家新高</span>
        </div>
        <div v-if="!data.boards.length" class="md-empty">暂无数据</div>
      </div>
    </template>

    <!-- 外围市场 -->
    <template v-else-if="section === 'global'">
      <div v-if="data.indexes && data.indexes.length" class="md-group">
        <div class="md-group-head"><span class="md-group-tag">主要指数</span></div>
        <div class="md-group-body">
          <div class="md-list-head">
            <span class="md-stock-name">指数</span>
            <span class="md-stock-val">最新</span>
            <span class="md-stock-chg">涨跌幅</span>
          </div>
          <div v-for="g in data.indexes" :key="g.code" class="md-stock-row">
            <span class="md-stock-name">{{ g.name }}</span>
            <span class="md-stock-val">{{ fmt(g.last) }}</span>
            <span class="md-stock-chg" :style="{ color: up(g.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(g.chgPct) }}</span>
          </div>
        </div>
      </div>
      <div v-if="data.futures && data.futures.length" class="md-group">
        <div class="md-group-head"><span class="md-group-tag">股指期货</span></div>
        <div class="md-group-body">
          <div class="md-list-head">
            <span class="md-stock-name">合约</span>
            <span class="md-stock-val">最新</span>
            <span class="md-stock-chg">涨跌幅</span>
          </div>
          <div v-for="g in data.futures" :key="g.code" class="md-stock-row">
            <span class="md-stock-name">{{ g.name }}</span>
            <span class="md-stock-val">{{ fmt(g.last) }}</span>
            <span class="md-stock-chg" :style="{ color: up(g.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(g.chgPct) }}</span>
          </div>
        </div>
      </div>
      <div v-if="data.movers && data.movers.length" class="md-group">
        <div class="md-group-head"><span class="md-group-tag">异动</span></div>
        <div class="md-group-body">
          <div class="md-list-head">
            <span class="md-stock-name">品种</span>
            <span class="md-stock-val">最新</span>
            <span class="md-stock-chg">涨跌幅</span>
          </div>
          <div v-for="g in data.movers" :key="g.code" class="md-stock-row">
            <span class="md-stock-name">{{ g.name }}</span>
            <span class="md-stock-val">{{ fmt(g.last) }}</span>
            <span class="md-stock-chg" :style="{ color: up(g.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(g.chgPct) }}</span>
          </div>
        </div>
      </div>
    </template>

    <!-- 机构增仓: 板块行列表; IsBX=1 含北向资金席位增仓 / 0 过滤北向(仅机构净买入) -->
    <template v-else-if="section === 'institution'">
      <div class="md-switch-row">
        <span class="md-switch-label">机构增仓</span>
        <label class="md-switch">
          <input type="checkbox" :checked="isBX" @change="isBX = $event.target.checked">
          <span class="md-switch-track"><span class="md-switch-knob"></span></span>
        </label>
        <span class="md-switch-text">{{ isBX ? '含北向资金' : '过滤北向资金' }}</span>
      </div>
      <div class="md-list">
        <div class="md-list-head">
          <span class="md-row-name">板块</span>
          <span class="md-tags" title="机构增仓占板块流通比例(接口原始字段, 口径未公开; 负值=该期净减仓)">占比%</span>
          <span class="md-row-str">增仓额</span>
        </div>
        <div v-for="g in data" :key="g.bkCode" class="md-row" @click="goBoard(g)">
          <span class="md-row-name">{{ g.bkName }}</span>
          <span v-if="g.ratio" class="md-tags">{{ fmt(g.ratio, 1) }}%</span>
          <span class="md-row-str" style="color:#c0392b">{{ fmt(g.addAmt, 1) }}亿</span>
        </div>
        <div v-if="!data || !data.length" class="md-empty">暂无数据</div>
      </div>
    </template>

    <!-- 市场情绪: 强度折线 + 赚钱效应分布 + 近10日情绪表 -->
    <template v-else-if="section === 'mood'">
      <div class="md-summary" v-if="moodToday">今日涨停 <b style="color:#c0392b">{{ moodToday.zt }}</b> 家 · 连板 {{ moodToday.lbgd }} · 跌停 {{ moodToday.df }}</div>
      <!-- 情绪仪表盘: 半圆档位弧 + 指针 + 中央数值 -->
      <div class="md-gauge-block">
        <svg viewBox="0 0 200 118" class="md-gauge">
          <path v-for="seg in gaugeSegs" :key="seg.color" :d="seg.d" :stroke="seg.color" fill="none" stroke-width="14"/>
          <circle :cx="gaugeDot.x" :cy="gaugeDot.y" r="7" fill="#fff" stroke="#333" stroke-width="2.5"/>
          <text v-for="l in GAUGE_LABELS" :key="l.t" :x="l.x" y="113" text-anchor="middle" font-size="9" fill="#999">{{ l.t }}</text>
          <text x="100" y="64" text-anchor="middle" font-size="30" font-weight="700" :fill="moodColor(moodToday.strong)">{{ moodToday.strong }}</text>
          <text x="100" y="82" text-anchor="middle" font-size="12" :fill="moodColor(moodToday.strong)">
            {{ moodLabel(moodToday.strong) }}<tspan v-if="moodDelta !== null" :fill="moodDelta >= 0 ? '#e74c3c' : '#27ae60'">{{ moodDelta >= 0 ? ' ↗' : ' ↘' }} {{ Math.abs(moodDelta) }} 较昨日</tspan>
          </text>
          <text x="100" y="97" text-anchor="middle" font-size="8" fill="#bbb">0-100 综合评分</text>
        </svg>
      </div>

      <!-- 赚钱效应: 各档家数分布条(左亏右赚, 宽度按档位归一) -->
      <div v-if="data.effect" class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">💰 赚钱效应</span>
          <span class="md-group-count">{{ data.effect.day }} · {{ data.effect.num }} 家上榜</span>
        </div>
        <div class="md-effect">
          <div class="me-bar">
            <div class="me-half">
              <div v-for="(v, i) in [...data.effect.loss].reverse()" :key="'l' + i" class="me-seg loss" :style="{ width: meWidth(v) }" :title="'亏' + (5 - i) + '档 ' + v + '家'"></div>
            </div>
            <div class="me-mid"></div>
            <div class="me-half">
              <div v-for="(v, i) in data.effect.gain" :key="'g' + i" class="me-seg gain" :style="{ width: meWidth(v) }" :title="'赚' + (5 - i) + '档 ' + v + '家'"></div>
            </div>
          </div>
          <div class="me-legend">
            <span>大亏 {{ meLoss }} 家</span>
            <span class="me-ttag">净赚 {{ meGain - meLoss }} 家</span>
            <span>大赚 {{ meGain }} 家</span>
          </div>
          <div class="me-scale" title="档位 = 当日涨跌幅分档(1~5 由小到大); 大赚/大亏 = 达到该档的家数, 非全市场涨跌家数">
            <span>← 大亏 5~1 档</span><span>大赚 1~5 档 →</span>
          </div>
        </div>
      </div>

      <!-- 30 日情绪热力: 强度折线(高度=评分, 点色=当日分级), 一眼看趋势 -->
      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">📈 情绪热力</span>
          <span class="md-group-count">近 30 日强度走势 · 今日在最右</span>
        </div>
        <div class="md-heat">
          <svg v-if="heatDays.length > 1" viewBox="0 0 360 76" class="md-heat-chart">
            <defs>
              <linearGradient id="heatFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" :stop-color="heatTodayColor" stop-opacity=".25"/>
                <stop offset="100%" :stop-color="heatTodayColor" stop-opacity="0"/>
              </linearGradient>
            </defs>
            <!-- 中性参考线(50 分) -->
            <line x1="0" :y1="heatY(50)" x2="360" :y2="heatY(50)" stroke="#eef1f5" stroke-dasharray="4,4"/>
            <polygon :points="heatArea" fill="url(#heatFill)"/>
            <polyline :points="heatLine" fill="none" :stroke="heatTodayColor" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
            <circle v-for="(d, i) in heatDays" :key="d.day" :cx="heatX(i).toFixed(1)" :cy="heatY(d.strong).toFixed(1)" r="2.2" :fill="moodColor(d.strong)"
              :title="`${d.day} 强度 ${d.strong} · 涨停${d.zt} 连板${d.lbgd} 跌停${d.df}`"/>
            <!-- 今日点高亮 + 数值 -->
            <circle :cx="heatX(heatDays.length - 1).toFixed(1)" :cy="heatY(heatDays[heatDays.length - 1].strong).toFixed(1)" r="5" fill="#fff" :stroke="heatTodayColor" stroke-width="2.5"/>
            <text :x="heatX(heatDays.length - 1).toFixed(1)" y="10" text-anchor="middle" font-size="11" font-weight="700" :fill="heatTodayColor">{{ moodToday?.strong }}</text>
          </svg>
          <div v-else class="md-heat-empty">数据不足</div>
        </div>
        <div class="md-heat-legend">
          <span v-for="c in GAUGE_COLORS" :key="c" class="md-heat-lg" :style="{ background: c }"></span>
          <span class="md-heat-lg-txt">冰点 → 亢奋 · 点=当日强度</span>
        </div>
        <div class="md-heat-scale">
          <span v-if="heatDays[0]">{{ heatDays[0].day.slice(5) }}</span>
          <span>今日 {{ moodToday?.day?.slice(5) }}</span>
        </div>
      </div>
    </template>

    <template v-else-if="section === 'cycle'">
      <div v-if="cycle" class="md-cycle">
        <!-- 决策区①: 紧凑阶段条(阶段+依据+纪律一行) + 六段刻度 -->
        <div class="cy-hero" :style="{ borderColor: cyColor }">
          <div class="cy-hero-row">
            <span class="cy-stage" :style="{ color: cyColor }">{{ cycle.stage }}</span>
            <span class="cy-conf">置信度 {{ cycle.confidence }}/9 · 数据日 {{ cycle.date }}</span>
            <span v-if="battle && !battle.empty" class="cy-gate" :class="{ ban: battle.strike.gate.cap === 0 }">
              闸门: {{ battle.strike.gate.cap === 0 ? '禁买' : battle.strike.gate.cap >= 100 ? '全开' : '限' + battle.strike.gate.cap }}
            </span>
          </div>
          <ul class="cy-reasons"><li v-for="r in cycle.reasons" :key="r">{{ r }}</li></ul>
          <div class="cy-playbook">📌 {{ cycle.playbook }}</div>
          <div v-if="discRbr" class="cy-rbr">⚖️ 风险收益比 <span class="rbr-stars">{{ '★'.repeat(discRbr.stars) }}{{ '☆'.repeat(5 - discRbr.stars) }}</span><span class="rbr-txt">{{ discRbr.txt }}</span></div>
          <div v-if="yjQuote" class="cy-yj">💬 {{ yjQuote }}</div>
        </div>
        <div class="cy-scale">
          <div v-for="s in cycleStages" :key="s" class="cy-seg" :class="{ on: s === cycle.stage }"
               :style="s === cycle.stage ? { background: cyColor, borderColor: cyColor } : {}">{{ s }}</div>
        </div>

        <!-- 分析Tab: 博弈 / 龙头 / 周期 -->
        <div class="mt-tabs cy-tabs">
          <button v-for="t in CY_TABS" :key="t.key" class="mt-tab" :class="{ on: cyTab === t.key }" @click="cyTab = t.key">{{ t.label }}</button>
        </div>

        <!-- Tab ⚔️ 博弈 -->
        <div v-show="cyTab === 'war'" class="cy-pane">
          <div v-if="battle && !battle.empty" class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">⚔️ 板块之争</span>
              <span v-if="battle.boardWars.mainSwitch" class="md-group-count lb-switch">{{ battle.boardWars.mainSwitch.note }}</span>
            </div>
            <div v-for="w in battle.boardWars.wars.slice(0, 6)" :key="w.board" class="cy-line">
              <span class="lb-tag" :class="tagCls(w.tag)">{{ w.tag }}</span>
              <b>{{ w.board }}</b> 今 {{ w.count }} 只
              <span class="lb-delta" :class="w.dCount >= 0 ? 'up' : 'dn'">{{ w.dCount >= 0 ? '+' : '' }}{{ w.dCount }}</span>
              <span class="cy-names">昨 {{ w.prevCount }} 只 · 最高 {{ w.maxH }}板 · 封单合计 {{ yiTxt(w.sealSum) }}</span>
            </div>
            <div v-for="r in battle.boardWars.relations" :key="r.a + r.b" class="lb-rel">
              <b>{{ r.a }}</b> × <b>{{ r.b }}</b>
              <span class="lb-tag" :class="r.rel === '竞争切换' ? 'fade' : 'rise'">{{ r.rel }}</span>
              <span class="cy-names">{{ r.note }}</span>
            </div>
          </div>
          <div v-if="battle && !battle.empty && battle.duels.length" class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">🥊 高标对决</span>
              <span class="md-group-count">压制 / 卡位 / 接棒</span>
            </div>
            <div v-for="(d, i) in battle.duels" :key="i" class="cy-line">
              <span class="lb-tag plain">{{ d.type }}</span>
              <b @click.stop="d.a && d.a.code && goStock(d.a)" class="lb-link">{{ d.a?.name || d.a?.board }}</b>
              <template v-if="d.b"> × <b @click.stop="d.b.code && goStock(d.b)" class="lb-link">{{ d.b.name }}</b></template>
              <span class="cy-names">{{ d.verdict }}</span>
            </div>
          </div>
          <div v-if="battle && !battle.empty && battle.risks.brokenHighs.length" class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">🚨 高标开板</span>
              <span class="md-group-count">盘中分歧信号</span>
            </div>
            <div v-for="b in battle.risks.brokenHighs" :key="b.code" class="cy-line" @click="goStock(b)">
              <b class="lb-link">{{ b.name }}</b> <span class="lb-lv">{{ b.level }}板</span>
              <span class="lb-delta dn">{{ b.pct.toFixed(1) }}%</span>
              <span class="cy-names">{{ b.note }}</span>
            </div>
          </div>
          <div v-if="battle && !battle.empty && battle.risks.watch.length" class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">🔭 卡位雷达</span>
              <span class="md-group-count">早封+封单保持+主力净买</span>
            </div>
            <div v-for="w in battle.risks.watch" :key="w.code" class="cy-line" @click="goStock(w)">
              <b class="lb-link">{{ w.name }}</b> <span class="lb-lv">{{ w.level }}板</span>
              <span class="lb-tag rise">{{ w.board }}</span>
              <span class="cy-names">{{ w.note }}</span>
              <span v-if="w.tip" class="lb-tip">{{ w.tip }}</span>
            </div>
          </div>
        </div>

        <!-- Tab 👑 龙头 -->
        <div v-show="cyTab === 'leader'" class="cy-pane">
          <div class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">👑 龙头谱系</span>
            </div>
            <div v-if="yjLeader" class="dv-yj" style="margin: 0 0 8px">👑 {{ yjLeader.txt }}</div>
            <div v-for="l in cycle.leaders" :key="l.code + l.role" class="cy-line" @click="goStock(l)">
              <b class="lb-link">{{ l.name }}</b> {{ l.pid }}板 <span class="cy-role">[{{ l.role }}]</span>
              <span class="cy-names">{{ l.note }}</span>
            </div>
          </div>
          <div class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">🎨 主线板块</span>
              <span class="md-group-count">按涨停家数排序</span>
            </div>
            <div v-for="a in cycle.mainlines" :key="a.board" class="cy-line">
              <b>{{ a.board }}</b> {{ a.count }} 只 · 最高 {{ a.maxLevel }} 板
              <span class="cy-names">{{ a.names.slice(0, 5).join('、') }}</span>
            </div>
          </div>
        </div>

        <!-- Tab 📐 周期 -->
        <div v-show="cyTab === 'cycle2'" class="cy-pane">
          <div class="cy-metrics">
            <div class="cy-mi"><span class="cy-mi-v">{{ cycle.metrics.height }}B</span><span class="cy-mi-l">最高连板(昨 {{ cycle.metrics.heightPrev ?? '-' }})</span></div>
            <div class="cy-mi"><span class="cy-mi-v">{{ cycle.metrics.zt }}</span><span class="cy-mi-l">涨停(ma5 {{ Math.round(cycle.metrics.ztMa5 || 0) }})</span></div>
            <div class="cy-mi"><span class="cy-mi-v">{{ cycle.metrics.brokeRate }}%</span><span class="cy-mi-l">破板率</span></div>
          </div>
          <div class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">🪜 梯队晋级</span>
              <span class="md-group-count">低位 {{ cycle.metrics.ladder.low }} · 中位 {{ cycle.metrics.ladder.mid }} · 高位 {{ cycle.metrics.ladder.high }}</span>
            </div>
            <div class="cy-promo">
              <div class="cy-pi"><span>低位(1-2板)</span><b>{{ pctTxt(cycle.metrics.promo.low) }}</b></div>
              <div class="cy-pi"><span>中位(3-5板)</span><b>{{ pctTxt(cycle.metrics.promo.mid) }}</b></div>
              <div class="cy-pi"><span>高位(≥6板)</span><b>{{ pctTxt(cycle.metrics.promo.high) }}</b></div>
            </div>
          </div>
          <div class="md-group">
            <div class="md-group-head">
              <span class="md-group-tag">🧮 高中位矩阵</span>
              <span class="md-group-count">当前: 高位{{ cycle.matrix.high }} × 中位{{ cycle.matrix.mid }}</span>
            </div>
            <div class="cy-matrix">
              <div class="cy-cell head"></div>
              <div class="cy-cell head" v-for="mid in ['强', '平衡', '弱']" :key="'h' + mid">中位{{ mid }}</div>
              <template v-for="cell in matrixCells" :key="cell.k">
                <div class="cy-cell head" v-if="cell.mid === '强'">高位{{ cell.high }}</div>
                <div class="cy-cell" :class="{ on: cell.on }">
                  <span class="cy-cell-t">{{ cell.on ? '◀ 当前' : cell.high + '-' + cell.mid }}</span>
                  <span class="cy-cell-d">{{ cell.d }}</span>
                </div>
              </template>
            </div>
          </div>
        </div>

        <div class="md-summary">⚡ 打开页面时经 KPL 实时数据计算 · {{ cycleFetchedAt || '计算中' }} · 交易时段 30s 自动刷新</div>
      </div>
    </template>

    <!-- 我的纪律卡: 定性/五数/冰点许可/持仓处理/每日三行 -->
    <template v-else-if="section === 'discipline'">
      <div v-if="mine?.weekly_focus" class="md-group disc-focus">🎯 {{ mine.weekly_focus }}</div>

      <div v-if="mine?.battle_plan?.stage" class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag" style="color:#e67e22">🎯 今日作战指引</span>
          <span class="md-group-count">管线 {{ mine.as_of.slice(5, 16) }} · 每班更新</span>
        </div>
        <div class="bp-top">
          <div class="dv-top">
            <b class="dv-stage" style="font-size:20px">{{ mine.battle_plan.stage }}</b>
            <span class="dv-cap">{{ mine.battle_plan.cap }}</span>
          </div>
          <p class="bp-line">{{ mine.battle_plan.order_line }}</p>
          <p class="bp-act">{{ mine.battle_plan.act }}</p>
        </div>
        <div class="bp-lamps">
          <span v-for="m in [['A', mine.battle_plan.licenses.a], ['B', mine.battle_plan.licenses.b], ['C', mine.battle_plan.licenses.c], ['D', mine.battle_plan.licenses.d]]" :key="m[0]" class="dv-lamp" :class="{ on: m[1].ok }">{{ m[0] }} {{ m[1].txt }}</span>
          <span class="dv-lamp" :class="{ on: mine.battle_plan.licenses.trial }">🕯️ 试错许可</span>
          <span class="dv-lamp" :class="{ on: mine.battle_plan.licenses.recover }">🔥 仓位恢复</span>
        </div>
        <div class="bp-orders">
          <div class="bp-orders-head">📋 持仓挂单清单（9:15 前挂好不撤）</div>
          <div v-for="o in mine.battle_plan.orders" :key="o.code" class="bp-order" @click="goStock(o)">
            <b>{{ o.name }}</b><span>{{ o.action }}</span>
          </div>
        </div>
        <div v-if="mine.battle_plan.watchlist?.length || !mine.battle_plan.licenses.trial" class="bp-watch">
          <div class="bp-orders-head">🔭 若要买，只允许这些主线（首板/龙头低吸）</div>
          <div class="bp-watch-row">
            <span v-for="w in mine.battle_plan.watchlist" :key="w.board" class="dp-board vb-watch">[{{ w.board }}] {{ w.zt }}只·最高{{ w.max_lv }}B</span>
            <span v-if="!mine.battle_plan.licenses.trial" class="dp-board vb-dead">试错许可未亮 → 只观察不出手</span>
          </div>
        </div>
      </div>

      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag" :style="{ color: STAGE_COLORS[discCycle?.stage] || '#8a97a8' }">🧭 今日定性</span>
          <span class="md-group-count">盘面实时 · 交易时段 30s 刷新</span>
        </div>
        <div class="disc-verdict" :style="{ '--sc': STAGE_COLORS[discCycle?.stage] || '#8a97a8' }">
          <div class="dv-top">
            <b class="dv-stage">{{ discCycle?.stage || '计算中' }}</b>
            <span class="dv-cap">{{ discRule.cap }}</span>
            <span v-if="discRule.hot" class="dv-hot">{{ discRule.hot }}</span>
          </div>
          <p class="dv-act">{{ discRule.act }}</p>
          <p v-for="(r, i) in discCycle?.reasons || []" :key="i" class="dv-reason">· {{ r }}</p>
          <div class="dv-lamps">
            <span class="dv-lamp" :class="{ on: discMenu.trial }">🕯️ 试错许可 A+B（1成试主线首板）</span>
            <span class="dv-lamp" :class="{ on: discMenu.recover }">🔥 仓位恢复 C+D（5-6成）</span>
          </div>
        </div>
      </div>

      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">📖 盘前五数</span>
          <span class="md-group-count">{{ discCycle?.date || dayLabel }} · 红强绿弱</span>
        </div>
        <div class="disc-five">
          <div v-for="f in discFive" :key="f.k" class="df-item">
            <span class="df-k">{{ f.k }}</span>
            <b class="df-v" :style="{ color: f.good === true ? '#e74c3c' : f.good === false ? '#27ae60' : '#333' }">{{ f.v }}</b>
            <span class="df-sub">{{ f.sub }}</span>
          </div>
        </div>
        <div class="disc-menu">
          <div v-for="m in [['A', discMenu.a, '跌停萎缩'], ['B', discMenu.b, '昨日涨停溢价'], ['C', discMenu.c, '高度板梯队'], ['D', discMenu.d, '情绪指标≥50']]" :key="m[0]" class="dm-item" :class="{ ok: m[1].ok }">
            <i>{{ m[0] }}</i> {{ m[2] }} <b>{{ m[1].txt }}</b>
          </div>
        </div>
        <div class="md-summary">两灯规则：A+B 同日亮 → 允许 1 成试错当日最强主线首板；C+D 同日亮 → 仓位上限恢复 5-6 成</div>
      </div>

      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">✂️ 持仓处理({{ discPositions.length }})</span>
          <span v-if="mine?.as_of" class="md-group-count">价表 {{ mine.as_of.slice(5, 16) }}</span>
          <span v-if="discTouchCount" class="md-group-count" style="color:#e67e22;font-weight:700">⚠️ {{ discTouchCount }} 只触价</span>
        </div>
        <div v-if="!mine" class="disc-empty">my_positions.json 未生成 — 先在 jiarenmens 跑 export_json.py</div>
        <div v-for="p in discPositions" :key="p.code" class="disc-pos" :class="{ 'is-touch': p.touch, 'is-gone': p.gone }">
          <div class="dp-main">
            <b class="dp-name" @click="goStock(p)">{{ p.name }}</b>
            <span class="dp-weight">{{ p.weight }}</span>
            <span class="dp-price">现 {{ p.livePrice ?? p.price ?? '—' }}<i v-if="p.livePct != null" :style="{ color: p.livePct >= 0 ? '#e74c3c' : '#27ae60' }"> {{ p.livePct >= 0 ? '+' : '' }}{{ p.livePct }}%</i></span>
            <span class="dp-anchor" title="心中无顶底：成本仅作记录，操作只看信号">锚 {{ p.liveProfit != null ? (p.liveProfit > 0 ? '+' : '') + p.liveProfit + '%' : '—' }}</span>
            <span v-if="p.primary_board && p.primary_board.matched" class="dp-shi" :class="p.primary_board.zt > p.primary_board.zt_prev ? 'up' : (p.primary_board.zt < p.primary_board.zt_prev ? 'down' : '')">势{{ p.primary_board.zt > p.primary_board.zt_prev ? '增' : (p.primary_board.zt < p.primary_board.zt_prev ? '减' : '平') }}</span>
            <span v-if="p.hot?.rank" class="dp-hot">🔥#{{ p.hot.rank }}<i v-if="p.hot.delta" :class="p.hot.delta > 0 ? 'up' : 'down'">{{ p.hot.delta > 0 ? '↑' + p.hot.delta : '↓' + (-p.hot.delta) }}</i></span>
            <span v-if="p.touch" class="dp-touch">{{ p.touch === 'rebound' ? '触价·反抽执行' : '触价·无条件走' }}</span>
            <span v-else-if="p.gone" class="dp-badge">rtV2 未见持仓</span>
            <span v-else-if="p.auto_status === 'selling'" class="dp-badge warn">今日有净卖出</span>
            <span v-else-if="p.status === 'keep'" class="dp-badge keep">留仓</span>
          </div>
          <div class="dp-levels">
            <span v-if="p.exit_rebound" class="dp-lv">反抽 {{ p.exit_rebound[0] }}-{{ p.exit_rebound[1] }}</span>
            <span v-if="p.exit_break != null" class="dp-lv">破位 {{ p.exit_break }}</span>
            <span v-if="p.primary_board && p.primary_board.matched" class="dp-board" :class="'vb-' + ({ 主线: 'main', 次主线: 'sub', 观察: 'watch' }[p.primary_board.verdict] || 'dead')">
              [{{ p.primary_board.name }}] 今日{{ p.primary_board.zt }}只(昨{{ p.primary_board.zt_prev }})·最高{{ p.primary_board.max_lv }}B·{{ p.primary_board.verdict }}
            </span>
            <span v-else-if="!p.boards?.length" class="dp-board vb-dead">无板块梯队（独立妖股）</span>
            <span v-else-if="p.primary_board" class="dp-board vb-dead">[{{ p.primary_board.name }}] 板块无涨停记录·非主线</span>
          </div>
          <div class="dp-note">{{ p.note }}</div>
        </div>
        <div v-if="mine" class="disc-asof">板块口径 {{ mine.board_asof || '—' }} · 溢价近似口径 · 盘后实时价=收盘价{{ mine.trades_asof ? ` · 调仓核对至 ${mine.trades_asof.slice(4, 6)}-${mine.trades_asof.slice(6, 8)}` : '' }}</div>
      </div>

      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">🕵️ 操作点评({{ discOps?.date?.slice(4, 6) }}-{{ discOps?.date?.slice(6, 8) }})</span>
          <span class="md-group-count">❌ {{ discOps?.bad || 0 }} · ⚠️ {{ discOps?.warn || 0 }} · ✅ {{ discOps?.ok || 0 }} · {{ discOps?.stage || '—' }}期</span>
        </div>
        <div class="md-summary">按已立之法逐笔执法 · 管线每班(约30分钟)自动更新 · 卖出端看执行质量，买入端看是否踩线</div>
        <div v-if="discDaily" class="do-verdict" :class="'g-' + discDaily.grade">
          <b>{{ discDaily.grade === 'bad' ? '不合格' : discDaily.grade === 'warn' ? '警示' : '合格' }}</b>
          <span>{{ discDaily.text }}</span>
          <span v-if="discDaily.newN" class="do-new">NEW ×{{ discDaily.newN }}</span>
        </div>
        <div v-for="(o, i) in discOps?.items || []" :key="i" class="do-item" :class="'v-' + o.verdict">
          <span class="do-time">{{ o.time }}</span>
          <span class="do-bs" :class="o.bs === 'B' ? 'b' : 's'">{{ o.bs === 'B' ? '买' : '卖' }}</span>
          <b class="do-name" @click="goStock(o)">{{ o.name }}</b>
          <span class="do-qty">{{ o.qty }}@{{ o.price }}</span>
          <span class="do-badge">{{ o.verdict === 'bad' ? '❌' : o.verdict === 'warn' ? '⚠️' : '✅' }}</span>
          <span class="do-msg">{{ o.msg }}</span>
        </div>
        <div v-if="!discOps?.items?.length" class="disc-empty">最新交易日无操作记录</div>
      </div>

      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">⚖️ 铁律</span>
          <span class="md-group-count">按阶段强调</span>
        </div>
        <div class="disc-rules">
          <div class="dr-item" :class="{ hot: discRule.hot === '只卖不买' }">🚫 退潮/分歧断崖：只卖不买，执行清仓价位单</div>
          <div class="dr-item" :class="{ hot: discCycle?.stage === '高潮' }">🎯 高潮：持仓兑现为主，只做最强主流</div>
          <div class="dr-item">📉 当日收跌股禁买；单票 -3% 当日止损不过夜</div>
          <div class="dr-item">⏱️ 卖出后 30 分钟禁回买（当日黑名单）。回买需双签：①书面新逻辑写进三行卡 ②规则绿灯（试错许可/板块发酵证据）——缺一不可，导师周评复核</div>
          <div class="dr-item">🧱 只许对了加仓，不许错了摊平</div>
          <div class="dr-item">📐 价位单只上移不取消：高开越上沿先卖一半+分时均线清剩余；低开破位无条件走；平开 11:00 前未反抽也走</div>
          <div class="dr-item">📋 盘前 9:15 挂限价单：价位单挂好不撤，决策留在早晨、执行交给交易所（Q11 立法）</div>
        </div>
      </div>

      <div class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">📝 每日三行卡</span>
          <span class="md-group-count">{{ todayKey() }} · 存本机</span>
        </div>
        <div class="disc-diary">
          <div class="dd-checks">
            <label v-for="c in [['read', '盘前五数'], ['verdict', '定性+上限'], ['orders', '价位单挂好'], ['review', '收盘复盘']]" :key="c[0]" class="dd-check">
              <input type="checkbox" v-model="discForm.checks[c[0]]" @change="discSave"><span>{{ c[1] }}</span>
            </label>
          </div>
          <label class="dd-row"><span>① 定性+依据</span><input v-model="discForm.verdict" :placeholder="discSuggest || '如: 退潮延续（涨停41=均值57%·炸板55%）'" @change="discSave"></label>
          <label class="dd-row"><span>② 仓位上限</span><input v-model="discForm.cap" :placeholder="discRule.cap" @change="discSave"></label>
          <label class="dd-row"><span>③ 价位单</span><input v-model="discForm.plan" placeholder="原样执行 / 修改哪条+理由" @change="discSave"></label>
          <div v-if="discLog.length > 1" class="dd-hist">
            <button class="dd-toggle" @click="showDiscHist = !showDiscHist">{{ showDiscHist ? '收起历史' : `历史 ${discLog.length - 1} 天` }}</button>
            <div v-if="showDiscHist" class="dd-hist-list">
              <div v-for="h in discLog.filter(x => x.date !== todayKey())" :key="h.date" class="dd-h">
                <b>{{ h.date.slice(5) }}</b> {{ h.verdict || '—' }} ｜ {{ h.cap || '—' }} ｜ {{ h.plan || '—' }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 盘面动态: 板块标注流 + 亮点播报流 -->
    <template v-else-if="section === 'live'">
      <div v-if="liveAnnotations.length" class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">🗺️ 板块标注</span>
          <span class="md-group-count">实时播报 · 最新在上</span>
        </div>
        <div class="md-timeline">
          <div v-for="a in liveAnnotations" :key="a.time + a.text" class="tl-item" @click="a.bkCode ? goBoard(a) : null">
            <span class="tl-dot" :style="{ background: annColor(a.color) }" title="标注性质: 利多/利空/中性"></span>
            <span class="tl-time">{{ fmtTime(a.time) }}</span>
            <div class="tl-body">
              <span class="tl-text">{{ a.text }}</span>
              <span v-if="a.bkName" class="md-badge-bk">{{ a.bkName }}</span>
              <span v-if="fmtAmt(a.je)" class="md-badge-je">{{ fmtAmt(a.je) }}</span>
              <span v-if="isFinite(a.zdf)" class="tl-chg" :style="{ color: a.zdf >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(a.zdf) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="liveHighlights.length" class="md-group">
        <div class="md-group-head">
          <span class="md-group-tag">📣 盘面亮点</span>
          <span class="md-group-count">{{ liveHighlights.length }}/{{ data.highlights.length }} 条</span>
          <span v-if="liveDataDate" class="md-group-count md-lag">{{ liveDataDate }} 数据</span>
          <button v-if="data.highlights.length > 10" class="md-live-toggle" @click="showAllLive = !showAllLive">{{ showAllLive ? '收起' : '展开全部' }}</button>
        </div>
        <div class="md-timeline">
          <div v-for="(h, i) in liveHighlights" :key="i" class="tl-item" @click="h.bkCode ? goBoard({ bkCode: h.bkCode, bkName: h.bkName }) : null">
            <span class="tl-dot"></span>
            <span class="tl-time">{{ fmtTime(h.time) }}</span>
            <div class="tl-body">
              <span v-if="h.tagName" class="tl-tag">{{ h.tagName }}</span>
              <span class="tl-text">{{ h.detail }}</span>
              <span v-if="h.bkName" class="tl-bk">{{ h.bkName }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="!liveAnnotations.length && !liveHighlights.length" class="md-empty">暂无数据</div>
    </template>

    <!-- 龙虎榜: 全量个股(可按净买/机构/振幅/换手排序) -->
    <template v-else-if="section === 'lhb'">
      <div class="md-summary">龙虎榜 <b class="md-hot">{{ data.list.length }}</b> 家 · {{ data.time }} <span class="md-sum-sub">条形长度=净买入额</span></div>
      <div class="md-lhb-tools">
        <button v-for="o in LHB_SORTS" :key="o.key" class="md-chip" :class="{ on: lhbSort === o.key }" @click="lhbSort = o.key">{{ o.label }}</button>
      </div>
      <div class="md-list">
        <div class="md-list-head">
          <span class="md-row-name">个股</span>
          <span class="md-tags mini" style="width:auto;text-align:left">净买入</span>
          <span class="md-tags mini">涨幅</span>
        </div>
        <div v-for="s in lhbSorted" :key="s.code" class="md-row rank-row" @click="goStock(s)">
          <span class="md-row-name stacked">
            <span class="md-lhb-name-line">{{ s.name }}<span v-if="s.joinNum" class="md-badge-jg" title="有机构席位">机构×{{ s.joinNum }}</span><span v-if="s.buyIn >= 3e8" class="md-badge-key" title="净买≥3亿">重点</span></span>
            <span class="md-lhb-sub">振幅 {{ fmt(s.amplitude) }}% · 换手 {{ fmt(s.turnoverRatio) }}%</span>
          </span>
          <div class="md-bar-wrap"><div class="md-bar" :style="{ width: lhbBarW(s.buyIn), background: s.buyIn >= 0 ? '#e74c3c' : '#27ae60' }"></div></div>
          <span class="md-tags mini" :style="{ color: s.buyIn >= 0 ? '#c0392b' : '#27ae60' }">{{ fmtYi(s.buyIn) }}</span>
          <span class="md-tags mini" :style="{ color: up(s.chgPct) ? '#e74c3c' : '#27ae60' }">{{ pct(s.chgPct) }}</span>
        </div>
        <div v-if="!lhbSorted.length" class="md-empty">暂无数据</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.md-page { padding: 4px 14px 12px; }
.md-dayline { font-size: 11px; color: #999; margin: 0 0 10px; }

.md-summary { font-size: 13px; color: #333; margin: 4px 0 10px; }
.md-hot { color: #c0392b; font-weight: 600; }

/* 紧凑行列表(风口/新高/机构) — 替代卡片, 提高信息密度 */
.md-list { border: 1px solid #eceff3; border-radius: 10px; overflow: hidden; background: #fff; }
/* 列表表头: 灰底小字, 列宽与数据行对齐; 悬停显示字段含义(tooltip 由 title 提供) */
.md-list-head { display: flex; align-items: center; gap: 8px; padding: 7px 12px; font-size: 11px; color: #8e8e9a; background: #f6f8fa; border-bottom: 1px solid #eceff3; }
.md-row { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.md-row:last-child { border-bottom: none; }
.md-row:active { background: #f7f9fc; }
.md-row-name { flex: 1; min-width: 0; font-size: 13px; color: #333; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md-row-str { font-size: 12px; font-weight: 600; flex: none; }
.md-row-chg { font-size: 12px; width: 64px; text-align: right; flex: none; }
.md-tags { font-size: 11px; color: #999; flex: none; max-width: 42%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md-empty { padding: 16px 0; text-align: center; color: #999; font-size: 12px; }

/* 含北向 switch 开关 */
.md-switch-row { display: flex; align-items: center; gap: 8px; margin: 0 0 10px; }
.md-switch-label { font-size: 13px; font-weight: 600; color: #333; }
.md-switch { position: relative; display: inline-block; width: 38px; height: 22px; cursor: pointer; }
.md-switch input { opacity: 0; width: 0; height: 0; }
.md-switch-track { position: absolute; inset: 0; background: #d5dbe3; border-radius: 22px; transition: background .2s; }
.md-switch-knob { position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.25); transition: left .2s; }
.md-switch input:checked + .md-switch-track { background: #2980b9; }
.md-switch input:checked + .md-switch-track .md-switch-knob { left: 18px; }
.md-switch-text { font-size: 11px; color: #999; }

/* 分组(天梯/原因/外围) */
.md-group { margin-bottom: 12px; }
.md-group-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.md-group-tag { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 16px; background: #f0f2f5; color: #666; }
.md-group-count { font-size: 11px; color: #999; }
.md-group-count.md-lag { color: #e67e22; }   /* 数据滞后提示(非当天数据) */
.md-group-body { background: #fff; border: 1px solid #eceff3; border-radius: 10px; overflow: hidden; }
.md-stock-row { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.md-stock-row:last-child { border-bottom: none; }
.md-stock-row:active { background: #f7f9fc; }
.md-stock-name { flex: 1; font-size: 13px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md-stock-bk { font-size: 11px; color: #999; flex: none; }
.md-stock-val { font-size: 12px; color: #333; width: 64px; text-align: right; flex: none; }
.md-stock-chg { font-size: 12px; width: 64px; text-align: right; flex: none; }
.md-stock-go { color: #ccc; font-size: 14px; flex: none; }
.md-stock-line1 { display: flex; align-items: center; gap: 8px; }
.md-stock-main { flex: 1; min-width: 0; }
.md-level { font-size: 10px; color: #c0392b; background: #fdecea; padding: 1px 6px; border-radius: 10px; flex: none; }
.md-stock-toggle { font-size: 11px; color: #2980b9; flex: none; }
.md-reason { font-size: 12px; color: #555; line-height: 1.7; margin: 6px 0 2px; word-break: break-word; }

/* 趋势折线 */
.md-svg-block { background: #fff; border: 1px solid #eceff3; border-radius: 10px; padding: 10px 12px; margin-bottom: 10px; }
.md-svg { width: 100%; height: 120px; display: block; }
.md-svg-scale { display: flex; justify-content: space-between; font-size: 11px; color: #999; margin-top: 4px; }
.md-subtabs { margin: 0 0 10px; }

/* 盘面动态: 板块/标签徽标 */
.md-badge-bk { font-size: 10px; color: #2980b9; background: #eaf6fb; padding: 1px 6px; border-radius: 10px; flex: none; }
.md-badge-je { font-size: 10px; color: #666; background: #f0f2f5; padding: 1px 6px; border-radius: 10px; flex: none; font-variant-numeric: tabular-nums; }
/* 龙虎榜: 排序切换 + 机构徽标 + 振幅/换手副行 */
.md-lhb-tools { display: flex; gap: 6px; margin: 0 0 8px; }
.md-chip { font-size: 11px; padding: 3px 12px; border-radius: 14px; border: 1px solid #e0e4ea; background: #fff; color: #666; cursor: pointer; }
.md-chip.on { background: #2980b9; border-color: #2980b9; color: #fff; font-weight: 600; }
.md-row-name.stacked { display: flex; flex-direction: column; align-items: stretch; white-space: normal; overflow: visible; }
.md-lhb-name-line { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.md-lhb-sub { font-size: 10px; color: #999; font-weight: 400; margin-top: 1px; font-variant-numeric: tabular-nums; }

/* 龙虎榜: 机构徽标 + 窄列(5列适配) + 折叠按钮 + 情绪对比 */
.md-badge-jg { font-size: 9px; color: #c0392b; background: #fdecea; padding: 0 4px; border-radius: 8px; margin-left: 4px; vertical-align: 1px; }
.md-tags.mini, .md-row-str.mini { max-width: none; width: 56px; text-align: right; }
.md-live-toggle { font-size: 11px; color: #2980b9; background: none; border: none; cursor: pointer; padding: 0; }
.md-delta { font-size: 12px; font-weight: 600; margin-left: 2px; }

/* 情绪仪表盘 */
.md-gauge-block { background: #fff; border: 1px solid #eceff3; border-radius: 10px; padding: 6px 12px 4px; margin-bottom: 10px; }
.md-gauge { width: 100%; height: 128px; display: block; }

/* 情绪热力色带 */
.md-heat { background: #fff; border: 1px solid #eceff3; border-radius: 10px; padding: 10px 12px 6px; }
.md-heat-chart { width: 100%; height: auto; display: block; }
.md-heat-empty { font-size: 12px; color: #999; text-align: center; padding: 14px 0; }
.md-heat-scale { display: flex; justify-content: space-between; font-size: 11px; color: #999; margin-top: 4px; }
.md-heat-legend { display: flex; align-items: center; gap: 2px; margin-top: 6px; }
.md-heat-lg { width: 14px; height: 8px; border-radius: 2px; }
.md-heat-lg-txt { font-size: 10px; color: #999; margin-left: 6px; }

/* 赚钱效应分布条 */
.md-effect { background: #fff; border: 1px solid #eceff3; border-radius: 10px; padding: 12px; }
.me-bar { display: flex; gap: 2px; height: 16px; margin-bottom: 6px; }
.me-half { display: flex; flex: 1; gap: 2px; }
.me-seg { height: 100%; min-width: 2px; border-radius: 2px; }
.me-seg.loss { background: #27ae60; }
.me-seg.gain { background: #e74c3c; }
.me-mid { width: 2px; background: #d5dbe3; margin: 0 3px; border-radius: 1px; }
.me-legend { display: flex; justify-content: space-between; font-size: 11px; color: #999; }
.me-ttag { color: #c0392b; font-weight: 600; }
.me-scale { display: flex; justify-content: space-between; font-size: 10px; color: #bbb; margin-top: 3px; }

/* 时间线(盘面动态) */
.md-timeline { margin-left: 6px; padding-left: 18px; border-left: 2px solid #f0f0f0; }
.tl-item { position: relative; display: flex; align-items: baseline; gap: 8px; padding: 7px 0; cursor: pointer; }
.tl-item:active { background: #f7f9fc; border-radius: 6px; }
.tl-dot { position: absolute; left: -22.5px; top: 13px; width: 9px; height: 9px; border-radius: 50%; background: #2980b9; border: 2px solid #fff; box-shadow: 0 0 0 1px #2980b9; }
.tl-time { font-size: 10px; color: #999; flex: none; width: 34px; text-align: right; }
.tl-body { display: inline-flex; align-items: center; gap: 6px; flex: 1; min-width: 0; }
.tl-text { font-size: 13px; color: #333; line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-chg { font-size: 12px; flex: none; }
.tl-tag { font-size: 10px; color: #c0392b; background: #fdecea; padding: 1px 6px; border-radius: 10px; flex: none; }
.tl-bk { font-size: 10px; color: #999; flex: none; }

/* 龙虎榜: 净买条形 + 重点标记 */
.rank-row .md-row-name { flex: none; width: 96px; }
.md-bar-wrap { flex: 1; min-width: 40px; height: 8px; background: #f5f5f5; border-radius: 4px; overflow: hidden; }
.md-bar { height: 100%; border-radius: 4px; }
.md-badge-key { font-size: 9px; color: #fff; background: #c0392b; padding: 0 4px; border-radius: 8px; margin-left: 4px; }
.md-sum-sub { font-size: 11px; color: #999; font-weight: 400; margin-left: 6px; }

/* ── 我的纪律卡 ── */
.disc-focus { padding: 10px 14px; font-weight: 700; color: #5b6daa; background: #f0f3fa; border-radius: 10px; }
.disc-verdict { border-left: 4px solid var(--sc, #8a97a8); padding: 4px 0 4px 12px; }
.dv-top { display: flex; align-items: baseline; gap: 10px; }
.dv-stage { font-size: 26px; font-weight: 800; color: var(--sc, #8a97a8); }
.dv-cap { font-size: 16px; font-weight: 800; color: #fff; background: #5b6daa; border-radius: 6px; padding: 2px 10px; }
.dv-hot { font-size: 13px; color: #e67e22; font-weight: 700; }
.dv-act { margin: 6px 0 2px; font-size: 14px; color: #333; font-weight: 600; }
.dv-reason { margin: 2px 0; font-size: 12px; color: #8a97a8; }
.dv-lamps { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.dv-lamp { font-size: 12px; color: #8a97a8; background: #f0f1f4; border-radius: 14px; padding: 4px 10px; }
.dv-lamp.on { color: #fff; background: #e67e22; font-weight: 700; }
.disc-five { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.df-item { text-align: center; background: #f7f8fa; border-radius: 8px; padding: 8px 2px; }
.df-k { display: block; font-size: 11px; color: #999; }
.df-v { display: block; font-size: 15px; margin: 2px 0; }
.df-sub { display: block; font-size: 10px; color: #b0b6bd; }
.disc-menu { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px; }
.dm-item { font-size: 12px; color: #666; background: #f7f8fa; border-radius: 8px; padding: 6px 8px; }
.dm-item i { font-style: normal; font-weight: 800; color: #b0b6bd; margin-right: 4px; }
.dm-item b { font-weight: 600; color: #333; }
.dm-item.ok { background: #fdf3e7; }
.dm-item.ok i { color: #e67e22; }
.disc-pos { border-top: 1px solid #f0f1f4; padding: 9px 2px; }
.disc-pos:first-of-type { border-top: none; }
.disc-pos.is-touch { background: #fdf6ec; border-radius: 8px; padding: 9px 8px; }
.disc-pos.is-gone { opacity: .5; }
.dp-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.dp-name { font-size: 15px; color: #333; cursor: pointer; }
.dp-weight { font-size: 12px; color: #5b6daa; font-weight: 700; }
.dp-price, .dp-profit { font-size: 13px; color: #333; }
.dp-cost { font-style: normal; font-size: 11px; color: #b0b6bd; margin-left: 4px; }
.dp-touch { font-size: 12px; font-weight: 800; color: #fff; background: #e67e22; border-radius: 6px; padding: 2px 8px; }
.dp-badge { font-size: 11px; color: #8a97a8; background: #f0f1f4; border-radius: 6px; padding: 2px 8px; }
.dp-badge.warn { color: #e67e22; background: #fdf3e7; }
.dp-badge.keep { color: #fff; background: #27ae60; }
.dp-levels { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; align-items: center; }
.dp-lv { font-size: 12px; color: #5b6daa; background: #f0f3fa; border-radius: 6px; padding: 2px 8px; }
.dp-board { font-size: 11px; border-radius: 6px; padding: 2px 8px; }
.vb-main { color: #fff; background: #e74c3c; font-weight: 700; }
.vb-sub { color: #fff; background: #f39c12; }
.vb-watch { color: #e67e22; background: #fdf3e7; }
.vb-dead { color: #8a97a8; background: #f0f1f4; }
.dp-note { font-size: 12px; color: #8a97a8; margin-top: 4px; }
.disc-asof, .disc-empty { font-size: 11px; color: #b0b6bd; margin-top: 8px; }
.disc-empty { color: #e67e22; }
.disc-rules { display: flex; flex-direction: column; gap: 6px; }
.dr-item { font-size: 13px; color: #666; background: #f7f8fa; border-radius: 8px; padding: 8px 10px; }
.dr-item.hot { color: #fff; background: #e67e22; font-weight: 700; }
.dd-checks { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.dd-check { font-size: 13px; color: #333; display: flex; align-items: center; gap: 4px; cursor: pointer; }
.dd-check input { accent-color: #5b6daa; }
.dd-row { display: flex; align-items: center; gap: 8px; margin: 6px 0; font-size: 13px; color: #666; }
.dd-row span { flex: 0 0 76px; }
.dd-row input { flex: 1; min-width: 0; border: 1px solid #e3e7ec; border-radius: 8px; padding: 8px 10px; font-size: 13px; }
.dd-toggle { border: none; background: #f0f3fa; color: #5b6daa; font-size: 12px; border-radius: 8px; padding: 5px 10px; }
.dd-hist-list { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.dd-h { font-size: 12px; color: #666; background: #f7f8fa; border-radius: 6px; padding: 5px 8px; }
.dd-h b { color: #5b6daa; margin-right: 6px; }

/* ── 操作点评 ── */
.do-item { display: flex; align-items: baseline; gap: 6px; padding: 6px 8px; border-top: 1px solid #f0f1f4; font-size: 12px; }
.do-item:first-of-type { border-top: none; }
.do-item.v-bad { background: #fdf0ef; border-radius: 6px; }
.do-item.v-warn { background: #fdf6ec; border-radius: 6px; }
.do-time { color: #b0b6bd; font-size: 11px; flex: none; }
.do-bs { flex: none; font-weight: 800; }
.do-bs.b { color: #e74c3c; }
.do-bs.s { color: #27ae60; }
.do-name { color: #333; cursor: pointer; flex: none; }
.do-qty { color: #666; flex: none; }
.do-badge { flex: none; }
.do-msg { color: #666; min-width: 0; }
.dp-anchor { font-size: 11px; color: #b0b6bd; }
.dp-shi { font-size: 11px; font-weight: 700; color: #8a97a8; background: #f0f1f4; border-radius: 6px; padding: 1px 7px; }
.dp-shi.up { color: #e74c3c; background: #fdecea; }
.dp-shi.down { color: #27ae60; background: #eef9f1; }
.dp-hot { font-size: 11px; color: #e67e22; font-weight: 700; }
.dp-hot i { font-style: normal; font-size: 10px; margin-left: 2px; }
.dp-hot i.up { color: #e74c3c; }
.dp-hot i.down { color: #27ae60; }
.bp-rbr { margin: 6px 0 2px; font-size: 13px; color: #333; }
.rbr-stars { color: #e67e22; font-weight: 800; letter-spacing: 2px; margin: 0 6px; }
.rbr-txt { font-size: 12px; color: #8a97a8; }
.yj-cand { border-top: 1px solid #f0f1f4; padding: 8px 2px; }
.yj-cand:first-of-type { border-top: none; }
.yc-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.yc-level { font-size: 12px; font-weight: 700; color: #5b6daa; }
.yc-score { font-size: 12px; color: #e67e22; font-weight: 700; }
.yc-mode { font-size: 11px; color: #8a97a8; background: #f0f1f4; border-radius: 6px; padding: 1px 7px; }
.yc-status { font-size: 11px; font-weight: 700; color: #8a97a8; background: #f0f1f4; border-radius: 6px; padding: 1px 7px; }
.yc-status.go { color: #fff; background: #e74c3c; }
.yc-tip { font-size: 12px; color: #5b6daa; margin-top: 4px; }
.dv-yj { margin-top: 8px; font-size: 12px; color: #8a6d3b; background: #faf6ec; border-radius: 8px; padding: 6px 10px; }
.do-verdict { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; border-radius: 8px; padding: 9px 10px; margin: 8px 0 4px; font-size: 13px; }
.do-verdict b { font-size: 15px; }
.do-verdict.g-bad { background: #fdf0ef; color: #c0392b; }
.do-verdict.g-bad b { color: #c0392b; }
.do-verdict.g-warn { background: #fdf6ec; color: #e67e22; }
.do-verdict.g-ok { background: #eef9f1; color: #27ae60; }
.do-new { font-size: 11px; font-weight: 800; color: #fff; background: #e67e22; border-radius: 6px; padding: 1px 7px; }

/* ── 今日作战指引 ── */
.bp-top { border-left: 4px solid #e67e22; padding-left: 12px; }
.bp-line { margin: 6px 0 2px; font-size: 14px; font-weight: 700; color: #333; }
.bp-act { margin: 2px 0; font-size: 12px; color: #8a97a8; }
.bp-lamps { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
.bp-orders-head { font-size: 12px; font-weight: 700; color: #5b6daa; margin: 8px 0 4px; }
.bp-order { display: flex; gap: 8px; align-items: baseline; font-size: 13px; padding: 4px 0; border-bottom: 1px dashed #f0f1f4; cursor: pointer; }
.bp-order b { flex: none; color: #333; min-width: 62px; }
.bp-order span { color: #666; }
.bp-watch-row { display: flex; gap: 6px; flex-wrap: wrap; }

@media (max-width: 480px) {
  .md-page { padding-left: 10px; padding-right: 10px; }
}
@media (min-width: 768px) {
  .md-page { padding: 8px 28px 20px; }
}

/* 情绪周期 /market/cycle */
.md-cycle { margin-top: 4px; }
.cy-hero { background: #fff; border: 2px solid #e5e9f0; border-radius: 12px; padding: 12px 14px; margin-bottom: 10px; }
.cy-stage { font-size: 34px; font-weight: 800; letter-spacing: 4px; line-height: 1.2; }
.cy-conf { font-size: 11px; color: #999; margin: 2px 0 6px; }
.cy-reasons { margin: 0 0 8px; padding-left: 16px; }
.cy-reasons li { font-size: 12px; color: #555; line-height: 1.7; }
.cy-playbook { font-size: 12px; color: #b8860b; background: #fdf6e3; border-radius: 6px; padding: 6px 8px; line-height: 1.6; }
.cy-scale { display: flex; gap: 4px; margin: 10px 0; }
.cy-seg { flex: 1; text-align: center; font-size: 11px; padding: 5px 0; border: 1px solid #e5e9f0; border-radius: 6px; color: #99a; background: #f7f8fa; }
.cy-seg.on { color: #fff; font-weight: 700; }
.cy-metrics { display: flex; gap: 8px; margin: 10px 0; }
.cy-mi { flex: 1; background: #fff; border: 1px solid #e5e9f0; border-radius: 10px; padding: 8px 6px; text-align: center; }
.cy-mi-v { display: block; font-size: 18px; font-weight: 800; color: #2c3e50; }
.cy-mi-l { display: block; font-size: 10px; color: #999; margin-top: 2px; }
.cy-promo { display: flex; flex-direction: column; gap: 6px; }
.cy-pi { display: flex; justify-content: space-between; align-items: center; font-size: 13px; background: #f7f8fa; border-radius: 8px; padding: 7px 10px; }
.cy-pi b { color: #2c3e50; }
.cy-matrix { display: grid; grid-template-columns: 64px repeat(3, 1fr); gap: 4px; }
.cy-cell { background: #f7f8fa; border-radius: 8px; padding: 6px 6px; font-size: 11px; color: #666; display: flex; flex-direction: column; gap: 2px; min-height: 44px; }
.cy-cell.head { background: transparent; font-weight: 700; color: #2c3e50; justify-content: center; min-height: 26px; }
.cy-cell.on { background: #2c3e50; color: #fff; }
.cy-cell.on .cy-cell-d { color: #dfe6ee; }
.cy-cell-t { font-weight: 700; }
.cy-cell-d { font-size: 10px; line-height: 1.4; color: #888; }
.cy-cell.on .cy-cell-d { color: #dfe6ee; }
.cy-line { font-size: 13px; line-height: 1.7; padding: 5px 0; border-bottom: 1px dashed #eef1f5; }
.cy-line b { color: #2c3e50; }
.cy-names { color: #999; font-size: 11px; display: block; }
.cy-role { color: #b8860b; font-size: 11px; }
/* cycle 页重排: 决策区 + 分析Tab */
.cy-hero-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.cy-gate { margin-left: auto; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; background: #ff5a5a; color: #fff; }
.cy-gate.ban { background: #9b6bde; }
.lb-toggle { text-align: center; font-size: 12px; color: #2a7fd4; padding: 6px 0 2px; cursor: pointer; }
.cy-tabs { margin: 10px 0 2px; }
/* Tab 内容卡片化: 行内容不再浮在页面渐变底上 */
.cy-pane .md-group { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 8px 12px 6px; box-shadow: 0 1px 2px rgba(17,24,39,.04), 0 4px 14px rgba(17,24,39,.05); }
.cy-pane .md-group-head { margin: 4px 0 2px; }
.cy-pane .cy-line:last-child, .cy-pane .lb-rel:last-child { border-bottom: none; }
.cy-pane { min-height: 120px; }
.mt-tabs { display: flex; gap: 6px; overflow-x: auto; padding: 2px 0 8px; }
.mt-tab { flex-shrink: 0; border: 1px solid #e5e9f0; background: #fff; border-radius: 14px; padding: 5px 14px; font-size: 13px; color: #667; }
.mt-tab.on { background: #2c3e50; border-color: #2c3e50; color: #fff; font-weight: 700; }
/* 龙头博弈 + 今日出击 /market/cycle */
.lb-banner { border: 1px solid #e5e9f0; border-radius: 8px; padding: 8px 10px; font-size: 12px; color: #556; line-height: 1.6; background: #f8fafc; margin-bottom: 8px; }
.lb-mtx { margin-top: 5px; padding-top: 6px; border-top: 1px dashed #dfe6ee; color: #2b6cb0; }
.lb-banned { color: #ff5a5a; font-weight: 700; }
.lb-cand { border: 1px solid #e9edf3; border-left: 3px solid #cfd8e3; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer; background: #fbfcfe; }
.lb-cand.st-go { border-left-color: #ff5a5a; background: #fff5f5; }
.lb-cand.st-alt { border-left-color: #f5a623; background: #fffbf0; }
.lb-cand.st-watch { border-left-color: #cfd8e3; }
.lb-cand-top { display: flex; align-items: center; gap: 6px; }
.lb-cand-top b { font-size: 14px; }
.lb-lv { font-size: 11px; color: #b8860b; font-weight: 700; }
.lb-score { margin-left: auto; font-size: 18px; font-weight: 800; color: #8a97a8; }
.lb-score.hi { color: #ff5a5a; }
.lb-status { font-size: 11px; padding: 1px 6px; border-radius: 4px; background: #eef1f5; color: #667; }
.st-go .lb-status { background: #ff5a5a; color: #fff; }
.st-alt .lb-status { background: #f5a623; color: #fff; }
.lb-cand-mid { display: flex; gap: 8px; font-size: 11px; color: #778; margin: 3px 0; align-items: center; flex-wrap: wrap; }
.lb-role { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 9px; background: #fdf3e7; color: #b06020; flex: none; }
.lb-role.feng { background: #f0f0f2; color: #999; }
.lb-role.huo { background: #fdeaea; color: #c04848; }
.lb-mode { color: #2bc4a8; font-weight: 700; }
.lb-cand-logic { font-size: 12px; color: #556; }
.lb-cand-str { font-size: 11px; color: #4cd964; margin-top: 2px; }
.lb-cand-risk { font-size: 11px; color: #ff5a5a; margin-top: 2px; }
.lb-note { font-size: 10px; color: #a0aab8; margin-top: 4px; }
.lb-empty { font-size: 12px; color: #999; padding: 6px 0; }
.lb-tag { display: inline-block; font-size: 10px; padding: 0 5px; border-radius: 4px; margin-right: 4px; vertical-align: 1px; }
.lb-tag.hot { background: #ff5a5a; color: #fff; }
.lb-tag.rise { background: #e6f7ef; color: #1a9e6e; }
.lb-tag.up { background: #e8f4ff; color: #2a7fd4; }
.lb-tag.fade { background: #fdeeee; color: #d4574f; }
.lb-tag.plain { background: #eef1f5; color: #667; }
.lb-delta { font-size: 11px; font-weight: 700; }
.lb-delta.up { color: #ff5a5a; }
.lb-delta.dn { color: #4cd964; }
.lb-rel { font-size: 13px; padding: 4px 0; border-bottom: 1px dashed #eef1f5; }
.lb-rel b { color: #2c3e50; }
.lb-switch { color: #f5a623; font-weight: 700; }
.lb-link { cursor: pointer; }
.lb-tip { display: block; font-size: 11px; color: #f5a623; margin-top: 1px; }
</style>
