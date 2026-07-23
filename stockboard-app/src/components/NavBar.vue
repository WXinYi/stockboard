<script setup>
import { useRoute } from 'vue-router'

const tabs = [
  { key: 'copy', label: '💡 抄作业' },
  { key: 'overview', label: '📋 总览' },
  { key: 'rankings', label: '🏆 排行榜' },
  { key: 'stocks', label: '📈 重仓共识' },
  { key: 'sectors', label: '🏭 行业' },
  { key: 'trades', label: '🤝 调仓' },
  { key: 'compare', label: '📊 多空' },
  { key: 'tracking', label: '📅 变动' },
]

const route = useRoute()
</script>

<template>
  <nav class="nav-bar">
    <router-link
      v-for="t in tabs"
      :key="t.key"
      :to="'/' + t.key"
      class="nav-item"
      :class="{ active: route.path === '/' + t.key }"
    >
      <span class="nav-label">{{ t.label }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
.nav-bar {
  display: flex;
  gap: 2px;
  padding: 4px 12px;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  flex-shrink: 0;
}
.nav-item {
  flex-shrink: 0;
  padding: 8px 14px;
  font-size: 12px;
  border-radius: 8px;
  text-decoration: none;
  color: #666;
  transition: all 0.2s;
  white-space: nowrap;
}
.nav-item:hover { background: #f0f4ff; color: #2980b9; }
.nav-item.active { background: #2980b9; color: #fff; font-weight: 600; }
.nav-label { pointer-events: none; }

@media (max-width: 767px) {
  .nav-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    z-index: 100;
    border-bottom: none;
    border-top: 1px solid #e8e8e8;
    padding: 2px 4px 4px;
    justify-content: space-around;
    gap: 0;
  }
  .nav-item {
    flex-direction: column;
    padding: 4px 6px;
    font-size: 10px;
    border-radius: 6px;
    text-align: center;
  }
}
</style>
