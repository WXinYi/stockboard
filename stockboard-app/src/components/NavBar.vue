<script setup>
import { useRoute } from 'vue-router'
import { icons } from '../icons.js'

// 一级入口。抄作业(/copy)按用户决定隐藏(2026-09-15): 不设导航入口, 页面保留直达,
// 需要时直接在地址栏访问 /#/copy 即可 —— 若要恢复入口, 在这里加回 copy 一项即可。
const primary = [
  { key: 'market', label: '选股', icon: 'market' },
  { key: 'rankings', label: '排行', icon: 'rankings' },
  { key: 'stocks', label: '重仓共识', icon: 'stocks' },
]
const route = useRoute()

function isActive(key) { return route.path === '/' + key }
</script>

<template>
  <nav class="nav-bar">
    <router-link v-for="(t, i) in primary" :key="t.key" :to="'/' + t.key"
      class="nav-item" :class="{ active: isActive(t.key) }"
      :style="{ '--idx': i }">
      <span class="nav-icon" v-html="icons[t.icon]"></span>
      <span class="nav-label">{{ t.label }}</span>
      <span class="nav-scan" aria-hidden="true"></span>
    </router-link>
  </nav>
</template>

<style scoped>
/* ── 终端导航：竖线指示 + 扫描光带 ─────────────────────────── */
.nav-bar {
  display: flex; gap: 0; padding: 0 8px;
  background: rgba(21, 24, 30, .88);
  backdrop-filter: blur(24px) saturate(150%);
  -webkit-backdrop-filter: blur(24px) saturate(150%);
  border-bottom: 1px solid var(--line, #1d222b);
  flex-shrink: 0; position: relative; z-index: 60;
}

.nav-item {
  flex: 1; position: relative;
  display: flex; flex-direction: column; align-items: center; gap: 5px;
  padding: 10px 4px 9px;
  color: var(--ink-3, #5d6675);
  text-decoration: none;
  transition: color .22s ease;
  -webkit-tap-highlight-color: transparent;
}
.nav-item:hover { color: var(--ink-2, #98a1b1); }

.nav-icon {
  display: flex; align-items: center; justify-content: center;
  width: 18px; height: 18px;
  opacity: .55; transition: opacity .22s ease, transform .22s ease;
}
.nav-label {
  font-family: var(--mono, monospace);
  font-size: 9px; letter-spacing: .14em; line-height: 1;
  pointer-events: none;
}

/* 顶部指示竖线 */
.nav-item.active { color: var(--ink, #e7eaf0); }
.nav-item.active .nav-icon { opacity: 1; color: var(--accent, #ff6b35); transform: translateY(-1px); }
.nav-item.active::before {
  content: '';
  position: absolute; top: 0; left: 50%; transform: translateX(-50%);
  width: 24px; height: 2px;
  background: var(--accent, #ff6b35);
  box-shadow: 0 0 12px rgba(255, 107, 53, .85);
}

/* 扫描光带：只在 active 时出现，横向缓扫 */
.nav-scan {
  position: absolute; inset: 0; overflow: hidden;
  pointer-events: none; opacity: 0;
}
.nav-item.active .nav-scan {
  opacity: 1;
  background: linear-gradient(180deg, rgba(255,107,53,.10) 0%, transparent 70%);
}

@media (max-width: 767px) {
  .nav-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    border-bottom: none; border-top: 1px solid var(--line, #1d222b);
    padding-bottom: max(2px, env(safe-area-inset-bottom));
    justify-content: space-around;
  }
  .nav-item { padding: 9px 4px 7px; min-height: 50px; }
  .nav-label { font-size: 9px; }
}
</style>
