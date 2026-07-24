<script setup>
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { icons } from '../icons.js'

const primary = [
  { key: 'copy', label: '抄作业', icon: 'copy' },
  { key: 'rankings', label: '排行', icon: 'rankings' },
  { key: 'trades', label: '调仓', icon: 'trades' },
]
const secondary = [
  { key: 'overview', label: '总览', icon: 'overview' },
  { key: 'stocks', label: '重仓共识', icon: 'stocks' },
  { key: 'sectors', label: '行业板块', icon: 'sectors' },
  { key: 'compare', label: '多空对比', icon: 'compare' },
  { key: 'tracking', label: '变动追踪', icon: 'tracking' },
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
      <span class="nav-icon" v-html="icons[t.icon]"></span>
      <span class="nav-label">{{ t.label }}</span>
    </router-link>

    <!-- More button (mobile) / Secondary tabs (PC) -->
    <div class="more-wrap">
      <button class="nav-item more-btn" :class="{ active: secondary.some(s => isActive(s.key)) }"
        @click.stop="showMore = !showMore">
        <span class="nav-icon" v-html="icons.more"></span>
        <span class="nav-label">更多</span>
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
        <span class="nav-icon" v-html="icons[t.icon]"></span>
        <span class="nav-label">{{ t.label }}</span>
      </router-link>
    </template>
  </nav>
</template>

<style scoped>
/* ---- Base Bar ---- */
.nav-bar {
  display: flex; gap: 2px; padding: 4px 14px;
  background: rgba(255,255,255,.36);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border-bottom: 0.5px solid rgba(255,255,255,.25);
  overflow-x: auto; -webkit-overflow-scrolling: touch;
  flex-shrink: 0; position: relative;
}
.nav-bar::before {
  content: ''; position: absolute; top: 0; left: 16px; right: 16px; height: 0.5px;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.5) 20%, rgba(255,255,255,.5) 80%, transparent);
  pointer-events: none; z-index: 2;
}

/* ---- Tab ---- */
.nav-item {
  flex-shrink: 0; padding: 8px 16px; font-size: 12px; font-weight: 400; border-radius: 100px;
  text-decoration: none; color: #8e8e9a; transition: all .35s cubic-bezier(.34,1.56,.64,1);
  white-space: nowrap; border: none; background: none; cursor: pointer;
  font-family: inherit; position: relative; z-index: 1;
  outline: none; -webkit-tap-highlight-color: transparent;
}
.nav-item:hover { color: #5b6daa; }
.nav-icon { display: flex; align-items: center; justify-content: center; width: 20px; height: 20px; opacity: .55; transition: opacity .3s; }
.nav-item.active .nav-icon { opacity: 1; }
.nav-label { pointer-events: none; font-size: 11px; }
.nav-pc { display: none; }

/* Active: glowing droplet */
.nav-item.active {
  color: #5b6daa; font-weight: 520;
  background: radial-gradient(ellipse at 50% 130%, rgba(107,125,179,.14) 0%, rgba(107,125,179,.04) 60%, transparent 80%);
  box-shadow: 0 -3px 14px rgba(107,125,179,.06), 0 4px 8px rgba(107,125,179,.03);
  transform: scale(1.04);
}

/* ---- More ---- */
.more-wrap { flex-shrink: 0; }
.more-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.15); backdrop-filter: blur(3px); z-index: 999; display: flex; align-items: flex-end; justify-content: center; padding-bottom: 60px; }
.more-menu { background: rgba(255,255,255,.82); backdrop-filter: blur(30px); -webkit-backdrop-filter: blur(30px); border-radius: 22px 22px 0 0; box-shadow: 0 -10px 36px rgba(0,0,0,.06); padding: 8px 4px 28px; width: 100%; max-width: 400px; }
.more-item { display: block; padding: 15px 20px; font-size: 15px; font-weight: 380; text-decoration: none; color: #1a1a2e; border-radius: 12px; white-space: nowrap; text-align: center; transition: background .2s; }
.more-item:hover { background: rgba(107,125,179,.04); }
.more-item.active { background: rgba(107,125,179,.08); color: #5b6daa; font-weight: 500; }

/* ---- Mobile ---- */
@media (max-width: 767px) {
  .nav-bar {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 100; border-bottom: none;
    border-top: 0.5px solid rgba(255,255,255,.2);
    padding: 4px 8px; padding-bottom: max(4px, env(safe-area-inset-bottom));
    justify-content: space-around; gap: 0;
    background: rgba(245,242,238,.7);
    backdrop-filter: blur(30px) saturate(200%);
    -webkit-backdrop-filter: blur(30px) saturate(200%);
    box-shadow: 0 -0.5px 0 rgba(255,255,255,.3) inset;
  }
  .nav-bar::before {
    left: 20px; right: 20px;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,.6) 20%, rgba(255,255,255,.6) 80%, transparent);
  }
  .nav-item {
    flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 8px 4px 6px; font-size: 11px; border-radius: 16px; min-height: 48px;
    transition: all .4s cubic-bezier(.34,1.56,.64,1);
  }
  .nav-item.active {
    color: #5b6daa; font-weight: 550;
    background: radial-gradient(ellipse at 50% 140%, rgba(107,125,179,.16) 0%, rgba(107,125,179,.05) 60%, transparent 85%);
    box-shadow: 0 -4px 16px rgba(107,125,179,.08), 0 6px 10px rgba(107,125,179,.03);
    transform: translateY(-2px) scale(1.05);
  }
  .nav-pc { display: none !important; }
  .nav-label { font-size: 10px; line-height: 1; }
  .more-btn { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 48px; }
  .more-overlay { padding-bottom: 80px; }
}
@media (min-width: 768px) {
  .nav-pc { display: block; }
  .more-wrap { display: none; }
}
</style>
