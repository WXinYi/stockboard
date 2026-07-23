<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'

const primary = [
  { key: 'copy', label: '💡 抄作业' },
  { key: 'rankings', label: '🏆 排行' },
  { key: 'trades', label: '🤝 调仓' },
]
const secondary = [
  { key: 'overview', label: '📋 总览' },
  { key: 'stocks', label: '📈 重仓共识' },
  { key: 'sectors', label: '🏭 行业板块' },
  { key: 'compare', label: '📊 多空对比' },
  { key: 'tracking', label: '📅 变动追踪' },
]
const showMore = ref(false)
const route = useRoute()

function isActive(key) { return route.path === '/' + key }
</script>

<template>
  <nav class="nav-bar">
    <!-- Primary tabs (all screens) -->
    <router-link v-for="t in primary" :key="t.key" :to="'/' + t.key"
      class="nav-item" :class="{ active: isActive(t.key) }">
      <span class="nav-label">{{ t.label }}</span>
    </router-link>

    <!-- More button (mobile) / Secondary tabs (PC) -->
    <div class="more-wrap">
      <button class="nav-item more-btn" :class="{ active: secondary.some(s => isActive(s.key)) }"
        @click.stop="showMore = !showMore">
        <span class="nav-label">⋯</span>
      </button>
      <Teleport to="body">
        <div v-if="showMore" class="more-overlay" @click="showMore = false">
          <div class="more-menu" @click.stop>
            <router-link v-for="t in secondary" :key="t.key" :to="'/' + t.key"
              class="more-item" :class="{ active: isActive(t.key) }"
              @click="showMore = false">
              {{ t.label }}
            </router-link>
          </div>
        </div>
      </Teleport>
    </div>

    <!-- PC only: secondary tabs inline -->
    <template v-for="t in secondary" :key="'pc'+t.key">
      <router-link :to="'/' + t.key" class="nav-item nav-pc"
        :class="{ active: isActive(t.key) }">
        <span class="nav-label">{{ t.label }}</span>
      </router-link>
    </template>
  </nav>
</template>

<style scoped>
.nav-bar { display: flex; gap: 2px; padding: 4px 12px; background: #fff; border-bottom: 1px solid #e8e8e8; overflow-x: auto; -webkit-overflow-scrolling: touch; flex-shrink: 0; position: relative; }
.nav-item { flex-shrink: 0; padding: 8px 14px; font-size: 12px; border-radius: 8px; text-decoration: none; color: #666; transition: all 0.2s; white-space: nowrap; border: none; background: none; cursor: pointer; font-family: inherit; }
.nav-item:hover { background: #f0f4ff; color: #2980b9; }
.nav-item.active { background: #2980b9; color: #fff; font-weight: 600; }
.nav-label { pointer-events: none; }
.nav-pc { display: none; }
.more-wrap { flex-shrink: 0; }
.more-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.3); z-index: 999; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 60px; }
.more-menu { background: #fff; border-radius: 14px 14px 0 0; box-shadow: 0 -4px 20px rgba(0,0,0,.15); padding: 8px 4px 20px; width: 100%; max-width: 400px; }
.more-item { display: block; padding: 14px 20px; font-size: 15px; text-decoration: none; color: #333; border-radius: 8px; white-space: nowrap; text-align: center; }
.more-item:hover { background: #f0f4ff; color: #2980b9; }
.more-item.active { background: #e8f4fd; color: #2980b9; font-weight: 600; }

@media (max-width: 767px) {
  .nav-bar { position: fixed; bottom: 0; left: 0; right: 0; z-index: 100; border-bottom: none; border-top: 1px solid #e8e8e8; padding: 2px 4px 4px; justify-content: space-around; gap: 0; }
  .nav-item { padding: 6px 10px; font-size: 11px; border-radius: 6px; }
}
@media (min-width: 768px) {
  .nav-pc { display: block; }
  .more-wrap { display: none; }
}
</style>
