<script setup>
// 吸顶报价条: 滚动时固定在顶部, 显示 名称/代码+复制/现价/涨跌幅/数据时效
// 数据与顶部报价同源(quote + crossInfo 光标联动); isTradingTime 判定交易时段前缀"已收盘"
import { ref, computed } from 'vue'
import { freshnessText } from '../utils/timeText.js'
import { isTradingTime } from '../composables/useKplApi.js'

const props = defineProps({
  quote: { type: Object, default: null },
  crossInfo: { type: Object, default: null },
  code: { type: String, default: '' },
})

const UP = '#e74c3c'
const DOWN = '#27ae60'
const copied = ref(false)
const copyFailed = ref(false)

const color = computed(() => {
  const c = props.crossInfo || props.quote
  if (!c) return '#666'
  const chg = props.crossInfo ? props.crossInfo.chg : props.quote?.change
  return chg >= 0 ? UP : DOWN
})
const price = computed(() => props.crossInfo ? props.crossInfo.close : props.quote?.price)
const chgPct = computed(() => props.crossInfo ? props.crossInfo.chgPct : props.quote?.changePct)
const timeText = computed(() => freshnessText(props.quote?.quoteTime, isTradingTime()))
const codeText = computed(() => props.quote?.code || props.code)

async function copyCode() {
  const txt = codeText.value
  try {
    await navigator.clipboard.writeText(txt)
  } catch (e) {
    // 剪贴板不可用(非 https/无权限)时不得显示"已复制"——那是假反馈
    copyFailed.value = true
    setTimeout(() => { copyFailed.value = false }, 1500)
    return
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 1500)
}
function fmt(v) {
  return typeof v === 'number' && isFinite(v) ? v.toFixed(2) : '—'
}
// 行情来源角标(2026-09-15 审计 F3): KPL 主源 vs 降级源(东财延迟/腾讯)必须可见 ——
// 降级源的价格时效与字段完整度都不同, 之前是静默降级, 用户以为看到的都是同一口径
const SRC_LABEL = { kpl: '', em: '东财延迟', tencent: '腾讯' }
const srcTag = computed(() => SRC_LABEL[props.quote?.quoteSrc] || '')
</script>

<template>
  <div class="sticky-bar">
    <div class="sb-name">
      <strong class="sb-title">{{ quote?.name || '—' }}</strong>
      <span class="sb-code">{{ codeText }}</span>
      <button class="sb-copy" :class="{ on: copied }" @click="copyCode">{{ copied ? '已复制' : (copyFailed ? '复制失败' : '复制') }}</button>
    </div>
    <div class="sb-quote">
      <span class="sb-price" :style="{ color }">{{ fmt(price) }}</span>
      <span class="sb-chg" :style="{ color }">{{ typeof chgPct === 'number' ? (chgPct >= 0 ? '+' : '') + chgPct.toFixed(2) + '%' : '—' }}</span>
    </div>
    <span v-if="timeText" class="sb-time">{{ timeText }}</span>
    <span v-if="srcTag" class="sb-time" style="color:#b06020;" :title="'行情降级来源: ' + srcTag">{{ srcTag }}</span>
  </div>
</template>

<style scoped>
.sticky-bar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, .08);
  padding: 6px 12px;
  margin-bottom: 4px;
}
.sb-name { display: flex; align-items: baseline; gap: 6px; min-width: 0; flex: 1; }
.sb-title { font-size: 14px; font-weight: 600; color: #111; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sb-code { color: #999; font-size: 11px; flex: none; }
.sb-copy { border: 1px solid #e0e3e8; background: #fff; color: #2980b9; font-size: 10px; padding: 1px 7px; border-radius: 6px; cursor: pointer; flex: none; }
.sb-copy.on { border-color: #27ae60; color: #27ae60; }
.sb-quote { display: flex; align-items: baseline; gap: 8px; flex: none; }
.sb-price { font-size: 16px; font-weight: 700; }
.sb-chg { font-size: 12px; font-weight: 600; }
.sb-time { color: #999; font-size: 11px; flex: none; }
</style>
