<script setup>
// 竞价分时卡: 09:15-09:25 价+量迷你图(KPL GetStockBid)
// 价格折线(涨停~跌停 Y 轴, 昨收参考线) + 底部累计量柱(红涨绿跌); 非竞价时段显占位
import { computed } from 'vue'
import { bidToPoints } from '../utils/bidChart.js'
import '../styles/cards.css'

const props = defineProps({
  bid: { type: Array, default: null },
  prevClose: { type: Number, default: null },
})

const hasData = computed(() => Array.isArray(props.bid) && props.bid.length > 1 && typeof props.prevClose === 'number')
const pts = computed(() => (hasData.value ? bidToPoints(props.bid, props.prevClose, 220, 64) : { line: [], bars: [] }))
const linePts = computed(() => pts.value.line.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' '))
const last = computed(() => (hasData.value ? props.bid[props.bid.length - 1] : null))
const isUp = computed(() => (last.value ? last.value.price >= props.prevClose : true))
const lineColor = computed(() => (isUp.value ? '#e74c3c' : '#27ae60'))
// 昨收参考线 y: (up-昨收)/(up-down)*(h-8) = 0.1/0.2*56 = 28
</script>

<template>
  <section class="feat-card">
    <header class="fc-head">
      <h3>竞价分时</h3>
      <span class="fc-tag">09:15-09:25</span>
    </header>
    <template v-if="hasData">
      <div class="bd-price">
        <b class="bd-now" :style="{ color: lineColor }">{{ last.price.toFixed(2) }}</b>
        <span class="bd-vol">竞价量 {{ (last.cumVol / 1e4).toFixed(1) }}万</span>
      </div>
      <svg viewBox="0 0 220 64" preserveAspectRatio="none" class="bd-svg">
        <line x1="0" y1="28" x2="220" y2="28" stroke="#eee" stroke-width="1" />
        <polyline :points="linePts" fill="none" :stroke="lineColor" stroke-width="1.5" />
        <rect v-for="(b, i) in pts.bars" :key="i" :x="b.x - 1" :y="b.y" width="1.6" :height="b.h" :fill="b.color" opacity=".55" />
      </svg>
      <div class="bd-scale"><span>09:15</span><span>09:25</span></div>
    </template>
    <div v-else class="fc-empty">非竞价时段</div>
  </section>
</template>

<style scoped>
.bd-price { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
.bd-now { font-size: 15px; font-weight: 700; }
.bd-vol { font-size: 10px; color: #999; }
.bd-svg { width: 100%; height: 64px; display: block; }
.bd-scale { display: flex; justify-content: space-between; font-size: 9px; color: #999; margin-top: 2px; }
</style>
