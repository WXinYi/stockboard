<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { fetchInfoContent, fetchAnnounceContent } from '../composables/useKplApi.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'

// 资讯/研报/公告 正文详情页 — 路由 /info/:code/:iid, query.type: 1新闻/2研报/3公告
// 标题由列表页经 sessionStorage 传递(避免超长 URL); 公告接口自带 title
defineOptions({ name: 'InfoDetail' })

const route = useRoute()
const code = route.params.code
const iid = route.params.iid
const type = Number(route.query.type || 1)

const title = ref(sessionStorage.getItem('info_title_' + iid) || '')
const content = ref('')
const sourceUrl = ref('')
const loading = ref(true)
const error = ref(false)

async function load() {
  const id = route.params.iid
  loading.value = true
  error.value = false
  try {
    const res = type === 3 ? await fetchAnnounceContent(id) : await fetchInfoContent(id, type)
    if (!res) throw new Error('empty')
    content.value = res.content
    sourceUrl.value = res.sourceUrl || ''
    if (res.title) title.value = res.title
  } catch (e) {
    error.value = true
  } finally {
    loading.value = false
  }
}

// KeepAlive 复用: 一篇正文 → 另一篇(列表直跳)时按 iid 重载; 返回则走 onActivated 不重载
watch(() => route.params.iid, (id) => {
  title.value = sessionStorage.getItem('info_title_' + id) || ''
  content.value = ''
  sourceUrl.value = ''
  load()
})

// 下拉刷新: 仅当前激活页面响应(usePullRefresh 按激活态过滤)
usePullRefresh(() => { load() })

onMounted(load)
</script>

<template>
  <div class="id-page">
    <div class="id-card">
      <h1 class="id-title">{{ title || '资讯详情' }}</h1>
      <p v-if="loading" class="id-tip">正文加载中…</p>
      <p v-else-if="error" class="id-tip id-err">
        正文加载失败
        <button class="id-retry" @click="load">重试</button>
      </p>
      <template v-else>
        <p v-if="content" class="id-content">{{ content }}</p>
        <p v-else class="id-tip">该内容暂无正文{{ sourceUrl ? '，请点击查看原文' : '' }}</p>
        <a v-if="sourceUrl" class="id-src" :href="sourceUrl" target="_blank" rel="noopener">查看原文 ↗</a>
      </template>
    </div>
  </div>
</template>

<style scoped>
.id-page { padding: 8px 14px 16px; }
.id-card { background: #fff; border: 1px solid #eceff3; border-radius: 12px; padding: 16px; }
.id-title { font-size: 16px; font-weight: 600; color: #111; line-height: 1.5; margin: 0 0 12px; }
.id-tip { font-size: 12px; color: #999; margin: 8px 0; }
.id-err { color: #c0392b; }
.id-retry { border: 1px solid #2980b9; color: #2980b9; background: #fff; font-size: 12px; padding: 3px 10px; border-radius: 6px; cursor: pointer; margin-left: 6px; }
.id-content { font-size: 13px; color: #444; line-height: 1.9; margin: 0 0 14px; white-space: pre-wrap; word-break: break-word; }
.id-src { font-size: 12px; color: #2980b9; text-decoration: none; }
@media (min-width: 768px) {
  .id-page { padding: 12px 28px 24px; }
  .id-card { padding: 20px 24px; }
}
</style>
