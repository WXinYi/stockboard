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
import { loadCycleData, STAGES, STAGE_COLORS } from '../utils/emotionCycle.js'
import { loadBattleData } from '../utils/leaderBattle.js'

defineOptions({ name: 'MarketDetail' })

const route = useRoute()
const router = useRouter()
const section = computed(() => route.params.section)

const SECTION_TITLES = {
  auction: '竞价抢筹', wind: '最强风口', ladder: '涨停天梯', reasons: '涨停原因',
  newhighs: '百日新高', global: '外围市场', institution: '机构增仓',
  mood: '市场情绪', live: '盘面动态', lhb: '龙虎榜', cycle: '情绪周期',
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
      res = { cycle: cd.cycle, battle: await loadBattleData({ fetchLimitPool, fetchUnsealedPool }, cd) }
    }
    else if (s === 'live') res = {
      highlights: await fetchMarketHighlights(silent),
      annotations: await fetchBoardAnnotations(silent),
    }
    else if (s === 'lhb') res = await fetchLhbList(silent)
    if (res) data.value = res
    else if (!silent) error.value = true
    if (s === 'cycle' && res) cycleFetchedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
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
        <!-- 阶段大字卡 -->
        <div class="cy-hero" :style="{ borderColor: cyColor }">
          <div class="cy-stage" :style="{ color: cyColor }">{{ cycle.stage }}</div>
          <div class="cy-conf">置信度 {{ cycle.confidence }}/9 · 数据日 {{ cycle.date }}</div>
          <ul class="cy-reasons"><li v-for="r in cycle.reasons" :key="r">{{ r }}</li></ul>
          <div class="cy-playbook">📌 {{ cycle.playbook }}</div>
        </div>

        <!-- 六段刻度条 -->
        <div class="cy-scale">
          <div v-for="s in cycleStages" :key="s" class="cy-seg" :class="{ on: s === cycle.stage }"
               :style="s === cycle.stage ? { background: cyColor, borderColor: cyColor } : {}">{{ s }}</div>
        </div>

        <!-- 🎯 今日出击: 阶段闸门 + 确定性评分候选 -->
        <div v-if="battle && !battle.empty" class="md-group lb-strike">
          <div class="md-group-head">
            <span class="md-group-tag">🎯 今日出击</span>
            <span class="md-group-count" :class="{ 'lb-banned': battle.strike.gate.cap === 0 }">
              {{ battle.strike.gate.stage }}闸门 · 上限 {{ battle.strike.gate.cap || '禁买' }}
            </span>
          </div>
          <div class="lb-banner" :style="battle.strike.gate.cap === 0 ? {} : { borderColor: cyColor }">
            {{ battle.strike.gate.banner }}
          </div>
          <div v-for="c in battle.strike.candidates" :key="c.code" class="lb-cand" :class="'st-' + (c.status.startsWith('出击') ? 'go' : c.status.startsWith('备选') ? 'alt' : 'watch')" @click="goStock(c)">
            <div class="lb-cand-top">
              <b>{{ c.name }}</b>
              <span v-if="c.level" class="lb-lv">{{ c.level }}板</span>
              <span class="lb-score" :class="{ hi: c.score >= 75 }">{{ c.score }}</span>
              <span class="lb-status">{{ c.status }}</span>
            </div>
            <div class="lb-cand-mid">
              <span class="lb-mode">{{ c.mode }}</span>
              <span class="lb-plates">{{ c.platesTxt }}</span>
              <span class="lb-seal">{{ c.sealTxt }}</span>
            </div>
            <div class="lb-cand-logic">{{ c.logic }}</div>
            <div v-if="c.strength" class="lb-cand-str">💪 {{ c.strength }}</div>
            <div v-if="c.risk" class="lb-cand-risk">⚠️ {{ c.risk }}</div>
          </div>
          <div v-if="!battle.strike.candidates.length" class="lb-empty">当前阶段无符合条件的候选（纪律优先）</div>
          <div class="lb-note">{{ battle.strike.disclaimer }}</div>
        </div>

        <!-- ⚔️ 板块之争 -->
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

        <!-- 🥊 高标对决 -->
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

        <!-- 🚨 高标开板风险 -->
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

        <!-- 🔭 明日卡位雷达 -->
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

        <!-- 指标网格 -->
        <div class="cy-metrics">
          <div class="cy-mi"><span class="cy-mi-v">{{ cycle.metrics.height }}B</span><span class="cy-mi-l">最高连板(昨 {{ cycle.metrics.heightPrev ?? '-' }})</span></div>
          <div class="cy-mi"><span class="cy-mi-v">{{ cycle.metrics.zt }}</span><span class="cy-mi-l">涨停(ma5 {{ Math.round(cycle.metrics.ztMa5 || 0) }})</span></div>
          <div class="cy-mi"><span class="cy-mi-v">{{ cycle.metrics.brokeRate }}%</span><span class="cy-mi-l">破板率</span></div>
        </div>

        <!-- 梯队晋级 -->
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

        <!-- 3x3 矩阵 -->
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

        <!-- 主线板块 -->
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

        <!-- 龙头谱系 -->
        <div class="md-group">
          <div class="md-group-head">
            <span class="md-group-tag">👑 龙头谱系</span>
          </div>
          <div v-for="l in cycle.leaders" :key="l.code + l.role" class="cy-line">
            <b>{{ l.name }}</b> {{ l.pid }}板 <span class="cy-role">[{{ l.role }}]</span>
            <span class="cy-names">{{ l.note }}</span>
          </div>
        </div>

        <div class="md-summary">⚡ 打开页面时经 KPL 实时数据计算 · {{ cycleFetchedAt || '计算中' }} · 交易时段 30s 自动刷新</div>
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
/* 龙头博弈 + 今日出击 /market/cycle */
.lb-banner { border: 1px dashed #e5e9f0; border-radius: 8px; padding: 8px 10px; font-size: 12px; color: #556; background: #f8fafc; margin-bottom: 8px; }
.lb-banned { color: #ff5a5a; font-weight: 700; }
.lb-cand { border: 1px solid #eef1f5; border-left: 3px solid #cfd8e3; border-radius: 8px; padding: 8px 10px; margin-bottom: 6px; cursor: pointer; }
.lb-cand.st-go { border-left-color: #ff5a5a; background: #fff7f7; }
.lb-cand.st-alt { border-left-color: #f5a623; }
.lb-cand.st-watch { border-left-color: #cfd8e3; }
.lb-cand-top { display: flex; align-items: center; gap: 6px; }
.lb-cand-top b { font-size: 14px; }
.lb-lv { font-size: 11px; color: #b8860b; font-weight: 700; }
.lb-score { margin-left: auto; font-size: 18px; font-weight: 800; color: #8a97a8; }
.lb-score.hi { color: #ff5a5a; }
.lb-status { font-size: 11px; padding: 1px 6px; border-radius: 4px; background: #eef1f5; color: #667; }
.st-go .lb-status { background: #ff5a5a; color: #fff; }
.st-alt .lb-status { background: #f5a623; color: #fff; }
.lb-cand-mid { display: flex; gap: 8px; font-size: 11px; color: #778; margin: 3px 0; }
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
