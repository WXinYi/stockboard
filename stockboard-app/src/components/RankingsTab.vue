<script setup>
import { ref, computed, inject, watch, nextTick, onMounted, onUnmounted, onActivated } from 'vue'
import { useRouter, onBeforeRouteLeave } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'
import { rankCellHtml, drawdownColor } from '../utils/format.js'
import { RecycleScroller } from 'vue3-virtual-scroller'
import 'vue3-virtual-scroller/dist/vue3-virtual-scroller.css'

const router = useRouter()
const { sortedPlayers: sorted, playerStyles: styles, tradedPlayerIds, isQuality, watchedIds: WATCHED } = inject('stockData')

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

// ── 详情返回保持列表位置(2026-09-23 改 page-mode 后滚动在 window 上) ──
// 保留原重试思路: 重新插入初帧布局未稳, 恢复可能被钳回 → 验证不达就重试(最多 20×120ms)
let savedListScroll = 0
onBeforeRouteLeave(() => {
  savedListScroll = window.scrollY
})
onActivated(() => {
  if (!savedListScroll) return
  const pos = savedListScroll
  let tries = 0
  const restore = () => {
    window.scrollTo(0, pos)
    const got = window.scrollY
    if (Math.abs(got - pos) > 1 && tries++ < 20) setTimeout(restore, 120)
  }
  nextTick(restore)
})

// 筛选/排序/搜索变化 → 回到顶部
watch(
  () => [search.value, qualityOn.value, todayOnly.value, minRanks.value, sortKey.value, sortDir.value],
  () => {
    window.scrollTo(0, 0)
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
            :page-mode="true"
            :items="searchList"
            :item-size="ROW_H"
            key-field="zh_id"
            v-slot="{ item }"
          >
            <div class="rank-row" :class="{ pinned: WATCHED.has(item.zh_id) }" @click="navigateToPlayer(item.zh_id)">
              <span class="c-rank">{{ rankMap[item.zh_id] ?? '—' }}</span>
              <span class="c-name">
                <strong :style="{ color: WATCHED.has(item.zh_id) ? '#e67e22' : '#2980b9' }">{{ item.name || item.zh_id }}<template v-if="WATCHED.has(item.zh_id)"> ⭐</template><template v-else-if="isQuality(item)"> 🏅</template></strong>
                <span v-if="tradedPlayerIds.has(item.zh_id)" class="trade-dot" title="今日有调仓"></span>
              </span>
              <span v-for="h in sortHeaders" :key="'c'+h.key" class="c-num" v-html="rankCellHtml(h.key, item[h.key])"></span>
              <span class="c-num" :style="{ color: drawdownColor(item.max_drawdown) }">{{ item.max_drawdown == null ? '—' : item.max_drawdown.toFixed(1) + '%' }}</span>
              <span class="c-style">{{ styles[item.zh_id]?.emoji || '—' }}</span>
              <span class="c-pos">
                <span class="progress-bar"><span class="fill" :style="{ width: (item._total_position == null ? 0 : Math.min(100, item._total_position)) + '%' }"></span></span>
                {{ item._total_position == null ? '—' : item._total_position.toFixed(0) + '%' }}
              </span>
              <span class="c-num">{{ item.days == null ? '—' : item.days + '天' }}</span>
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
/* 2026-09-23 改 page-mode(用页面滚动, 消除双滚动条): 容器不再自约束高度,
   随内容自然撑开(虚拟滚动的 item-wrapper ~39万px, 浏览器上限内安全)。
   overflow-x:hidden 保留: iOS 上防止内层抢走横向手势(同前)。
   注: overflow-x:hidden 会使 overflow-y 计算为 auto, 但容器高度=内容高度,
   无溢出即无滚动条, 不会复辟内层滚动。 */
.rank-vscroll {
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
  .rank-cols { position: sticky; top: 47px; z-index: 5; }
}
@media (max-width: 767px) {
  .rank-cols { position: sticky; top: 47px; z-index: 5; }
  .rank-row { font-size: 12px; }
  .c-rank { flex-basis: 28px; }
  .c-name { flex-basis: 150px; min-width: 150px; }
  .c-num { flex-basis: 58px; padding: 0 4px; }
  .c-style { flex-basis: 36px; }
  .c-pos { flex-basis: 96px; padding: 0 4px; }
}
</style>
