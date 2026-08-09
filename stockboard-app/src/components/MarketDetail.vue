<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AuctionTab from './AuctionTab.vue'
import { usePullRefresh } from '../composables/usePullRefresh.js'
import {
  fetchFengKou, fetchTianTi, fetchMarketLimitReasons,
  fetchNewHighBoards, fetchNewHighStocks, fetchNewHighTrend,
  fetchGlobalIndexes, fetchInstitutionIncrease,
  getLatestTradingDay, getLatestReportDate, isTradingTime,
} from '../composables/useKplApi.js'

defineOptions({ name: 'MarketDetail' })

const route = useRoute()
const router = useRouter()
const section = computed(() => route.params.section)

const SECTION_TITLES = {
  auction: '竞价抢筹', wind: '最强风口', ladder: '涨停天梯', reasons: '涨停原因',
  newhighs: '百日新高', global: '外围市场', institution: '机构增仓',
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

async function load(silent = false) {
  const s = section.value
  if (s === 'auction') { loading.value = false; return }
  loading.value = true
  if (!silent) error.value = false
  try {
    const day = await getLatestTradingDay()
    dayLabel.value = day || ''
    const dayDash = day ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : ''
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
    if (res) data.value = res
    else if (!silent) error.value = true
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
function pct(v) { return typeof v === 'number' ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }
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

@media (max-width: 480px) {
  .md-page { padding-left: 10px; padding-right: 10px; }
}
@media (min-width: 768px) {
  .md-page { padding: 8px 28px 20px; }
}
</style>
