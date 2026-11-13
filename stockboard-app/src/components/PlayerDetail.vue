<script setup>
/**
 * P3 选手详情：零静态 players/{zh}.json 依赖。
 * 持仓/收益/调仓三块由 EM rtV2 按 userId 独立直连；
 * 成功区块**直接渲染真实数据**，失败区块诚实标注原因，互不影响。
 */
import { computed, inject, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useEmPlayer, BLOCKS, BLOCK_LABELS } from '../composables/useEmPlayer.js'
import DataBadge from './DataBadge.vue'

const route = useRoute()
const router = useRouter()
const { playerLookup, emContract, tradedPlayerIds, sliceErrors, currentDate } = inject('stockData')
const refreshTick = inject('refreshTick', ref(0))

const { blocks, load } = useEmPlayer()
const loading = ref(false)
const showRaw = ref({})

const zhId = computed(() => String(route.params.zh_id || ''))
const player = computed(() => playerLookup.value[zhId.value] || null)
const traded = computed(() => tradedPlayerIds.value.has(zhId.value))

// 字符串/数字通用格式化：接口收益率是百分数字符串，静态 prevClose 是数字
function toNum(v) {
  if (v == null || v === '') return NaN
  const n = typeof v === 'number' ? v : parseFloat(v)
  return isFinite(n) ? n : NaN
}
function fmtNum(v, d = 2) {
  const n = toNum(v)
  return isNaN(n) ? '—' : n.toFixed(d)
}
function fmtPct(v) {
  const n = toNum(v)
  if (isNaN(n)) return '—'
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}
function retColor(v) {
  const n = toNum(v)
  if (isNaN(n)) return 'var(--ink-3)'
  return n >= 0 ? 'var(--up)' : 'var(--down)'
}

// ── 三块真实数据解析（字段名来自 rtV2 实测返回）──────────────────
const positionRows = computed(() => {
  const b = blocks.value.position
  if (b.state !== 'live' || !Array.isArray(b.data)) return []
  const out = []
  for (const blk of b.data) {
    for (const s of (blk.data || [])) {
      out.push({
        code: s.__code || s.stkMktCode || '',
        name: s.__name || '',
        block: blk.BlockName || '',
        price: s.__zxjg,
        cost: s.cbj,
        pos: s.holdPos,
        pnl: s.webYkRate,
      })
    }
  }
  return out.slice(0, 30)
})

const yieldData = computed(() => {
  const b = blocks.value.yield
  const d = b.data
  return b.state === 'live' && d && !Array.isArray(d) ? d : null
})

const tradeRows = computed(() => {
  const b = blocks.value.trades
  if (b.state !== 'live' || !b.data) return []
  const pages = Array.isArray(b.data) ? b.data : (b.data.pages || [])
  return pages.slice(0, 30).map(t => ({
    time: t.relocateTime || String(t.bizDate || ''),
    code: t.stkCode || '',
    name: t.stkName || '',
    side: t.bsMark === 'B' ? '买入' : (t.bsMark === 'S' ? '卖出' : (t.bsMark || '—')),
    buy: t.bsMark === 'B',
    price: t.relocatePrice,
    ratio: t.positionRatio,
  }))
})

// 静态 today.json 尚无快照时，用实时调仓里的当日记录给出真实结论，不要说「今日无操作」
const todayTrades = computed(() => {
  const d = String(currentDate.value || '').slice(0, 10)
  if (!d) return []
  return tradeRows.value.filter(t => String(t.time).slice(0, 10) === d)
})

// 顶部读数：实时收益优先；yield 失败则回退静态 prevClose（可能为空 → 显示 —）
const metrics = computed(() => {
  const y = yieldData.value
  if (y) return {
    total: y.totalYieldRateView, daily: y.dayYieldRateView,
    net: y.netWorth, month: y.monthYieldRateView, year: y.yearYieldRateView,
    source: '实时接口',
  }
  return {
    total: player.value?.total_return, daily: player.value?.daily_return,
    net: player.value?.net_value, month: null, year: null,
    source: '前一交易日收盘（当前无静态快照）',
  }
})

const overall = computed(() => {
  const list = BLOCKS.map(b => blocks.value[b])
  const live = list.filter(x => x.state === 'live')
  const errs = list.filter(x => x.state === 'error')
  if (live.length === BLOCKS.length) {
    return { state: 'live', at: live[0].at, reason: '', detail: null }
  }
  if (live.length > 0) {
    const failed = errs.map(x => `${BLOCK_LABELS[BLOCKS[list.indexOf(x)]] || '区块'}：${x.reason}`).join('；')
    return {
      state: 'stale', at: '',
      reason: `${live.length}/${BLOCKS.length} 区块实时可用，其余已回退${failed ? '（' + failed + '）' : ''}`,
      detail: null,
    }
  }
  if (errs.length) return { state: 'error', reason: errs[0].reason, detail: errs[0].detail, at: '' }
  return {
    state: 'stale', at: '',
    reason: emContract.value?.ok === false ? '东财接口策略变更，全站回退历史数据' : '数据时点',
    detail: null,
  }
})

const rows = computed(() => BLOCKS.map(b => ({
  key: b, label: BLOCK_LABELS[b], ...blocks.value[b],
  count: Array.isArray(blocks.value[b].data) ? blocks.value[b].data.length
    : (blocks.value[b].data ? Object.keys(blocks.value[b].data).length : 0),
})))

async function loadEm() {
  if (!player.value?.userId) return
  loading.value = true
  try { await load(player.value.userId, zhId.value, emContract.value) }
  finally { loading.value = false }
}

watch([zhId, () => player.value?.userId, emContract], () => { loadEm() })
watch(refreshTick, () => { loadEm() })
onMounted(() => { loadEm() })

function openStockDetail(c, n) { router.push({ path: '/stock/' + c, query: { name: n } }) }
function openMarket() { router.push('/market') }
</script>

<template>
  <div v-if="!player" class="card">
    <h2>选手详情</h2>
    <div class="empty-state">
      <template v-if="sliceErrors.players">选手榜单加载失败，无法读取该选手档案</template>
      <template v-else>该选手不在今日榜单（ID {{ zhId }}），暂无档案</template>
    </div>
  </div>

  <div v-else>
    <div class="card">
      <h2>选手档案 <span class="badge">{{ zhId }}</span></h2>
      <div class="pd-head">
        <div>
          <div class="pd-name">{{ player.name }}</div>
          <div class="pd-id">userId {{ player.userId || '—' }}<span v-if="player.watched" class="pd-watched">关注</span></div>
        </div>
        <span v-if="traded" class="pd-traded">今日有操作</span>
      </div>
      <div class="pd-badge"><DataBadge v-bind="overall" /></div>
    </div>

    <!-- 顶部读数：实时 yield 优先，静态 prevClose 兜底 -->
    <div class="player-meta">
      <div class="player-meta-item"><span class="val" :style="{ color: retColor(metrics.total) }">{{ fmtPct(metrics.total) }}</span><span class="lbl">总收益</span></div>
      <div class="player-meta-item"><span class="val" :style="{ color: retColor(metrics.daily) }">{{ fmtPct(metrics.daily) }}</span><span class="lbl">日收益</span></div>
      <div class="player-meta-item"><span class="val">{{ fmtNum(metrics.net, 3) }}</span><span class="lbl">净值</span></div>
    </div>
    <div class="pd-source">读数来源：{{ metrics.source }}</div>

    <div class="card">
      <h2>今日操作</h2>
      <div v-if="player.today && Object.keys(player.today).length" class="pd-today">
        <span class="pd-today-time">{{ player.today.updatedAt }}</span>
        <span class="pd-today-count">{{ player.today.tradeCount ?? 0 }} 笔调仓</span>
        <span v-if="traded" class="trade-dot" aria-label="今日有调仓"></span>
      </div>
      <div v-else-if="todayTrades.length" class="pd-today">
        <span class="pd-today-time">实时接口：今日 {{ todayTrades.length }} 笔调仓</span>
        <span class="pd-today-count">最新 {{ todayTrades[0].time }}</span>
        <span class="trade-dot" aria-label="今日有调仓"></span>
      </div>
      <div v-else class="empty-state">今日尚无调仓快照（实时接口与静态快照均无当日记录）</div>
    </div>

    <!-- 收益：实时接口读数 -->
    <div class="card">
      <h2>收益 <span class="badge">实时</span></h2>
      <div class="pd-badge"><DataBadge :state="blocks.yield.state" :at="blocks.yield.at" :reason="blocks.yield.reason" :detail="blocks.yield.detail" /></div>
      <div v-if="yieldData" class="pd-ygrid">
        <div><span class="lbl">近一月</span><span class="val" :style="{ color: retColor(metrics.month) }">{{ fmtPct(metrics.month) }}</span></div>
        <div><span class="lbl">近一年</span><span class="val" :style="{ color: retColor(metrics.year) }">{{ fmtPct(metrics.year) }}</span></div>
        <div><span class="lbl">净值</span><span class="val">{{ fmtNum(metrics.net, 3) }}</span></div>
        <div><span class="lbl">打板</span><span class="val">{{ yieldData.hitBoardName || '—' }} 第 {{ yieldData.hitBoardPos || '—' }} 名</span></div>
      </div>
      <div v-else class="empty-state">{{ loading ? '实时收益请求中…' : '该区块实时数据不可用（原因见徽标），本区块暂无数据' }}</div>
    </div>

    <!-- 持仓：真实表格 -->
    <div class="card">
      <h2>持仓 <span class="badge">实时</span></h2>
      <div class="pd-badge"><DataBadge :state="blocks.position.state" :at="blocks.position.at" :reason="blocks.position.reason" :detail="blocks.position.detail" /></div>
      <template v-if="positionRows.length">
        <div class="pd-table-wrap"><table class="pd-table">
          <thead><tr><th>股票</th><th>现价</th><th>成本</th><th>仓位</th><th>盈亏</th></tr></thead>
          <tbody>
            <tr v-for="(s, i) in positionRows" :key="s.code + i">
              <td><span class="pd-stock" @click="openStockDetail(s.code, s.name)">{{ s.name || s.code }}</span><span class="pd-code">{{ s.code }}</span></td>
              <td>{{ fmtNum(s.price) }}</td>
              <td>{{ fmtNum(s.cost) }}</td>
              <td>{{ fmtNum(s.pos, 0) }}%</td>
              <td :style="{ color: retColor(s.pnl) }">{{ fmtPct(s.pnl) }}</td>
            </tr>
          </tbody>
        </table></div>
        <div class="pd-count">共 {{ positionRows.length }} 条持仓</div>
      </template>
      <div v-else class="empty-state">{{ loading ? '实时持仓请求中…' : '该区块实时数据不可用（原因见徽标），本区块暂无数据' }}</div>
    </div>

    <!-- 调仓：真实流水 -->
    <div class="card">
      <h2>调仓 <span class="badge">实时</span></h2>
      <div class="pd-badge"><DataBadge :state="blocks.trades.state" :at="blocks.trades.at" :reason="blocks.trades.reason" :detail="blocks.trades.detail" /></div>
      <template v-if="tradeRows.length">
        <div class="pd-table-wrap"><table class="pd-table">
          <thead><tr><th>时间</th><th>股票</th><th>方向</th><th>价格</th><th>仓位</th></tr></thead>
          <tbody>
            <tr v-for="(t, i) in tradeRows" :key="t.time + t.code + i">
              <td class="pd-time">{{ t.time }}</td>
              <td><span class="pd-stock" @click="openStockDetail(t.code, t.name)">{{ t.name || t.code }}</span></td>
              <td :style="{ color: t.buy ? 'var(--up)' : 'var(--down)' }">{{ t.side }}</td>
              <td>{{ fmtNum(t.price) }}</td>
              <td>{{ t.ratio || '—' }}</td>
            </tr>
          </tbody>
        </table></div>
        <div class="pd-count">共 {{ tradeRows.length }} 条调仓（最多 30 条）</div>
      </template>
      <div v-else class="empty-state">{{ loading ? '实时调仓请求中…' : '该区块实时数据不可用（原因见徽标），本区块暂无数据' }}</div>
    </div>

    <!-- 原始 JSON 保留为可核查入口 -->
    <div class="card">
      <h2>原始返回 <span class="badge">核查</span></h2>
      <div v-for="r in rows" :key="r.key" class="pd-raw-row">
        <button type="button" class="pd-raw-btn" :aria-expanded="String(!!showRaw[r.key])"
                :disabled="!r.data" @click="showRaw[r.key] = !showRaw[r.key]">
          {{ r.label }} · {{ r.state }} · {{ r.data ? '查看' : '无数据' }}
        </button>
        <pre v-if="showRaw[r.key] && r.data" class="pd-raw">{{ JSON.stringify(r.data, null, 2) }}</pre>
      </div>
      <div class="pd-foot">实时明细按区块独立拉取；任一区块失败不影响其它区块，失败原因在各自徽标中可展开。</div>
      <button type="button" class="pd-back" @click="openMarket()">返回盘面</button>
    </div>
  </div>
</template>

<style scoped>
.pd-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.pd-name { font-size: 20px; font-weight: 700; color: var(--ink); letter-spacing: -.02em; }
.pd-id { margin-top: 5px; font-family: var(--mono); font-size: 10px; color: var(--ink-4); letter-spacing: .05em; }
.pd-watched { margin-left: 8px; color: var(--accent); border: 1px solid var(--accent-line); padding: 0 5px; border-radius: 2px; }
.pd-traded { font-family: var(--mono); font-size: 10px; color: var(--gold); border: 1px solid rgba(224,177,92,.3); padding: 3px 7px; border-radius: 2px; flex: none; }
.pd-badge { margin-top: 12px; }
.pd-source { margin: -8px 0 14px; font-family: var(--mono); font-size: 10px; color: var(--ink-4); letter-spacing: .04em; }
.pd-today { display: flex; align-items: center; gap: 12px; font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.pd-today-time { color: var(--ink); }

.pd-ygrid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-top: 12px; }
.pd-ygrid > div { background: var(--bg-1); border: 1px solid var(--line); border-radius: 2px; padding: 9px 10px; display: flex; flex-direction: column; gap: 4px; }
.pd-ygrid .lbl { font-size: 10px; color: var(--ink-4); letter-spacing: .08em; }
.pd-ygrid .val { font-family: var(--mono); font-size: 15px; font-weight: 600; color: var(--ink); }

.pd-table-wrap { margin-top: 12px; overflow-x: auto; }
.pd-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.pd-table th { font-family: var(--mono); font-size: 9px; font-weight: 500; letter-spacing: .1em; color: var(--ink-4); text-align: left; padding: 7px 6px; border-bottom: 1px solid var(--line-2); white-space: nowrap; }
.pd-table td { padding: 9px 6px; border-bottom: 1px solid var(--line); color: var(--ink-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
.pd-stock { color: var(--ink); cursor: pointer; text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--line-3); }
.pd-stock:hover { color: var(--accent); }
.pd-code { display: block; font-family: var(--mono); font-size: 9px; color: var(--ink-4); }
.pd-time { font-family: var(--mono); font-size: 10px; color: var(--ink-3); }
.pd-count { margin-top: 8px; font-family: var(--mono); font-size: 10px; color: var(--ink-4); }
.pd-raw-row { margin-bottom: 6px; }
.pd-raw-btn, .pd-back {
  background: var(--bg-3); color: var(--ink-2);
  border: 1px solid var(--line-2); border-radius: 2px;
  padding: 4px 9px; font: inherit; font-family: var(--mono); font-size: 10px;
  cursor: pointer;
}
.pd-raw-btn:disabled { opacity: .4; cursor: default; }
.pd-raw-btn:hover:not(:disabled), .pd-back:hover { color: var(--accent); border-color: var(--accent-line); }
.pd-raw { margin-top: 8px; max-height: 260px; overflow: auto; padding: 10px; background: var(--bg-1); border: 1px solid var(--line); border-radius: 2px; font-family: var(--mono); font-size: 10px; color: var(--ink-3); line-height: 1.5; }
.pd-foot { font-size: 11px; color: var(--ink-4); line-height: 1.6; margin: 12px 0 10px; }
</style>
