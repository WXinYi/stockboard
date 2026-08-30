<script setup>
// 全局股票搜索浮层: 输入代码/名称/拼音 → 东财建议接口 → 跳股票详情页
import { ref, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { searchStock } from '../utils/stockSearch.js'

const emit = defineEmits(['close'])
const router = useRouter()
const kw = ref('')
const list = ref([])
const loading = ref(false)
const err = ref(false)
const inputRef = ref(null)
let seq = 0      // 竞态序号: 仅采纳最后一次请求的结果
let timer = null

onMounted(() => { nextTick(() => inputRef.value?.focus()) })

watch(kw, (v) => {
  clearTimeout(timer)
  const q = String(v || '').trim()
  if (!q) { list.value = []; loading.value = false; err.value = false; return }
  loading.value = true
  timer = setTimeout(async () => {
    const id = ++seq
    try {
      const rows = await searchStock(q)
      if (id !== seq) return
      list.value = rows
      err.value = false
    } catch (e) {
      if (id !== seq) return
      list.value = []
      err.value = true
    } finally {
      if (id === seq) loading.value = false
    }
  }, 250)
})

function go(s) {
  emit('close')
  router.push({ path: '/stock/' + s.code, query: { name: s.name } })
}
</script>

<template>
  <div class="ss-mask" @click.self="emit('close')">
    <div class="ss-panel">
      <div class="ss-bar">
        <input ref="inputRef" v-model="kw" class="ss-input" placeholder="代码 / 名称 / 拼音缩写，如 600519 / 茅台 / GZMT" />
        <button class="ss-cancel" @click="emit('close')">取消</button>
      </div>
      <div class="ss-body">
        <div v-if="!kw.trim()" class="ss-tip">输入代码 / 名称 / 拼音缩写搜索 A 股</div>
        <div v-else-if="loading" class="ss-tip">搜索中…</div>
        <div v-else-if="err" class="ss-tip ss-err">搜索失败，请重试</div>
        <div v-else-if="!list.length" class="ss-tip">无匹配的 A 股结果</div>
        <ul v-else class="ss-list">
          <li v-for="s in list" :key="s.code" class="ss-item" @click="go(s)">
            <span class="ss-name">{{ s.name }}</span>
            <span class="ss-code">{{ s.code }}</span>
            <span class="ss-market">{{ s.market }}</span>
            <span class="ss-go">›</span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ss-mask { position: fixed; inset: 0; z-index: 200; background: rgba(30, 35, 50, .3); }
.ss-panel {
  position: absolute; top: 0; left: 0; right: 0;
  background: #fff; border-radius: 0 0 14px 14px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, .12); padding-bottom: 8px;
}
.ss-bar { display: flex; gap: 8px; padding: 10px 12px; }
.ss-input {
  flex: 1; min-width: 0; border: 1px solid #e0e3e8; border-radius: 9px;
  padding: 8px 12px; font-size: 14px; outline: none; background: #f7f8fa;
  font-family: inherit; -webkit-appearance: none;
}
.ss-input:focus { border-color: #2980b9; background: #fff; }
.ss-cancel { border: none; background: none; color: #2980b9; font-size: 14px; cursor: pointer; flex: none; padding: 0 2px; }
.ss-body { max-height: 60vh; overflow-y: auto; -webkit-overflow-scrolling: touch; }
.ss-tip { padding: 22px 0; text-align: center; color: #999; font-size: 13px; }
.ss-err { color: #c0392b; }
.ss-list { list-style: none; margin: 0; padding: 0 4px; }
.ss-item { display: flex; align-items: baseline; gap: 8px; padding: 11px 10px; border-bottom: 1px solid #f2f4f7; cursor: pointer; }
.ss-item:last-child { border-bottom: none; }
.ss-item:active { background: #f5f7fa; }
.ss-name { font-size: 14px; color: #333; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ss-code { font-size: 12px; color: #888; flex: none; font-variant-numeric: tabular-nums; }
.ss-market { font-size: 10px; color: #2980b9; background: #eef4fa; border-radius: 4px; padding: 1px 5px; flex: none; }
.ss-go { color: #ccc; flex: none; }
</style>
