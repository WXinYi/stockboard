<script setup>
import { ref, computed, inject, watch, nextTick, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter, onBeforeRouteLeave } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'
import { rankCellHtml, drawdownColor } from '../utils/format.js'
import { RecycleScroller } from 'vue3-virtual-scroller'
import 'vue3-virtual-scroller/dist/vue3-virtual-scroller.css'

const router = useRouter()
const { sortedPlayers: sorted, playerStyles: styles, tradedPlayerIds, isQuality } = inject('stockData')

const WATCHED = new Set(['900456476', '900450475', '900351276', '900401128', '900422074', '900443192', '900315547', '900240956', '900376763', '900439290'])

function navigateToPlayer(id) { router.push('/player/' + id) }

const qualityOn = ref(false)
const todayOnly = ref(true)
const search = ref('')
const minRanks = ref(0)

// 当日是否有调仓数据（非交易日 tradedPlayerIds 为空 → todayOnly 过滤自动失效，展示上一交易日全量榜单）
const hasTodayTrades = computed(() => tradedPlayerIds.value.size > 0)

const allPlayers = computed(() => [...sorted.value.pinned, ...sorted.value.rest])

const { sorted: sortedList, toggle: tog, indicator: ind, sortKey, sortDir } = useTableSort(allPlayers, 'weekly_return')

// 应用筛选
const displayList = computed(() => {
  let list = [...sortedList.value]
  let filtered = list.filter(p => !WATCHED.has(p.zh_id))
  if (qualityOn.value) filtered = filtered.filter(isQuality)
  if (todayOnly.value && hasTodayTrades.value) filtered = filtered.filter(p => tradedPlayerIds.value.has(p.zh_id))
  if (minRanks.value > 0) filtered = filtered.filter(p => (p.ranks || []).length >= minRanks.value)
  // 置顶选手独立（渲染时放在最前）
  const pinned = list.filter(p => WATCHED.has(p.zh_id))
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

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}
function posLabel(total) {
  if (!total || total === 0) return '空仓'
  if (total < 10) return '1成以下'
  if (total < 30) return '1-3成'
  if (total < 50) return '3-5成'
  if (total < 70) return '5-7成'
  if (total < 90) return '7-9成'
  return '9成以上'
}

const sortPeriods = [
  { key: 'daily_return', label: '日' },
  { key: 'weekly_return', label: '周' },
  { key: 'monthly_return', label: '月' },
  { key: 'yearly_return', label: '年' },
  { key: 'total_return', label: '总' },
]
const activePeriod = ref('weekly_return')
function setPeriod(key) {
  if (activePeriod.value === key) { activePeriod.value = null }
  else { activePeriod.value = key; tog(key) }
}
const sortHeaders = computed(() => {
  const base = activePeriod.value
    ? [{ key: activePeriod.value, label: sortPeriods.find(s => s.key === activePeriod.value).label + '收益' }]
    : sortPeriods.map(s => ({ key: s.key, label: s.label + '收益' }))
  base.push({ key: 'net_value', label: '净值' }, { key: 'followers', label: '关注' })
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
  () => [search.value, qualityOn.value, todayOnly.value, minRanks.value, sortKey.value, sortDir.value],
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
      <span style="font-size:12px;color:#888;margin-right:6px;">排序:</span>
      <button v-for="s in sortPeriods" :key="s.key"
        :class="['filter-btn', { active: activePeriod === s.key }]"
        @click="setPeriod(s.key)">{{ s.label }}</button>
      <button v-if="activePeriod" class="filter-btn" @click="activePeriod = null">全部</button>
    </div>
    <div class="filter-row">
      <span style="font-size:12px;color:#888;">筛选:</span>
      <button :class="['filter-btn', { active: qualityOn }]" @click="qualityOn = !qualityOn">高质量</button>
      <button :class="['filter-btn', { active: todayOnly && hasTodayTrades }]" @click="todayOnly = !todayOnly">今日操作</button>
      <span v-if="!hasTodayTrades" style="font-size:11px;color:#e67e22;">非交易日，已展示最近榜单</span>
      <span style="font-size:12px;color:#888;">上榜≥</span>
      <button v-for="n in [1,3,5]" :key="n"
              :class="['filter-btn', { active: minRanks === n }]"
              @click="minRanks = minRanks === n ? 0 : n">{{ n }}榜</button>
      <span style="font-size:11px;color:#888;margin-left:auto;">{{ filteredCount }} 人</span>
    </div>
    <div class="rank-wrap" ref="rankWrap">
      <div class="rank-hscroll">
        <div class="rank-cols">
          <div class="rank-head">
            <span class="c-rank">#</span>
            <span class="c-name">选手</span>
            <span v-for="h in sortHeaders" :key="'h'+h.key" class="c-num sortable" @click="tog(h.key)">{{ h.label }}{{ ind(h.key) }}</span>
            <span class="c-num sortable" @click="tog('max_drawdown')">回撤{{ ind('max_drawdown') }}</span>
            <span class="c-style">风格</span>
            <span class="c-pos">仓位</span>
            <span class="c-num sortable" @click="tog('days')">运行{{ ind('days') }}</span>
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
              <span class="c-rank">{{ rankMap[item.zh_id] || 1 }}</span>
              <span class="c-name">
                <strong :style="{ color: WATCHED.has(item.zh_id) ? '#e67e22' : '#2980b9' }">{{ item.name || item.zh_id }}<template v-if="WATCHED.has(item.zh_id)"> ⭐</template><template v-else-if="isQuality(item)"> 🏅</template></strong>
                <span v-if="tradedPlayerIds.has(item.zh_id)" class="trade-dot" title="今日有调仓"></span>
              </span>
              <span v-for="h in sortHeaders" :key="'c'+h.key" class="c-num" v-html="rankCellHtml(h.key, item[h.key])"></span>
              <span class="c-num" :style="{ color: drawdownColor(item.max_drawdown) }">{{ (item.max_drawdown || 0).toFixed(1) }}%</span>
              <span class="c-style">{{ styles[item.zh_id]?.emoji || '—' }}</span>
              <span class="c-pos">
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, item._total_position || 0) + '%' }"></span></span>
                {{ (item._total_position || 0).toFixed(0) }}%
              </span>
              <span class="c-num">{{ item.days || 0 }}天</span>
            </div>
          </RecycleScroller>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
  background: rgba(255,255,255,.92);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  font-size: 11px;
  font-weight: 450;
  color: #8e8e9a;
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
  color: #1C1C1E;
  cursor: pointer;
  border-bottom: 0.5px solid rgba(0,0,0,.04);
  transition: background .15s;
}
.rank-row:hover { background: rgba(107,125,179,.04); }
.rank-row.pinned { background: rgba(107,125,179,.025); }
/* 固定宽度列一律 min-width:0，防止内容（如进度条+百分比文字）把列撑宽导致列与表头错位 */
.c-rank { flex: 0 0 34px; min-width: 0; text-align: center; color: #8e8e9a; }
.c-name {
  flex: 1 1 210px;
  min-width: 210px;
  padding: 0 8px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.c-num { flex: 0 0 72px; min-width: 0; text-align: right; padding: 0 8px; }
.c-style { flex: 0 0 44px; min-width: 0; text-align: center; }
.c-pos {
  flex: 0 0 108px;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
  padding: 0 8px;
}
/* 进度条改为弹性：占满剩余空间但不超过 60px，避免“100%”文本把列撑宽 */
.c-pos .progress-bar {
  flex: 1 1 0;
  min-width: 24px;
  max-width: 60px;
  margin-right: 0;
}
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
  .c-style { flex-basis: 36px; }
  .c-pos { flex-basis: 96px; padding: 0 4px; }
}
</style>
