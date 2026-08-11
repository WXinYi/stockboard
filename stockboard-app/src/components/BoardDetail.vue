<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchBoardConstituents, fetchNewHighBoards, getLatestTradingDay, isTradingTime } from '../composables/useKplApi.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'

defineOptions({ name: 'BoardDetail' })

const route = useRoute()
const router = useRouter()
const bkCode = computed(() => route.params.bk_code)
const bkName = computed(() => route.query.name || bkCode.value)
// 数据口径: 'bid' 完整成分(ZhiShuStockList_W8) / 'nh' 百日新高股(fetchNewHighBoards 板块 List)
const src = computed(() => (route.query.src === 'nh' ? 'nh' : 'bid'))

const rows = ref([])
const loading = ref(true)
const error = ref(false)
const day = ref('')

async function load(silent = false) {
  try {
    let list = null
    if (src.value === 'nh') {
      // 新高口径: 一次拉全板块新高分组, 按 bkCode 过滤出本板块新高股
      const boards = await fetchNewHighBoards(silent)
      list = (boards || []).find(b => b.bkCode === bkCode.value)?.stocks || []
    } else {
      if (!day.value) day.value = await getLatestTradingDay()
      list = await fetchBoardConstituents(bkCode.value, day.value, silent)
    }
    if (list) rows.value = list
    if (!silent) error.value = false
  } catch (e) {
    if (!silent) error.value = true
  } finally {
    loading.value = false
  }
}

// KeepAlive 复用: 板块 A → 板块 B 直接跳转时清空旧数据重载
watch(bkCode, () => {
  // 路由离开板块页(bkCode 变 undefined)时不重载, 避免无效请求
  if (!bkCode.value) return
  rows.value = []
  loading.value = true
  error.value = false
  load()
})

// 下拉刷新: 仅当前激活页面响应(usePullRefresh 按激活态过滤)
usePullRefresh(() => { load(true) })

// 15s 轮询(交易时段)
let timer = null
function startTimer() {
  if (timer || !isTradingTime()) return
  timer = setInterval(() => {
    if (!isTradingTime()) { stopTimer(); return }
    load(true)
  }, 15000)
}
function stopTimer() { clearInterval(timer); timer = null }
function onVisibility() {
  if (document.hidden) { stopTimer(); return }
  if (!isTradingTime()) return
  startTimer(); load(true)
}

// KeepAlive 组件首次挂载时 onMounted+onActivated 双触发 → 加载只在 onActivated 做(inited 分支)
let inited = false
onMounted(() => {
  document.addEventListener('visibilitychange', onVisibility)
})
onActivated(() => {
  if (!inited) { inited = true; load() } else if (isTradingTime()) load(true)
  startTimer()
})
onDeactivated(() => { stopTimer() })
onUnmounted(() => {
  stopTimer()
  document.removeEventListener('visibilitychange', onVisibility)
})

function goStock(row) { router.push({ path: '/stock/' + row.code, query: { name: row.name } }) }
function goBack() { router.back() }
function fmt(v, d = 2) { return (typeof v === 'number' && isFinite(v)) ? v.toFixed(d) : '—' }
function pct(v) { return typeof v === 'number' ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }
const isUp = r => (typeof r.chgPct === 'number' ? r.chgPct >= 0 : false)
const dayStr = computed(() => String(day.value).replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3'))

// ── 大单净额格式化(元 → 亿/万) ──
function fmtNet(v) {
  if (typeof v !== 'number' || !isFinite(v)) return '—'
  const abs = Math.abs(v)
  const s = v >= 0 ? '+' : '-'
  if (abs >= 1e8) return s + (abs / 1e8).toFixed(2) + '亿'
  if (abs >= 1e4) return s + (abs / 1e4).toFixed(0) + '万'
  return s + abs.toFixed(0)
}

// ── 表头排序: 涨幅(默认) / 大单净额 / 换手率 ──
const sortKey = ref('chgPct')
const sortDir = ref(-1)   // -1 降序
function toggleSort(key) {
  if (sortKey.value === key) sortDir.value = -sortDir.value
  else { sortKey.value = key; sortDir.value = -1 }
}
const SORT_HEADERS = [
  { key: 'chgPct', label: '涨幅' },
  { key: 'bigNet', label: '大单净' },
  { key: 'turnover', label: '换手' },
]
const sortedRows = computed(() => {
  const key = sortKey.value
  const dir = sortDir.value
  return [...rows.value].sort((a, b) => dir * ((b[key] || 0) - (a[key] || 0)))
})
// 换手率迷你条: 宽度按全表最大值归一
const turnoverMax = computed(() => Math.max(...rows.value.map(r => r.turnover || 0), 1))
const turnoverW = r => (Math.max(0, r.turnover || 0) / turnoverMax.value * 100).toFixed(1) + '%'
</script>

<template>
  <div class="bd-page">
    <!-- 顶部导航由 App header 统一提供(返回+标题), 此处信息条含板块代码; bid=竞价异动口径非完整成分 -->
    <div class="bd-bar">⚡ {{ bkName }} {{ bkCode }} · {{ src === 'nh' ? '百日新高' : '完整成分' }}口径{{ src === 'nh' ? '' : ' · ' + dayStr }}</div>

    <div v-if="loading" class="sd-loading">板块成分加载中…</div>
    <div v-else-if="error" class="sd-error">
      加载失败
      <button class="sd-retry" @click="load()">重试</button>
    </div>
    <template v-else>
      <div class="bd-sub">{{ rows.length }} {{ src === 'nh' ? '家新高' : '只成分' }} · {{ src === 'nh' ? '新高口径' : '完整成分(ZhiShuStockList_W8)' }}</div>
      <div class="bd-table">
        <div class="bd-row bd-head">
          <span class="bd-rank">#</span>
          <span class="bd-name">名称</span>
          <span class="bd-price">现价</span>
          <span v-for="h in SORT_HEADERS" :key="h.key" class="bd-sortable" :class="{ on: sortKey === h.key }" @click="toggleSort(h.key)">{{ h.label }}{{ sortKey === h.key ? (sortDir === -1 ? ' ↓' : ' ↑') : '' }}</span>
        </div>
        <div v-for="(r, i) in sortedRows" :key="r.code" class="bd-row" @click="goStock(r)">
          <span class="bd-rank">{{ i + 1 }}</span>
          <span class="bd-name">
            {{ r.name }}
            <span v-if="r.boardLabel" class="bd-lb">{{ r.boardLabel }}</span>
            <span v-if="r.tags" class="bd-tags">{{ r.tags }}</span>
          </span>
          <span class="bd-price">{{ fmt(r.price) }}</span>
          <span class="bd-chg" :style="{ color: isUp(r) ? '#e74c3c' : '#27ae60', fontWeight: (r.chgPct >= 9.8 ? 700 : 400) }">{{ pct(r.chgPct) }}</span>
          <span class="bd-tr">
            <span class="bd-tr-track"><span class="bd-tr-fill" :style="{ width: turnoverW(r) }"></span></span>
            <span class="bd-tr-num">{{ fmt(r.turnover, 1) }}%</span>
          </span>
          <span class="bd-net" :style="{ color: (r.bigNet || 0) >= 0 ? '#c0392b' : '#27ae60' }">{{ fmtNet(r.bigNet) }}</span>
        </div>
        <div v-if="!rows.length" class="sd-error">该板块今日无成分数据(可能停牌或已改代码)</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.bd-page { padding: 4px 14px 12px; }
.bd-bar { font-size: 13px; color: #333; font-weight: 600; margin-bottom: 8px; }
.bd-sub { font-size: 11px; color: #999; margin-bottom: 8px; }
.bd-table { border: 1px solid #eceff3; border-radius: 10px; overflow: hidden; }
.bd-row { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
/* 表头: 灰底小字, 与数据行同列宽布局 */
.bd-row.bd-head { background: #f6f8fa; font-size: 11px; color: #8e8e9a; cursor: default; }
.bd-row:last-child { border-bottom: none; }
.bd-row:active { background: #f7f9fc; }
.bd-rank { width: 22px; color: #999; font-size: 12px; flex: none; }
.bd-name { flex: 1; font-size: 13px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bd-tags { color: #999; font-size: 11px; margin-left: 6px; }
.bd-lb { background: #e74c3c; color: #fff; font-size: 10px; padding: 1px 5px; border-radius: 4px; margin-left: 6px; flex: none; }
.bd-price { font-size: 13px; color: #333; width: 64px; text-align: right; flex: none; }
.bd-chg { font-size: 13px; width: 64px; text-align: right; flex: none; }
.bd-sortable { font-size: 11px; width: 64px; text-align: right; flex: none; cursor: pointer; }
.bd-sortable.on { color: #2980b9; font-weight: 600; }
.bd-tr { width: 74px; flex: none; display: flex; align-items: center; gap: 4px; }
.bd-tr-track { flex: 1; min-width: 0; height: 5px; background: #f0f2f5; border-radius: 3px; overflow: hidden; }
.bd-tr-fill { display: block; height: 100%; background: linear-gradient(90deg, #8899c8, #a8b8e0); border-radius: 3px; }
.bd-tr-num { font-size: 11px; color: #666; flex: none; font-variant-numeric: tabular-nums; }
.bd-net { width: 72px; flex: none; text-align: right; font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; }

@media (max-width: 480px) {
  .bd-page { padding-left: 10px; padding-right: 10px; }
  .bd-price { display: none; }
  .bd-sortable { width: 52px; }
  .bd-tr { width: 62px; }
  .bd-net { width: 64px; }
}
@media (min-width: 768px) {
  .bd-page { padding: 8px 28px 20px; }
}
</style>
