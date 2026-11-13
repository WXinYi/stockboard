<script setup>
import { ref, computed, inject, watch, nextTick, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter, onBeforeRouteLeave } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'
import { rankCellHtml } from '../utils/format.js'
import { RecycleScroller } from 'vue3-virtual-scroller'
import 'vue3-virtual-scroller/dist/vue3-virtual-scroller.css'

const router = useRouter()
// P3 4 契约只提供 { totalReturn, dailyReturn, monthlyReturn, yearlyReturn, netValue }。
// 周收益 / 回撤 / 风格 / 仓位 / 运行天数 / 关注数已无数据源：相关列、排序项与筛选一并下线。
const {
  sortedPlayers: sorted, tradedPlayerIds, watchedIds: WATCHED,
  loading, sliceErrors, sliceLoaded, ensureSlices,
} = inject('stockData')

function navigateToPlayer(id) { router.push('/player/' + id) }

const todayOnly = ref(true)
const search = ref('')

// 当日是否有调仓数据（非交易日 tradedPlayerIds 为空 → todayOnly 过滤自动失效，展示上一交易日全量榜单）
const hasTodayTrades = computed(() => tradedPlayerIds.value.size > 0)

const allPlayers = computed(() => [...sorted.value.pinned, ...sorted.value.rest])

// ── 榜单三态：加载中 / 分片失败可重试 / 数据为空 ──
// Failure ≠ Fact：拉不到 players 分片时不能让空表格冒充「今日无人操作」，
// 也不能把缺失收益显示成 +0.00%。三态各自显式，且失败/空态都给重试入口。
const retrying = ref(false)
const rankState = computed(() => {
  if (retrying.value || loading.value.players) return 'loading'
  if (sliceErrors.value.players || sliceErrors.value.today) return 'error'
  if (sliceLoaded.value.players && !allPlayers.value.length) return 'empty'
  return 'ok'
})

async function retry() {
  if (retrying.value) return
  retrying.value = true
  try {
    await ensureSlices(['players', 'today'])
  } catch { /* ensureSlices 已在 sliceErrors 中标记失败原因 */ }
  finally { retrying.value = false }
}

const { sorted: sortedList, toggle: tog, indicator: ind, sortKey, sortDir } = useTableSort(allPlayers, 'total_return')

// 应用筛选
const displayList = computed(() => {
  const list = [...sortedList.value]
  let filtered = list.filter(p => !WATCHED.value.has(p.zh_id))
  if (todayOnly.value && hasTodayTrades.value) filtered = filtered.filter(p => tradedPlayerIds.value.has(p.zh_id))
  // 置顶选手独立（渲染时放在最前）
  const pinned = list.filter(p => WATCHED.value.has(p.zh_id))
  return { pinned, rest: filtered }
})

const filteredCount = computed(() => displayList.value.pinned.length + displayList.value.rest.length)

// 搜索过滤（置顶在前）
const searchList = computed(() => {
  let l = [...displayList.value.pinned, ...displayList.value.rest]
  const q = search.value.trim().toLowerCase()
  if (q) l = l.filter(p => (p.name + '' + p.zh_id).toLowerCase().includes(q))
  return l
})

// 排名映射
const rankMap = computed(() => {
  const map = {}
  sortedList.value.forEach((p, i) => { map[p.zh_id] = i + 1 })
  return map
})

const sortPeriods = [
  { key: 'daily_return', label: '日' },
  { key: 'monthly_return', label: '月' },
  { key: 'yearly_return', label: '年' },
  { key: 'total_return', label: '总' },
]
const activePeriod = ref('total_return')
function setPeriod(key) {
  if (activePeriod.value === key) { activePeriod.value = null }
  else { activePeriod.value = key; tog(key) }
}
const sortHeaders = computed(() => {
  const base = activePeriod.value
    ? [{ key: activePeriod.value, label: sortPeriods.find(s => s.key === activePeriod.value).label + '收益' }]
    : sortPeriods.map(s => ({ key: s.key, label: s.label + '收益' }))
  base.push({ key: 'net_value', label: '净值' })
  return base
})

// ── 虚拟滚动：RecycleScroller（固定行高）──
const ROW_H = 40
const rankScroller = ref(null)

// ── 详情返回保持列表位置 ──
// 关键坑(3个, 全踩过):
// 1. 真实滚动容器是 RecycleScroller 内部(.rank-vscroll), 不是 window
// 2. KeepAlive 停用时先摘 DOM 再触发 onDeactivated, 摘出后无布局盒 scrollTop 恒 0
//    → 必须在路由离开守卫(onBeforeRouteLeave)里保存, DOM 尚在文档读数才准确
// 3. 后台标签页 requestAnimationFrame 不触发 → 恢复用 setTimeout;
//    重新插入初帧布局未稳 scrollTop 可能被钳回 0 → 验证不达就重试(最多 20×120ms)
let savedListScroll = 0
onBeforeRouteLeave(() => {
  savedListScroll = rankScroller.value?.$el?.scrollTop ?? 0
})
onActivated(() => {
  if (!savedListScroll) return
  const pos = savedListScroll
  let tries = 0
  const restore = () => {
    const sc = rankScroller.value
    if (!sc) return
    sc.scrollToPosition(pos)
    const got = sc.$el?.scrollTop ?? 0
    if (Math.abs(got - pos) > 1 && tries++ < 20) setTimeout(restore, 120)
  }
  nextTick(restore)
})

// 筛选/排序/搜索变化 → 回到顶部
watch(
  () => [search.value, todayOnly.value, sortKey.value, sortDir.value],
  () => {
    rankScroller.value?.scrollToPosition(0)
  }
)

// ── iOS/WebKit 列宽修正 ──
// WebKit 对 flex 容器的 max-content 计算不可靠（实测把表头 570 算成 351），
// 导致 .rank-cols/.rank-vscroll 过窄、行被 overflow-x:hidden 裁剪（右列空白）。
// 用表头 scrollWidth 实测列总宽，直接设成 .rank-cols 的 min-width，绕开关键字计算。
const rankWrap = ref(null)
function syncColWidth() {
  nextTick(() => {
    const wrap = rankWrap.value
    const cols = wrap?.querySelector('.rank-cols')
    const head = wrap?.querySelector('.rank-head')
    if (cols && head && head.scrollWidth > 0) {
      const w = head.scrollWidth + 'px'
      if (cols.style.minWidth !== w) cols.style.minWidth = w
    }
  })
}
watch(activePeriod, syncColWidth)  // 列数变化（如切"全部"）时重算
watch(rankState, s => { if (s === 'ok') syncColWidth() })  // 三态切回榜单时表头才存在，需重算列宽
function onWindowResize() { syncColWidth() }  // 断点切换（移动↔桌面）列宽变化
onMounted(() => {
  syncColWidth()
  window.addEventListener('resize', onWindowResize)
})
onUnmounted(() => window.removeEventListener('resize', onWindowResize))
</script>

<template>
  <div class="rank-page">
    <div class="search-box">
      <input type="text" v-model="search" placeholder="🔍 搜索选手名称..." />
    </div>
    <div class="sort-row">
      <span style="font-size:12px;color:var(--ink-3);margin-right:6px;">排序:</span>
      <button v-for="s in sortPeriods" :key="s.key"
        :class="['filter-btn', { active: activePeriod === s.key }]"
        @click="setPeriod(s.key)">{{ s.label }}</button>
      <button v-if="activePeriod" class="filter-btn" @click="activePeriod = null">全部</button>
    </div>
    <div class="filter-row">
      <span style="font-size:12px;color:var(--ink-3);">筛选:</span>
      <button :class="['filter-btn', { active: todayOnly && hasTodayTrades }]" @click="todayOnly = !todayOnly">今日操作</button>
      <span v-if="!hasTodayTrades" style="font-size:11px;color:var(--accent);">非交易日，已展示最近榜单</span>
      <span style="font-size:11px;color:var(--ink-3);margin-left:auto;">{{ filteredCount }} 人</span>
    </div>
    <div class="rank-wrap" ref="rankWrap">
      <div v-if="rankState === 'loading'" class="rank-state" data-state="loading">
        <div class="loading-spinner"></div>
        <p class="rank-state-title">正在加载选手榜单…</p>
        <p class="rank-state-sub">读取 players / today 分片</p>
      </div>
      <div v-else-if="rankState === 'error'" class="rank-state" data-state="error">
        <p class="rank-state-title">榜单数据加载失败</p>
        <p class="rank-state-sub">players / today 分片读取失败。未用 0 填充收益，请重试后再看榜单。</p>
        <button class="filter-btn rank-retry" @click="retry()">重试</button>
      </div>
      <div v-else-if="rankState === 'empty'" class="rank-state" data-state="empty">
        <p class="rank-state-title">暂无选手数据</p>
        <p class="rank-state-sub">当前快照没有榜单记录；若刚完成采集，可重试刷新。</p>
        <button class="filter-btn rank-retry" @click="retry()">重试</button>
      </div>
      <div v-else class="rank-hscroll">
        <div class="rank-cols">
          <div class="rank-head">
            <span class="c-rank">#</span>
            <span class="c-name">选手</span>
            <span v-for="h in sortHeaders" :key="'h'+h.key" class="c-num sortable" @click="tog(h.key)">{{ h.label }}{{ ind(h.key) }}</span>
          </div>
          <RecycleScroller
            ref="rankScroller"
            class="rank-vscroll"
            :items="searchList"
            :item-size="ROW_H"
            key-field="zh_id"
            v-slot="{ item }"
          >
            <div class="rank-row" :class="{ pinned: WATCHED.has(item.zh_id) }" @click="navigateToPlayer(item.zh_id)">
              <span class="c-rank">{{ rankMap[item.zh_id] ?? '—' }}</span>
              <span class="c-name">
                <strong style="color:var(--accent)">{{ item.name || item.zh_id }}<template v-if="WATCHED.has(item.zh_id)"> ⭐</template></strong>
                <span v-if="tradedPlayerIds.has(item.zh_id)" class="trade-dot" title="今日有调仓"></span>
              </span>
              <span v-for="h in sortHeaders" :key="'c'+h.key" class="c-num" v-html="rankCellHtml(h.key, item[h.key])"></span>
            </div>
          </RecycleScroller>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 三态占位：与榜单共用容器，避免加载/失败时露出空表头 + 0 人的误导性表格 */
.rank-state {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; min-height: 220px; padding: 24px 16px;
  text-align: center; font-family: var(--mono); font-size: 12px; color: var(--ink-3);
}
.rank-state-title { margin: 0; font-size: 13px; color: var(--ink-2); }
.rank-state-sub { margin: 0; max-width: 320px; font-size: 11px; line-height: 1.6; }
.rank-state p { margin: 0; }
.rank-retry { margin-top: 6px; }
.rank-wrap {
  display: flex;
  flex-direction: column;
}
.rank-hscroll {
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
}
.rank-cols {
  min-width: max-content;
  width: 100%;
  display: flex;
  flex-direction: column;
}
.rank-head, .rank-row {
  display: flex;
  align-items: center;
  white-space: nowrap;
}
.rank-head {
  flex: 0 0 auto;
  background: rgba(32, 36, 44, .92);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  font-size: 11px;
  font-weight: 450;
  color: var(--ink-3);
  padding: 8px 0;
  border-bottom: 0.5px solid rgba(0,0,0,.045);
  letter-spacing: .02em;
}
/* 虚拟滚动容器必须自约束高度：RecycleScroller 内部 item-wrapper 高度=行数×行高（~39万px），
   若靠父级 flex 撑出高度，clientHeight 会暴涨触发 "Rendered items limit reached"（>1000 行）。
   overflow-y:auto + max-height 让它在内容超高时固定为视口高度、内容短时自适应。
   overflow-x:hidden 关键：库 CSS 只设 overflow-y:auto，按规范 overflow-x 会被计算成 auto，
   使本容器成为横向滚动候选。iOS 嵌套滚动时内层容器会抢走横向手势，导致行上左滑不滚动
   .rank-hscroll（看起来右侧空白）。显式 hidden 让它只滚纵向，横向手势归外层容器。 */
.rank-vscroll {
  /* 去卡片化后列表高度=视口-固定头部(搜索/排序/筛选/导航), 整页不再嵌套滚动 */
  max-height: calc(100vh - 300px);
  min-height: 120px;
  overflow-x: hidden;
  /* iOS Safari 上 .rank-cols 的 min-width:max-content 未能按表头撑宽(实测行被裁在视口宽)。
     直接在本容器下 min-width 下限,让行的列总宽决定滚动宽度,不依赖外层交叉轴撑宽。 */
  min-width: max-content;
}
.rank-row {
  height: 40px;
  font-size: 13px;
  font-weight: 400;
  color: var(--ink);
  cursor: pointer;
  border-bottom: 0.5px solid rgba(0,0,0,.04);
  transition: background .15s;
}
.rank-row:hover { background: rgba(107,125,179,.04); }
.rank-row.pinned { background: rgba(107,125,179,.025); }
/* 固定宽度列一律 min-width:0，防止内容把列撑宽导致列与表头错位 */
.c-rank { flex: 0 0 34px; min-width: 0; text-align: center; color: var(--ink-3); }
.c-name {
  flex: 1 1 210px;
  min-width: 210px;
  padding: 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.c-num { flex: 0 0 72px; min-width: 0; text-align: right; padding: 0 8px; }
.sortable { cursor: pointer; user-select: none; }
@media (min-width: 768px) {
  /* 桌面: 无底部导航大留白, 头部更矮 → 列表更高 */
  .rank-vscroll { max-height: calc(100vh - 240px); }
}
@media (max-width: 767px) {
  .rank-vscroll { max-height: calc(100vh - 300px); }
  .rank-row { font-size: 12px; }
  .c-rank { flex-basis: 28px; }
  .c-name { flex-basis: 150px; min-width: 150px; }
  .c-num { flex-basis: 58px; padding: 0 4px; }
}
</style>
