<script setup>
import { ref, computed } from 'vue'
import { useTableSort } from '../composables/useTableSort.js'

const props = defineProps({
  sorted: { type: Object, required: true },
  styles: { type: Object, default: () => ({}) },
  tradedPlayerIds: { type: Set, default: () => new Set() },
})
const emit = defineEmits(['show-player'])

const qualityOn = ref(false)
const search = ref('')
const minRanks = ref(0)

function isQuality(p) {
  return (p.days || 0) >= 200 && (p.max_drawdown || 0) <= 30
}

const allPlayers = computed(() => [...props.sorted.pinned, ...props.sorted.rest])

const { sorted: sortedList, toggle: tog, indicator: ind, sortKey } = useTableSort(allPlayers, 'total_return')

// 应用筛选
const displayList = computed(() => {
  const watched = new Set(['900240956'])
  let list = [...sortedList.value]
  let filtered = list.filter(p => !watched.has(p.zh_id))
  if (qualityOn.value) filtered = filtered.filter(isQuality)
  if (minRanks.value > 0) filtered = filtered.filter(p => (p.ranks || []).length >= minRanks.value)
  // 置顶选手独立
  const pinned = list.filter(p => watched.has(p.zh_id))
  return { pinned, rest: filtered }
})

const filteredCount = computed(() => displayList.value.pinned.length + displayList.value.rest.length)

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

const sortHeaders = [
  { key: 'total_return', label: '总收益' },
  { key: 'yearly_return', label: '年收益' },
  { key: 'monthly_return', label: '月收益' },
  { key: 'weekly_return', label: '周收益' },
  { key: 'daily_return', label: '日收益' },
  { key: 'net_value', label: '净值' },
  { key: 'followers', label: '关注' },
]
</script>

<template>
  <div class="card">
    <div class="search-box">
      <input type="text" v-model="search" placeholder="🔍 搜索选手名称或ID..." />
    </div>
    <div class="filter-row">
      <span style="font-size:12px;color:#888;">筛选:</span>
      <button :class="['filter-btn', { active: qualityOn }]" @click="qualityOn = !qualityOn">高质量</button>
      <span style="font-size:12px;color:#888;">上榜≥</span>
      <button v-for="n in [1,3,5]" :key="n"
              :class="['filter-btn', { active: minRanks === n }]"
              @click="minRanks = minRanks === n ? 0 : n">{{ n }}榜</button>
      <span style="font-size:11px;color:#888;margin-left:auto;">{{ filteredCount }} 人</span>
    </div>
    <div style="max-height:600px;overflow:auto;">
      <table>
        <thead><tr>
          <th>#</th><th>选手</th><th>ID</th>
          <th v-for="h in sortHeaders" :key="h.key"
              style="cursor:pointer;user-select:none;"
              @click="tog(h.key)">{{ h.label }}{{ ind(h.key) }}</th>
          <th style="cursor:pointer;user-select:none;" @click="tog('max_drawdown')">回撤{{ ind('max_drawdown') }}</th>
          <th>风格</th>
          <th>仓位</th>
          <th style="cursor:pointer;user-select:none;" @click="tog('days')">运行{{ ind('days') }}</th>
        </tr></thead>
        <tbody>
          <tr v-for="p in displayList.pinned" :key="'p'+p.zh_id"
              :class="['clickable', 'pinned-row']"
              @click="emit('show-player', p.zh_id)"
              v-show="!search || (p.name+''+p.zh_id).toLowerCase().includes(search.toLowerCase())">
            <td>{{ rankMap[p.zh_id] || 1 }}</td>
            <td><strong style="color:#e67e22;">{{ p.name || p.zh_id }} ⭐</strong><span v-if="tradedPlayerIds.has(p.zh_id)" class="trade-dot" title="今日有调仓"></span></td>
            <td style="color:#999;font-size:11px;">{{ p.zh_id }} <span v-if="p.ranks?.length" style="font-size:10px;color:#888;">({{ p.ranks.length }}榜)</span></td>
            <td v-for="h in sortHeaders" :key="h.key" v-html="pct(p[h.key])"></td>
            <td>{{ (p.max_drawdown || 0).toFixed(1) }}%</td>
            <td><span style="font-size:11px;">{{ styles[p.zh_id]?.emoji || '—' }}</span></td>
            <td>
              <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, p._total_position || 0) + '%' }"></span></span>
              {{ (p._total_position || 0).toFixed(0) }}%
            </td>
            <td>{{ p.days || 0 }}天</td>
          </tr>
          <tr v-for="p in displayList.rest" :key="p.zh_id"
              class="clickable" @click="emit('show-player', p.zh_id)"
              v-show="!search || (p.name+''+p.zh_id).toLowerCase().includes(search.toLowerCase())">
            <td>{{ rankMap[p.zh_id] || 1 }}</td>
            <td><strong style="color:#2980b9;">{{ p.name || p.zh_id }}<span v-if="isQuality(p)"> 🏅</span></strong><span v-if="tradedPlayerIds.has(p.zh_id)" class="trade-dot" title="今日有调仓"></span></td>
            <td style="color:#999;font-size:11px;">{{ p.zh_id }} <span v-if="p.ranks?.length" style="font-size:10px;color:#888;">({{ p.ranks.length }}榜)</span></td>
            <td v-for="h in sortHeaders" :key="h.key" v-html="pct(p[h.key])"></td>
            <td>{{ (p.max_drawdown || 0).toFixed(1) }}%</td>
            <td><span style="font-size:11px;">{{ styles[p.zh_id]?.emoji || '—' }}</span></td>
            <td>
              <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, p._total_position || 0) + '%' }"></span></span>
              {{ (p._total_position || 0).toFixed(0) }}%
            </td>
            <td>{{ p.days || 0 }}天</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
