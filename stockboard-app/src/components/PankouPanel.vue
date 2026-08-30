<script setup>
// 右侧五档盘口面板: 卖5→卖1(红) / 现价 / 买1→买5(绿) + 委比委差 + 外内盘 + 涨跌停 + 换手量比
// 数据源: quote.pankou(Task 4, loadTencentPankou 5s 轮询); 无数据显占位不裸报错
import { computed } from 'vue'
import { calcWeiBi, fmtHand } from '../utils/pankou.js'

const props = defineProps({
  code: { type: String, default: '' },
  quote: { type: Object, default: null },
})

const UP = '#e74c3c'
const DOWN = '#27ae60'

const pk = computed(() => props.quote?.pankou || null)
const wei = computed(() => calcWeiBi(pk.value))
const maxVol = computed(() => Math.max(
  ...(pk.value?.buy || []).map(b => b.vol || 0),
  ...(pk.value?.sell || []).map(s => s.vol || 0),
  1,
))
const curChg = computed(() => {
  if (!pk.value || !pk.value.prevClose) return null
  return (pk.value.price - pk.value.prevClose) / pk.value.prevClose * 100
})

function px(v) { return typeof v === 'number' ? v.toFixed(2) : '—' }
function pct(v) { return typeof v === 'number' ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—' }
// 平盘(price==prevClose)惯例显灰, 不并入红涨
function curColor(pk) { return pk.price > pk.prevClose ? UP : pk.price < pk.prevClose ? DOWN : '#888' }
function barStyle(vol, color) {
  const pctw = (vol / maxVol.value) * 100
  return { background: `linear-gradient(90deg, ${color}1f 0%, ${color}1f ${pctw}%, transparent ${pctw}%)` }
}
</script>

<template>
  <aside class="pk">
    <div v-if="pk" class="pk-body">
      <!-- 委比/委差 -->
      <div class="pk-wei">
        <span class="pk-wei-item">委比 <b :style="{ color: wei.weiBi >= 0 ? UP : DOWN }">{{ pct(wei.weiBi) }}</b></span>
        <span class="pk-wei-item">委差 <b :style="{ color: wei.weiCha >= 0 ? UP : DOWN }">{{ fmtHand(wei.weiCha) }}</b></span>
      </div>

      <!-- 卖5→卖1 -->
      <div v-for="(s, i) in pk.sell" :key="'s' + i" class="pk-row" :style="barStyle(s.vol, UP)">
        <span class="pk-lvl" :style="{ color: UP }">卖{{ 5 - i }}</span>
        <span class="pk-px" :style="{ color: UP }">{{ px(s.px) }}</span>
        <span class="pk-vol">{{ fmtHand(s.vol) }}</span>
      </div>

      <!-- 现价 -->
      <div class="pk-cur">
        <span class="pk-cur-px" :style="{ color: curColor(pk) }">{{ px(pk.price) }}</span>
        <span class="pk-cur-chg" :style="{ color: curColor(pk) }">{{ pct(curChg) }}</span>
      </div>

      <!-- 买1→买5 -->
      <div v-for="(b, i) in pk.buy" :key="'b' + i" class="pk-row" :style="barStyle(b.vol, DOWN)">
        <span class="pk-lvl" :style="{ color: DOWN }">买{{ i + 1 }}</span>
        <span class="pk-px" :style="{ color: DOWN }">{{ px(b.px) }}</span>
        <span class="pk-vol">{{ fmtHand(b.vol) }}</span>
      </div>

      <!-- 外盘内盘/涨跌停/换手量比 -->
      <div class="pk-foot">
        <div class="pk-foot-row"><span>外盘 <b>{{ fmtHand(pk.outer) }}</b></span><span>内盘 <b>{{ fmtHand(pk.inner) }}</b></span></div>
        <div class="pk-foot-row"><span>涨停 <b :style="{ color: UP }">{{ px(pk.upPx) }}</b></span><span>跌停 <b :style="{ color: DOWN }">{{ px(pk.downPx) }}</b></span></div>
        <div class="pk-foot-row"><span>换手 <b>{{ pk.turnover }}%</b></span><span>量比 <b>{{ typeof pk.volumeRatio === 'number' ? pk.volumeRatio.toFixed(2) : '—' }}</b></span></div>
      </div>
    </div>
    <div v-else class="pk-empty">暂无盘口数据</div>
  </aside>
</template>

<style scoped>
.pk { display: flex; flex-direction: column; min-width: 0; }
.pk-body { display: flex; flex-direction: column; gap: 1px; font-size: 11px; }
.pk-row { display: flex; align-items: center; gap: 4px; border-radius: 3px; padding: 1px 4px; }
.pk-lvl { width: 28px; flex: none; font-size: 10px; }
.pk-px { width: 52px; flex: none; font-weight: 500; }
.pk-vol { flex: 1; text-align: right; color: #666; }
.pk-cur { display: flex; align-items: baseline; justify-content: space-between; border-radius: 4px; background: #f2f6fb; padding: 3px 6px; margin: 2px 0; }
.pk-cur-px { font-size: 13px; font-weight: 700; }
.pk-cur-chg { font-size: 10px; font-weight: 600; }
.pk-wei { display: flex; justify-content: space-between; margin-bottom: 2px; color: #666; font-size: 10px; }
.pk-wei-item b { font-weight: 600; }
.pk-foot { margin-top: 4px; padding-top: 4px; border-top: 1px dashed #e5e5e5; display: flex; flex-direction: column; gap: 2px; color: #888; font-size: 10px; }
.pk-foot-row { display: flex; justify-content: space-between; }
.pk-foot-row b { color: #555; font-weight: 500; }
.pk-empty { padding: 24px 0; text-align: center; color: #bbb; font-size: 11px; }
/* 移动端面板 116px(内容 100px): 默认列宽 28+52 只给量列剩 12px 会换行/溢出 → 压缩列宽保住挂单量 */
@media (max-width: 480px) {
  .pk-lvl { width: 20px; font-size: 9px; }
  .pk-px { width: 42px; }
  .pk-vol { font-size: 10px; }
}
</style>
