<script setup>
defineProps({
  count: { type: Number, default: 0 },
  posCount: { type: Number, default: 0 },
  tradeCount: { type: Number, default: 0 },
  stockCount: { type: Number, default: 0 },
})
</script>

<template>
  <div class="stats-row">
    <div class="stat-card c1"><div class="value">{{ count }}</div><div class="label">选手总数</div></div>
    <div class="stat-card c2"><div class="value">{{ posCount }}</div><div class="label">持仓记录</div></div>
    <div class="stat-card c3"><div class="value">{{ tradeCount }}</div><div class="label">调仓记录</div></div>
    <div class="stat-card c4"><div class="value">{{ stockCount }}</div><div class="label">涉及股票</div></div>
  </div>
</template>
