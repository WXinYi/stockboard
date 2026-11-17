<script setup>
import { computed, inject, ref, onActivated, onDeactivated, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useTableSort } from '../composables/useTableSort.js'
import { useCopyCode } from '../composables/useCopyCode.js'
import { pctHtml } from '../utils/format.js'
import { onlyAStock } from '../utils/stockMarket.js'
import { snapshotLabel, formatBeijingShort } from '../utils/formatBeijing.js'

const router = useRouter()
const showInfo = ref(false)
const stockData = inject('stockData')
const { stockStats: stats, stocksSnapshot, stocksUpdatedAt, sliceLoaded, sliceErrors, loading } = stockData
const refresh = inject('refreshData', () => {})

const label = computed(() => snapshotLabel(stocksSnapshot.value) || '持仓快照（盘中不刷新）')
const timeText = computed(() => formatBeijingShort(stocksUpdatedAt.value))
const failed = computed(() => !!sliceErrors.value.stocks)
const showLoading = computed(() => loading.value.stocks || !sliceLoaded.value.stocks)

// 只看 A 股；数据契约保留全量，展示层过滤可回退
const viewStats = computed(() => onlyAStock(stats.value))

const savedScrollY = ref(0)
function onSaveScroll() { savedScrollY.value = window.scrollY }
onActivated(() => {
  window.addEventListener('scroll', onSaveScroll, { passive: true })
  nextTick(() => { if (savedScrollY.value > 0) window.scrollTo(0, savedScrollY.value) })
})
onDeactivated(() => window.removeEventListener('scroll', onSaveScroll))
onBeforeUnmount(() => window.removeEventListener('scroll', onSaveScroll))

const { sorted: sortedStats, toggle: tog, indicator: ind } = useTableSort(viewStats, 'tp')
const { copiedKey, copyStockCode } = useCopyCode()

function openStockDetail(c, n) { router.push({ path: '/stock/' + c, query: { name: n } }) }
</script>

<template>
  <div class="card">
    <h2>
      重仓共识
      <span class="badge">A股 Top 20</span>
      <button class="info-btn" @click="showInfo = !showInfo" :aria-expanded="showInfo"
              aria-controls="stocks-info" aria-label="查看重仓共识说明">说明</button>
    </h2>
    <p class="compact-meta">
      {{ label }}<span v-if="timeText">（{{ timeText }} 北京时间）</span>
      · A股 · 点击表头排序
    </p>
    <p v-if="showInfo" id="stocks-info" class="info-detail">
      仅展示 A 股（沪深/北交所/ETF/可转债，港股已滤除）；全体采集选手持仓聚合。
      合计仓位=所有持有人仓位之和，括号内为人均。P3 起选手级持仓明细不再进入静态产物，反向查股票已下线。
    </p>

    <div v-if="showLoading" class="state-line">正在读取持仓快照…</div>
    <div v-else-if="failed" class="state-line error">
      持仓数据加载失败，<button type="button" @click="refresh()">重试</button>
    </div>
    <template v-else>
      <div v-if="!sortedStats.length" class="empty">当日暂无 A 股重仓共识数据。</div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>股票</th><th>代码</th>
              <th tabindex="0" role="button" @click="tog('h')" @keydown.enter="tog('h')">持有人{{ ind('h') }}</th>
              <th tabindex="0" role="button" title="合计仓位=所有持有人仓位之和；括号内为人均"
                  @click="tog('tp')" @keydown.enter="tog('tp')">合计仓位{{ ind('tp') }}</th>
              <th tabindex="0" role="button" @click="tog('ap')" @keydown.enter="tog('ap')">平均盈亏{{ ind('ap') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in sortedStats.slice(0, 20)" :key="s.code">
              <td>{{ i + 1 }}</td>
              <td>
                <button class="stock-name" @click="openStockDetail(s.code, s.name)"
                        :title="'点击查看详情 ' + s.name" :aria-label="'查看详情 ' + s.name">{{ s.name }}</button>
                <button class="stock-copy" @click.stop="copyStockCode(s.code)" title="复制代码" :aria-label="'复制代码 ' + s.code">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                <span v-if="copiedKey === s.code" class="copied-tip">✓ 已复制</span>
              </td>
              <td style="color:var(--ink-3);">{{ s.code }}</td>
              <td>{{ s.h }}人</td>
              <td>
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, s.tp / s.h) + '%' }"></span></span>
                {{ s.tp.toFixed(0) }}%<span class="per-head">人均 {{ (s.tp / s.h).toFixed(0) }}%</span>
              </td>
              <td v-html="pctHtml(s.ap / s.h)"></td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.stock-name { cursor: pointer; text-decoration: underline; text-underline-offset: 3px; text-decoration-color: rgba(41,128,185,.45); }
.stock-name:hover { color: var(--accent); }
.stock-copy { border: none; background: none; cursor: pointer; color: var(--ink-4); padding: 0 3px; vertical-align: middle; }
.stock-copy:hover { color: var(--accent); }
.copied-tip { color: var(--down); font-size: 11px; margin-left: 4px; }
.per-head { color: var(--ink-4); font-size: 10px; margin-left: 5px; white-space: nowrap; }
.state-line { padding: 18px 0; text-align: center; font-size: 12px; color: var(--ink-3); }
.state-line.error { color: var(--down, #e74c3c); }
.state-line button { margin-left: 6px; cursor: pointer; color: inherit; background: transparent; border: 1px solid currentColor; border-radius: 3px; font-size: 11px; padding: 2px 8px; }
.info-btn {
  float: right; font-family: var(--mono, monospace); font-size: 10px;
  padding: 2px 7px; margin-left: 8px; border: 1px solid var(--line, #1d222b);
  background: transparent; color: var(--ink-3); border-radius: 3px; cursor: pointer;
}
.info-btn[aria-expanded="true"] { color: var(--accent, #ff6b35); border-color: var(--accent, #ff6b35); }
.compact-meta { margin: 4px 0 2px; font-size: 11px; color: var(--ink-3); }
.info-detail {
  margin: 8px 0 2px; padding: 8px 10px; font-size: 11px; line-height: 1.6;
  color: var(--ink-3); background: rgba(255,255,255,.03); border-radius: 4px;
}
</style>
