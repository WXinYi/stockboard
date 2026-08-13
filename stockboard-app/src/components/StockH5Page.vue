<script setup>
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { emQuoteUrl, thsUrl } from '../utils/eastmoney.js'

defineOptions({ name: 'StockH5Page' })

const route = useRoute()
const code = computed(() => route.params.code)
const qname = computed(() => route.query.name || '')

const src = ref('ths')   // 'ths' 同花顺 / 'em' 东财
</script>

<template>
  <div class="sh5">
    <div class="sh5-head">
      <strong class="sh5-title">{{ qname || code }}</strong>
      <span class="sh5-code">{{ code }}</span>
      <div class="sh5-src">
        <button :class="['sh5-srcbtn', { on: src === 'ths' }]" @click="src = 'ths'">同花顺</button>
        <button :class="['sh5-srcbtn', { on: src === 'em' }]" @click="src = 'em'">东财</button>
      </div>
    </div>
    <div class="sh5-body">
      <iframe v-if="src === 'ths'" :key="'ths' + code" :src="thsUrl(code)" class="sh5-frame" loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups allow-forms" />
      <iframe v-if="src === 'em'" :key="'em' + code" :src="emQuoteUrl(code)" class="sh5-frame" loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups allow-forms" />
    </div>
  </div>
</template>

<style scoped>
.sh5 { display: flex; flex-direction: column; }
.sh5-head { display: flex; align-items: center; gap: 10px; padding: 4px 2px 12px; flex-wrap: wrap; }
.sh5-title { font-size: 16px; }
.sh5-code { color: #999; font-size: 12px; }
.sh5-src { margin-left: auto; display: flex; gap: 4px; }
.sh5-srcbtn { border: none; background: #f0f2f5; font-size: 12px; padding: 5px 12px; border-radius: 8px; color: #666; cursor: pointer; }
.sh5-srcbtn.on { background: #2980b9; color: #fff; }
.sh5-body { flex: 1; min-height: calc(100vh - 220px); border: 1px solid rgba(0,0,0,.06); border-radius: 12px; overflow: hidden; background: #fff; }
.sh5-frame { width: 100%; height: calc(100vh - 220px); border: none; background: #fff; display: block; }
</style>
