<script setup>
import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'
import { useCopyCode } from '../composables/useCopyCode.js'
import { pctHtml } from '../utils/format.js'

const router = useRouter()
const { stockStats: stats, sortedPlayers: sp, isQuality } = inject('stockData')
function navigateToPlayer(id) { router.push('/player/' + id) }

// 📈 跳转股票详情页(自建详情, 页内含 H5 嵌套入口)
function openStockDetail(c, n) { router.push({ path: '/stock/' + c, query: { name: n } }) }

const stockSearch = ref('')
const lookedUpHolders = ref(null)
const allPlayers = computed(() => [...sp.value.pinned, ...sp.value.rest])

const { sorted: sortedStats, toggle: tog, indicator: ind } = useTableSort(computed(() => stats.value), 'tp')

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}

function lookupStock() {
  const q = stockSearch.value.trim()
  if (!q) { lookedUpHolders.value = null; return }
  // stockStats 是全量 code→name 映射（stocks.json），支持按股票名称反查
  const codeToName = {}
  for (const s of stats.value) codeToName[s.c] = s.n
  const holders = allPlayers.value.filter(p => {
    return (p.stocks || []).some(code => {
      // code.includes 天然容忍省略前导零（01810.includes('1810')），无需额外处理
      if (code.includes(q)) return true
      return (codeToName[code] || '').includes(q)
    })
  })
  lookedUpHolders.value = holders.sort((a, b) => (b._total_position || 0) - (a._total_position || 0))
}

// ── 股票名称 → 点击跳详情页; 名称后复制图标 → 复制代码到剪贴板 ──
const { copiedKey, copyStockCode } = useCopyCode()
</script>

<template>
  <div class="card" style="margin-bottom:20px;">
    <h2>🔍 反向查股票</h2>
    <p class="hint">输入股票代码或名称，查看哪些选手持有它</p>
    <div class="search-box" style="display:flex;gap:8px;">
      <input type="text" v-model="stockSearch" placeholder="例: 000938 或 紫光股份" @keyup.enter="lookupStock" style="flex:1;" />
      <button @click="lookupStock" style="padding:8px 16px;background:#2980b9;color:white;border:none;border-radius:8px;cursor:pointer;font-size:13px;">查询</button>
    </div>
    <div v-if="lookedUpHolders !== null" style="margin-top:12px;">
      <p v-if="!lookedUpHolders.length" class="empty-state">未找到持有该股票的选手</p>
      <div v-else>
        <p style="font-size:12px;color:#888;margin-bottom:8px;">{{ lookedUpHolders.length }} 人持有:</p>
        <div style="display:flex;flex-wrap:wrap;gap:8px;">
          <span v-for="p in lookedUpHolders" :key="p.zh_id" style="background:#f0f2f5;border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer;" @click="navigateToPlayer(p.zh_id)">
            {{ p.name || p.zh_id }}<span v-if="isQuality(p)"> 🏅</span> <span style="color:#888;">{{ (p._total_position || 0).toFixed(0) }}%仓位</span>
          </span>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>重仓共识 <span class="badge">Top 20</span></h2>
    <p class="hint">按加权总仓位排序，点击表头可切换排序</p>
    <div style="max-height:500px;overflow-y:auto;">
      <table><thead><tr>
        <th>#</th><th>股票</th><th>代码</th>
        <th style="cursor:pointer;" @click="tog('h')">持有人{{ ind('h') }}</th>
        <th style="cursor:pointer;" @click="tog('tp')">总仓位{{ ind('tp') }}</th>
        <th style="cursor:pointer;" @click="tog('ap')">平均盈亏{{ ind('ap') }}</th>
      </tr></thead>
        <tbody>
          <tr v-for="(s, i) in sortedStats.slice(0,20)" :key="s.c">
            <td>{{ i + 1 }}</td>
            <td>
              <strong class="stock-name" @click="openStockDetail(s.c, s.n)" :title="'点击查看详情 ' + s.n">{{ s.n }}</strong>
              <button class="stock-copy" @click.stop="copyStockCode(s.c)" title="复制代码" :aria-label="'复制代码 ' + s.c">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
              </button>
              <span v-if="copiedKey === s.c" class="copied-tip">✓ 已复制</span>
            </td>
            <td style="color:#999;">{{ s.c }}</td>
            <td>{{ s.h }}人</td>
            <td>
              <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, s.tp/s.h) + '%' }"></span></span>
              {{ s.tp.toFixed(0) }}%
            </td>
            <td v-html="pctHtml(s.ap/s.h)"></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
/* 下划线标识可点击 → 跳详情页 */
.stock-name { cursor: pointer; text-decoration: underline; text-underline-offset: 3px; text-decoration-color: rgba(41,128,185,.45); }
.stock-name:hover { color: #2980b9; }
.stock-copy { border: none; background: none; cursor: pointer; color: #aaa; padding: 0 3px; vertical-align: middle; }
.stock-copy:hover { color: #2980b9; }
.copied-tip { color: #27ae60; font-size: 11px; margin-left: 4px; }
</style>
