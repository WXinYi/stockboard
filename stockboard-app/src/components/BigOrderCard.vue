<script setup>
// 大单监控卡: 逐笔大单(时间/价/方向红买绿卖/手数/金额/超大·大·中标签), KPL GetMainMonitor_w30
// 空态显"暂无大单数据"; 默认显示最近 8 条
import { computed } from 'vue'
import { fmtHand } from '../utils/pankou.js'
import { fmtWan } from '../utils/fmt.js'
import '../styles/cards.css'

const props = defineProps({
  orders: { type: Array, default: null },
})

const rows = computed(() => (Array.isArray(props.orders) ? props.orders.slice(0, 8) : []))
const UP = '#e74c3c'
const DOWN = '#27ae60'
</script>

<template>
  <section class="feat-card">
    <header class="fc-head">
      <h3>大单监控</h3>
      <span class="fc-tag">100万+</span>
    </header>
    <div v-if="rows.length" class="bo-list">
      <div v-for="(o, i) in rows" :key="i" class="bo-row">
        <span class="bo-time">{{ o.time }}</span>
        <span class="bo-px">{{ typeof o.price === 'number' ? o.price.toFixed(2) : '—' }}</span>
        <span class="bo-side" :style="{ color: o.side === '买' ? UP : DOWN }">{{ o.side }}</span>
        <span class="bo-vol">{{ fmtHand(o.vol) }}</span>
        <span class="bo-amt">{{ fmtWan(o.amount) }}</span>
        <span v-if="o.type" class="bo-tag" :class="o.type === '超大' ? 'xl' : 'md'">{{ o.type }}</span>
      </div>
    </div>
    <div v-else class="fc-empty">暂无大单数据</div>
  </section>
</template>

<style scoped>
.bo-list { display: flex; flex-direction: column; gap: 2px; max-height: 148px; overflow-y: auto; }
.bo-row { display: grid; grid-template-columns: 38px 1fr 22px 1fr 1fr auto; gap: 4px; align-items: center; font-size: 10px; padding: 2px 0; border-bottom: 1px solid #f7f8fa; }
.bo-row:last-child { border-bottom: none; }
.bo-time { color: #999; }
.bo-px { color: #333; font-weight: 500; text-align: right; }
.bo-side { font-weight: 600; text-align: center; }
.bo-vol { color: #666; text-align: right; }
.bo-amt { color: #555; text-align: right; }
.bo-tag { font-size: 8px; border-radius: 6px; padding: 0 4px; flex: none; }
.bo-tag.xl { background: #fdecea; color: #c0392b; }
.bo-tag.md { background: #eef3ff; color: #2980b9; }
</style>
