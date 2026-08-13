<script setup>
// 历史涨停卡: 最近涨停原因文本 + 关联板块 chips(KPL GetDayZhangTing, 仅当日涨停由父级 isLimitUp 控制显隐)
// 数据源与详情页原有 涨停原因块 一致(limitReason), 并入功能卡区第二行
import { ref } from 'vue'
import '../styles/cards.css'

const props = defineProps({
  reason: { type: Object, default: null },
  boardMap: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['go-board'])

const limitMore = ref(false)
function firstLine(s) {
  if (!s) return ''
  const i = s.indexOf('\n')
  return i > 0 ? s.slice(0, i) : s
}
function go(code) { emit('go-board', code, props.boardMap[code] || code) }
</script>

<template>
  <section class="feat-card">
    <header class="fc-head">
      <h3>📌 涨停原因</h3>
      <span class="fc-tag">当日</span>
    </header>
    <div v-if="reason">
      <div class="zt-head">
        <button v-if="reason.reason && reason.reason.length > 60" class="zt-toggle" @click="limitMore = !limitMore">{{ limitMore ? '收起 ▴' : '更多 ▾' }}</button>
      </div>
      <p class="zt-text">{{ limitMore ? reason.reason : firstLine(reason.reason) }}</p>
      <div v-if="reason.zsCodes && reason.zsCodes.length" class="zt-chips">
        <span v-for="c in reason.zsCodes" :key="c" class="zt-chip" @click="go(c)">{{ boardMap[c] || c }}</span>
      </div>
    </div>
    <div v-else class="fc-empty">非涨停或无涨停原因</div>
  </section>
</template>

<style scoped>
.zt-head { display: flex; justify-content: flex-end; margin-bottom: 2px; }
.zt-toggle { border: none; background: none; color: #2980b9; font-size: 11px; cursor: pointer; }
.zt-text { font-size: 12px; color: #555; line-height: 1.7; margin: 0 0 8px; }
.zt-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.zt-chip { background: #fdecea; color: #c0392b; font-size: 11px; padding: 2px 8px; border-radius: 20px; cursor: pointer; }
</style>
