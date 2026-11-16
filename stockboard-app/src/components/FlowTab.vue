<script setup>
import { computed, inject, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useCopyCode } from '../composables/useCopyCode.js'

const router = useRouter()
const { tradeConsensus } = inject('stockData')

const consensus = computed(() => tradeConsensus.value || { totalTrades: 0, totalBuyers: 0, stocks: [] })
const rows = computed(() => consensus.value.stocks || [])
const { copiedKey, copyStockCode } = useCopyCode()

// 视图切换：净买入 / 买入人数 / 卖出人数（默认净买入 = 看大家往哪换仓）
const view = ref('net')
const viewRows = computed(() => {
  const list = [...rows.value]
  if (view.value === 'buy') list.sort((a, b) => b.buy - a.buy || b.net - a.net)
  else if (view.value === 'sell') list.sort((a, b) => b.sell - a.sell || a.net - b.net)
  else list.sort((a, b) => b.net - a.net || b.buy - a.buy)
  return list
})

function openStockDetail(c, n) { router.push({ path: '/stock/' + c, query: { name: n } }) }
function netHtml(n) {
  if (n > 0) return `<span class="up">+${n}</span>`
  if (n < 0) return `<span class="down">${n}</span>`
  return '<span class="flat">0</span>'
}
</script>

<template>
  <div class="card">
    <h2>调仓共识说明</h2>
    <p class="hint">
      统计全部选手当日买卖：买入/卖出人数按选手去重，一人多次只算一人。点击表头或切换排序查看。
      这是收盘后采集的公开组合调仓，属滞后信息，仅作资金关注度参考，<strong>不构成买卖依据</strong>。
    </p>
  </div>

  <div class="card">
    <h2>
      调仓共识
      <span class="badge">Top 20</span>
    </h2>
    <p class="hint">
      当日 {{ consensus.totalTrades }} 笔调仓 · {{ consensus.totalBuyers }} 人买入 ·
      数据为最近一次采集快照（盘中不刷新，收盘后更新当日）
    </p>

    <div class="tabs" role="tablist" aria-label="调仓排序">
      <button :class="{ active: view === 'net' }" @click="view = 'net'">净买入</button>
      <button :class="{ active: view === 'buy' }" @click="view = 'buy'">买入人数</button>
      <button :class="{ active: view === 'sell' }" @click="view = 'sell'">卖出人数</button>
    </div>

    <div v-if="!viewRows.length" class="empty">
      当日暂无调仓共识数据（可能为休市、或采集尚未完成）。
    </div>
    <div v-else>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>股票</th>
            <th>代码</th>
            <th>买入</th>
            <th>卖出</th>
            <th>净买入</th>
            <th>末次</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(s, i) in viewRows.slice(0, 20)" :key="s.code">
            <td>{{ i + 1 }}</td>
            <td>
              <strong class="stock-name" @click="openStockDetail(s.code, s.name)" :title="'查看详情 ' + s.name">{{ s.name }}</strong>
              <button class="stock-copy" @click.stop="copyStockCode(s.code)" title="复制代码" aria-label="复制代码">
                <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M13 3H5a2 2 0 0 0-2 2v10"/></svg>
              </button>
              <span v-if="copiedKey === s.code" class="copied-tip">✓</span>
            </td>
            <td style="color:var(--ink-3);">{{ s.code }}</td>
            <td class="up">{{ s.buy }}人<span class="sub">/{{ s.buyTrades }}笔</span></td>
            <td class="down">{{ s.sell }}人<span class="sub">/{{ s.sellTrades }}笔</span></td>
            <td v-html="netHtml(s.net)"></td>
            <td style="color:var(--ink-3);font-size:11px;">{{ s.lastTime || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
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
.tabs { display: flex; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
.tabs button {
  font-family: var(--mono, monospace); font-size: 11px;
  padding: 5px 10px; border: 1px solid var(--line, #1d222b);
  background: transparent; color: var(--ink-3); border-radius: 4px; cursor: pointer;
}
.tabs button.active { color: var(--accent, #ff6b35); border-color: var(--accent, #ff6b35); }
.empty { padding: 28px 8px; text-align: center; color: var(--ink-3); font-size: 13px; }
</style>
