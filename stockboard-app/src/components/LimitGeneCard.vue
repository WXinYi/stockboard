<script setup>
// 涨停基因卡: 涨停次数/5%溢价/次日红盘%/首板封板率%/破板率%/连板率%(KPL GetZhangTingGene)
// 空态显"暂无涨停基因数据"(非涨停股/接口空时, 不报错)
import { computed } from 'vue'
import '../styles/cards.css'

const props = defineProps({
  gene: { type: Object, default: null },
})

const items = computed(() => {
  const g = props.gene
  if (!g) return []
  const num = v => (v === null || v === undefined || v === '') ? '—' : +v
  return [
    { label: '涨停次数', value: g.ztCount ?? '—', cls: 'c-up' },
    { label: '5%溢价', value: (g.premium5 ?? '—') + (g.premium5 != null ? '次' : ''), cls: '' },
    { label: '次日红盘', value: (g.nextRedPct ?? '—') + (g.nextRedPct != null ? '%' : ''), cls: num(g.nextRedPct) >= 50 ? 'c-up' : num(g.nextRedPct) < 50 ? 'c-dn' : '' },
    { label: '首板封板率', value: (g.firstSealPct ?? '—') + (g.firstSealPct != null ? '%' : ''), cls: num(g.firstSealPct) >= 50 ? 'c-up' : '' },
    { label: '破板率', value: (g.breakPct ?? '—') + (g.breakPct != null ? '%' : ''), cls: num(g.breakPct) < 30 ? 'c-dn' : 'c-up' },
    { label: '连板率', value: (g.lianbanPct ?? '—') + (g.lianbanPct != null ? '%' : ''), cls: num(g.lianbanPct) >= 30 ? 'c-up' : '' },
  ]
})
</script>

<template>
  <section class="feat-card">
    <header class="fc-head">
      <h3>涨停基因</h3>
      <span class="fc-tag">近期</span>
    </header>
    <div v-if="items.length" class="lg-grid">
      <div v-for="it in items" :key="it.label" class="lg-cell">
        <span class="lg-lbl">{{ it.label }}</span>
        <b class="lg-val" :class="it.cls">{{ it.value }}</b>
      </div>
    </div>
    <div v-else class="fc-empty">暂无涨停基因数据</div>
  </section>
</template>

<style scoped>
.lg-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 6px; }
.lg-cell { display: flex; flex-direction: column; gap: 2px; }
.lg-lbl { font-size: 9px; color: #999; white-space: nowrap; }
.lg-val { font-size: 13px; color: #333; font-weight: 600; }
.lg-val.c-up { color: #e74c3c; }
.lg-val.c-dn { color: #27ae60; }
</style>
