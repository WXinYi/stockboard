<script setup>
// 龙虎榜个股卡: 上榜记录(日期/涨幅/净买入/机构家数), KPL GetStockList 按 code 过滤
// 游资席位标签已按需求跳过(dealer 未展示); 未上榜显"近期未上榜"
import { computed } from 'vue'
import { fmtWan, fmtPct } from '../utils/fmt.js'
import '../styles/cards.css'

const props = defineProps({
  history: { type: Array, default: null },
})

const rows = computed(() => (Array.isArray(props.history) ? props.history.slice(0, 6) : []))
const UP = '#e74c3c'
const DOWN = '#27ae60'
</script>

<template>
  <section class="feat-card">
    <header class="fc-head">
      <h3>龙虎榜</h3>
      <span class="fc-tag">个股</span>
    </header>
    <div v-if="rows.length" class="lh-list">
      <div v-for="(r, i) in rows" :key="i" class="lh-row">
        <span class="lh-date">{{ r.date }}</span>
        <span class="lh-chg" :style="{ color: r.chgPct >= 0 ? UP : DOWN }">{{ fmtPct(r.chgPct) }}</span>
        <span class="lh-buy" :style="{ color: r.buyIn >= 0 ? UP : DOWN }">{{ fmtWan(r.buyIn) }}</span>
        <span class="lh-join">{{ r.joinNum }}家机构</span>
      </div>
    </div>
    <div v-else class="fc-empty">近期未上榜</div>
  </section>
</template>

<style scoped>
.lh-list { display: flex; flex-direction: column; gap: 2px; max-height: 148px; overflow-y: auto; }
.lh-row { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 4px; align-items: center; font-size: 10px; padding: 2px 0; border-bottom: 1px solid #f7f8fa; }
.lh-row:last-child { border-bottom: none; }
.lh-date { color: #999; }
.lh-chg { font-weight: 600; text-align: right; }
.lh-buy { color: #555; text-align: right; font-weight: 500; }
.lh-join { color: #999; }
</style>
