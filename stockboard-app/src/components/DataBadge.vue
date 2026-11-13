<script setup>
import { ref, computed } from 'vue'

/** 三态数据时点徽标：颜色 + 文字双编码，🔴 可展开原始 code/message（K12 100% 可见）。 */
const props = defineProps({
  state: { type: String, default: 'idle' },  // idle | loading | live | stale | error
  at: { type: String, default: '' },
  reason: { type: String, default: '' },
  detail: { type: Object, default: null },
})

const open = ref(false)

const label = computed(() => {
  switch (props.state) {
    case 'live': return `实时接口 · ${props.at}`
    case 'loading': return '实时接口请求中…'
    case 'error': return `实时接口失败 · ${props.reason || '未知原因'}`
    case 'stale': return `前一交易日收盘 · ${props.reason || '数据时点'}`
    default: return '前一交易日收盘 · 数据时点'
  }
})
const tone = computed(() => {
  if (props.state === 'live') return 'live'
  if (props.state === 'error') return 'error'
  return 'stale'
})
const expandable = computed(() => props.state === 'error' || props.state === 'stale')
</script>

<template>
  <div class="data-badge" :class="tone">
    <button class="db-main" type="button"
            :aria-expanded="expandable ? String(open) : undefined"
            :disabled="!expandable"
            @click="open = !open">
      <span class="db-dot" aria-hidden="true"></span>
      <span class="db-text">{{ label }}</span>
      <span v-if="expandable" class="db-caret" aria-hidden="true">{{ open ? '▴' : '▾' }}</span>
    </button>
    <div v-if="open && expandable" class="db-detail">
      <div><strong>状态</strong> {{ state }} · 已回退至静态快照</div>
      <div v-if="reason"><strong>原因</strong> {{ reason }}</div>
      <div v-if="detail"><strong>原始返回</strong> code={{ detail.code }} · {{ detail.message }}</div>
      <div v-else><strong>原始返回</strong> 无（未发起实时请求）</div>
    </div>
  </div>
</template>

<style scoped>
.data-badge {
  border: 1px solid var(--line-2);
  border-radius: 2px;
  background: var(--bg-1);
  font-family: var(--mono);
  font-size: 10px;
  overflow: hidden;
}
.data-badge.live { border-color: rgba(33,197,139,.32); }
.data-badge.error { border-color: rgba(255,77,90,.32); }
.data-badge.stale { border-color: rgba(224,177,92,.28); }

.db-main {
  width: 100%; display: flex; align-items: center; gap: 7px;
  padding: 6px 9px; background: none; border: none;
  color: var(--ink-2); font: inherit; text-align: left;
  cursor: pointer; -webkit-tap-highlight-color: transparent;
}
.db-main:disabled { cursor: default; }
.db-main:focus-visible { outline: 1px solid var(--accent); outline-offset: -1px; }

.db-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; background: var(--gold); }
.live .db-dot { background: var(--down); box-shadow: 0 0 8px rgba(33,197,139,.6); }
.error .db-dot { background: var(--up); }
.stale .db-dot { background: var(--gold); }

.db-text { flex: 1; min-width: 0; line-height: 1.4; letter-spacing: .02em; }
.live .db-text { color: var(--down); }
.error .db-text { color: #ff8b93; }
.stale .db-text { color: var(--gold); }
.db-caret { color: var(--ink-4); flex: none; }

.db-detail {
  padding: 8px 9px 9px; border-top: 1px solid var(--line);
  color: var(--ink-3); line-height: 1.65; word-break: break-word;
}
.db-detail strong { color: var(--ink-2); font-weight: 600; }
</style>
