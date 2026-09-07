<script setup>
// 表头级轻量说明气泡: 默认不占版面, 点击 "?" 展开, 再点其它处/Esc 收起
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineProps({
  text: { type: String, default: '' },
})
const open = ref(false)
function toggle() { open.value = !open.value }
function close() { open.value = false }
function onDoc(e) {
  if (open.value && !e.target.closest('.pk-hint')) close()
}
function onKey(e) {
  if (e.key === 'Escape') close()
}
onMounted(() => {
  document.addEventListener('click', onDoc)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDoc)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <span class="pk-hint" :class="{ open }">
    <button type="button" class="pk-hint-q" aria-label="说明" aria-haspopup="dialog" :aria-expanded="open" @click.stop="toggle">?</button>
    <span v-if="open" class="pk-hint-pop" role="tooltip">{{ text }}</span>
  </span>
</template>

<style scoped>
.pk-hint { position: relative; display: inline-flex; margin-left: 3px; vertical-align: 2px; }
.pk-hint-q { width: 15px; height: 15px; border: none; border-radius: 50%; background: #e3e8ef; color: #5a6472; font-size: 10px; font-weight: 800; line-height: 1; cursor: pointer; padding: 0; display: inline-flex; align-items: center; justify-content: center; }
.pk-hint.open .pk-hint-q { background: #2980b9; color: #fff; }
.pk-hint-pop { position: absolute; top: calc(100% + 4px); left: 0; z-index: 40; width: min(248px, 68vw); background: #fff; border: 1px solid #dbe3ec; border-radius: 10px; box-shadow: 0 8px 24px rgba(20, 30, 60, .16); padding: 7px 10px; font-size: 11px; color: #4a5568; line-height: 1.7; text-align: left; font-weight: 400; }
</style>
