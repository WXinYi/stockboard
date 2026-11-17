<script setup>
import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useCopyCode } from '../composables/useCopyCode.js'
import { onlyAStock } from '../utils/stockMarket.js'
import { snapshotLabel } from '../utils/formatBeijing.js'

const router = useRouter()
const stockData = inject('stockData')
const { tradeConsensusA, stocksSnapshot, sliceLoaded, sliceErrors, loading } = stockData
const refresh = inject('refreshData', () => {})

const consensus = computed(() => tradeConsensusA.value || { totalTrades: 0, totalBuyers: 0, stocks: [] })
const scopeLabel = computed(() => consensus.value.aTotalTrades != null ? 'A股口径' : '全市场口径')
const snapshotText = computed(() => snapshotLabel(stocksSnapshot.value) || '快照时点未知')
const failed = computed(() => !!sliceErrors.value.today)
const showLoading = computed(() => loading.value.today || !sliceLoaded.value.today)

// 只看 A 股，港股（5 位代码）滤除；统计由数据层 aTotalTrades/aTotalBuyers 提供
const rows = computed(() => onlyAStock(consensus.value.stocks))
const { copiedKey, copyStockCode } = useCopyCode()

const view = ref('net')
const showInfo = ref(false)
const viewRows = computed(() => {
  const list = [...rows.value]
  if (view.value === 'buy') list.sort((a, b) => b.buy - a.buy || b.net - a.net)
  else if (view.value === 'sell') list.sort((a, b) => b.sell - a.sell || a.net - b.net)
  else list.sort((a, b) => b.net - a.net || b.buy - a.buy)
  return list
})

function openStockDetail(c, n) { router.push({ path: '/stock/' + c, query: { name: n } }) }
</script>

<template>
  <div class="card">
    <h2>
      调仓共识
      <span class="badge">A股 Top 20</span>
      <button class="info-btn" @click="showInfo = !showInfo" :aria-expanded="showInfo"
              aria-controls="flow-info" aria-label="查看调仓共识说明">说明</button>
    </h2>
    <p class="compact-meta">
      {{ scopeLabel }} {{ consensus.totalTrades }} 笔调仓 · {{ consensus.totalBuyers }} 人买入 · {{ snapshotText }}
      <span class="warn">非买卖依据</span>
    </p>
    <p v-if="showInfo" id="flow-info" class="info-detail">
      统计当日买卖，仅展示 A 股（港股已滤除）；买入/卖出人数按选手去重，一人多次只算一人。
      数据为公开组合调仓快照，属滞后信息，仅作资金关注度参考，<strong>不构成买卖依据</strong>。
    </p>

    <div v-if="showLoading" class="state-line">正在读取调仓共识…</div>
    <div v-else-if="failed" class="state-line error">
      调仓数据加载失败，<button type="button" @click="refresh()">重试</button>
    </div>
    <template v-else>
      <div class="tabs" role="tablist" aria-label="调仓排序">
        <button role="tab" :aria-selected="view === 'net'" :class="{ active: view === 'net' }" @click="view = 'net'">净买入</button>
        <button role="tab" :aria-selected="view === 'buy'" :class="{ active: view === 'buy' }" @click="view = 'buy'">买入人数</button>
        <button role="tab" :aria-selected="view === 'sell'" :class="{ active: view === 'sell' }" @click="view = 'sell'">卖出人数</button>
      </div>

      <div v-if="!viewRows.length" class="empty">当日暂无 A 股调仓共识数据。</div>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>股票</th><th>代码</th>
              <th>买入</th><th>卖出</th><th>净买入</th><th>末次</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(s, i) in viewRows.slice(0, 20)" :key="s.code">
              <td>{{ i + 1 }}</td>
              <td>
                <button class="stock-name" @click="openStockDetail(s.code, s.name)"
                        :title="'查看详情 ' + s.name" :aria-label="'查看详情 ' + s.name">{{ s.name }}</button>
                <button class="stock-copy" @click.stop="copyStockCode(s.code)" title="复制代码" :aria-label="'复制代码 ' + s.code">
                  <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M13 3H5a2 2 0 0 0-2 2v10"/></svg>
                </button>
                <span v-if="copiedKey === s.code" class="copied-tip">✓</span>
              </td>
              <td style="color:var(--ink-3);">{{ s.code }}</td>
              <td class="up">{{ s.buy }}人<span class="sub">/{{ s.buyTrades }}笔</span></td>
              <td class="down">{{ s.sell }}人<span class="sub">/{{ s.sellTrades }}笔</span></td>
              <td>
                <span v-if="s.net > 0" class="up">+{{ s.net }}</span>
                <span v-else-if="s.net < 0" class="down">{{ s.net }}</span>
                <span v-else class="flat">0</span>
              </td>
              <td style="color:var(--ink-3);font-size:11px;">{{ s.lastTime || '—' }}</td>
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
.up { color: var(--up, #e74c3c); }
.down { color: var(--down, #27ae60); }
.flat { color: var(--ink-3); }
.sub { color: var(--ink-4); font-size: 10px; }
.state-line { padding: 18px 0; text-align: center; font-size: 12px; color: var(--ink-3); }
.state-line.error { color: var(--down, #e74c3c); }
.state-line button { margin-left: 6px; cursor: pointer; color: inherit; background: transparent; border: 1px solid currentColor; border-radius: 3px; font-size: 11px; padding: 2px 8px; }
.tabs { display: flex; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
.tabs button {
  font-family: var(--mono, monospace); font-size: 11px;
  padding: 5px 10px; border: 1px solid var(--line, #1d222b);
  background: transparent; color: var(--ink-3); border-radius: 4px; cursor: pointer;
}
.tabs button.active { color: var(--accent, #ff6b35); border-color: var(--accent, #ff6b35); }
.info-btn {
  float: right; font-family: var(--mono, monospace); font-size: 10px;
  padding: 2px 7px; margin-left: 8px; border: 1px solid var(--line, #1d222b);
  background: transparent; color: var(--ink-3); border-radius: 3px; cursor: pointer;
}
.info-btn[aria-expanded="true"] { color: var(--accent, #ff6b35); border-color: var(--accent, #ff6b35); }
.compact-meta { margin: 4px 0 2px; font-size: 11px; color: var(--ink-3); }
.compact-meta .warn { color: var(--down, #e74c3c); margin-left: 6px; }
.info-detail {
  margin: 8px 0 2px; padding: 8px 10px; font-size: 11px; line-height: 1.6;
  color: var(--ink-3); background: rgba(255,255,255,.03); border-radius: 4px;
}
</style>
