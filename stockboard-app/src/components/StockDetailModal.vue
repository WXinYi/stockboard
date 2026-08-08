<script setup>
import { ref } from 'vue'
import { emQuoteUrl, thsUrl } from '../utils/eastmoney.js'

defineProps({
  code: { type: String, required: true },
  name: { type: String, default: '' },
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const src = ref('ths')  // 'ths' 同花顺 / 'em' 东财

function close() { emit('update:modelValue', false) }
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="sm-mask" @click.self="close">
      <div class="sm-panel">
        <div class="sm-head">
          <strong class="sm-title">{{ name || code }}</strong>
          <span class="sm-code">{{ code }}</span>
          <div class="sm-src">
            <button :class="['sm-srcbtn', { on: src === 'ths' }]" @click="src = 'ths'">同花顺</button>
            <button :class="['sm-srcbtn', { on: src === 'em' }]" @click="src = 'em'">东财</button>
          </div>
          <button class="sm-close" @click="close">×</button>
        </div>
        <div class="sm-body">
          <iframe v-if="src === 'ths'" :key="'ths' + code" :src="thsUrl(code)" class="sm-frame" loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups allow-forms" />
          <iframe v-if="src === 'em'" :key="'em' + code" :src="emQuoteUrl(code)" class="sm-frame" loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups allow-forms" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.sm-mask { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 999; display: flex; align-items: center; justify-content: center; padding: 8px; }
.sm-panel { width: min(760px, 100%); height: 92vh; background: #fff; border-radius: 14px; display: flex; flex-direction: column; overflow: hidden; }
.sm-head { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid rgba(0,0,0,.06); flex-wrap: wrap; }
.sm-title { font-size: 16px; }
.sm-code { color: #999; font-size: 12px; }
.sm-src { margin-left: auto; display: flex; gap: 4px; }
.sm-srcbtn { border: none; background: #f0f2f5; font-size: 12px; padding: 5px 12px; border-radius: 8px; color: #666; cursor: pointer; }
.sm-srcbtn.on { background: #2980b9; color: #fff; }
.sm-close { border: none; background: none; font-size: 22px; color: #888; cursor: pointer; padding: 0 4px; }
.sm-body { flex: 1; min-height: 0; }
.sm-frame { width: 100%; height: 100%; border: none; background: #fff; }
</style>
