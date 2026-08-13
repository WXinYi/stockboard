<script setup>
import { ref } from 'vue'

const props = defineProps({ refreshing: { type: Boolean, default: false } })
const emit = defineEmits(['refresh'])

const THRESHOLD = 60      // 触发刷新的下拉距离
const MAX_PULL = 110      // 阻尼后的最大下拉距离
const RESISTANCE = 0.4    // 阻尼系数：拉 100px 手指 ≈ 40px 位移
const INDICATOR_H = 44    // 指示器展示高度（px）

const pull = ref(0)
let startY = 0
let pulling = false

function onTouchStart(e) {
  if (props.refreshing) return
  // 手指落在某个可滚动容器上且内容已滚动 → 交给列表自己滚动，避免劫持列表手势
  let el = e.target
  while (el && el !== document.documentElement && el !== document.body) {
    if (el.scrollTop > 0) return
    el = el.parentElement
  }
  if (window.scrollY > 0) return   // 页面在顶部才接管
  startY = e.touches[0].clientY
  pulling = true
}

function onTouchMove(e) {
  if (!pulling) return
  const dy = e.touches[0].clientY - startY
  if (dy <= 0) { pull.value = 0; return }              // 向上滑 → 放行原生滚动
  pull.value = Math.min(MAX_PULL, dy * RESISTANCE)
  if (pull.value > 0) e.preventDefault()               // 向下拉 → 拦截页面滚动
}

function onTouchEnd() {
  if (!pulling) return
  pulling = false
  if (pull.value >= THRESHOLD) emit('refresh')
  pull.value = 0
}
</script>

<template>
  <div class="ptr"
       @touchstart="onTouchStart" @touchmove="onTouchMove"
       @touchend="onTouchEnd" @touchcancel="onTouchEnd">
    <div class="ptr-indicator" :class="{ show: pull > 0 || refreshing }">
      <span v-if="refreshing" class="ptr-spinner"></span>
      <span v-else>{{ pull >= THRESHOLD ? '释放刷新' : '下拉刷新' }}</span>
    </div>
    <div class="ptr-content" :style="pull ? { transform: `translateY(${pull}px)` } : null">
      <slot />
    </div>
  </div>
</template>
