<script setup>
import { computed, inject, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchPlayerDetail } from '../data/loader.js'
import { useTableSort } from '../composables/useTableSort.js'
import { useCopyCode } from '../composables/useCopyCode.js'
import { pctHtml, drawdownColor } from '../utils/format.js'
import { usePullRefresh } from '../composables/usePullRefresh.js'

const route = useRoute()
const router = useRouter()
const { playerLookup } = inject('stockData')

// 📈 跳转股票详情页(自建详情, 页内含 H5 嵌套入口)
function openStockDetail(c, n) { router.push({ path: '/stock/' + c, query: { name: n } }) }

// 股票名称交互：点击跳转自建详情页 / 长按打开东方财富App / 名称后复制图标 → 复制代码
const { copiedKey, copyStockCode } = useCopyCode()

// 东方财富 market 参数: 0=深市 1=沪市 2=北交所 116=港股
function emMarket(code) {
  if (!code) return '0'
  if (/^\d{5}$/.test(code)) return '116'      // 港股 5 位
  if (/^(4|8|92)/.test(code)) return '2'      // 北交所
  if (/^(6|5|9|11|10)/.test(code)) return '1' // 沪市(6主板/688科创/5基金/900B股/110 113 118转债)
  return '0'                                   // 深市及默认
}

// 平台原生 scheme(来自东财官方 commscheme_config.js,type=818):
//   iOS:     eastmoney://page/stockpage?market={0|1|2|116}&code={code}
//   Android: dfcft://router/market/stock?anchorKey=STOCK_BAR&market={m}&stockCode={code}
function emNativeUrl(code) {
  const m = emMarket(code)
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) return `eastmoney://page/stockpage?market=${m}&code=${code}`
  if (/Android/i.test(navigator.userAgent)) return `dfcft://router/market/stock?anchorKey=STOCK_BAR&market=${m}&stockCode=${code}`
  return null  // 桌面/未知 → 走通用深链
}

// 通用深链(H5 中转拉起 App,iOS/Android 通用,App 未装则回退下载页)
function emUniversalUrl(code) {
  return `https://emh5wap.eastmoney.com/h52n/CommScheme?linktype=818&sharetype=1&market=${emMarket(code)}&stockCode=${code}`
}

// 原生 scheme + 2s 超时回退: 先试原生唤起,未唤起则跳通用深链
let emFallbackTimer = null
function onEmVisibility() {
  if (document.hidden) clearTimeout(emFallbackTimer)   // App 被唤起,页面隐藏 → 取消回退
}
function openEmApp(code) {
  const native = emNativeUrl(code)
  const universal = emUniversalUrl(code)
  if (!native) { window.location.href = universal; return }  // 桌面直接开通用深链
  clearTimeout(emFallbackTimer)
  document.addEventListener('visibilitychange', onEmVisibility)
  emFallbackTimer = setTimeout(() => {
    if (!document.hidden) window.location.href = universal  // 2s 未唤起 → 回退
  }, 2000)
  window.location.href = native
}

// 长按 600ms → 打开东方财富个股页; 与单击跳详情互斥(长按后抑制随后的 click)
const lpTimer = ref(null)
const lpFired = ref(false)
function onPressStart(code) {
  lpFired.value = false
  clearTimeout(lpTimer.value)
  lpTimer.value = setTimeout(() => {
    lpFired.value = true
    openEmApp(code)
  }, 600)
}
function onPressEnd() { clearTimeout(lpTimer.value) }
function onPressCancel() { clearTimeout(lpTimer.value) }
function onClickName(code, name) {
  if (lpFired.value) { lpFired.value = false; return }  // 长按已跳转, 不再导航
  openStockDetail(code, name)
}

onUnmounted(() => {
  clearTimeout(emFallbackTimer)
  document.removeEventListener('visibilitychange', onEmVisibility)
})

const playerData = ref(null)
const loadingDetail = ref(false)

// 合并：player文件(pos/trades/inferred) + stockData(基本字段如ranks/returns)
const player = computed(() => {
  if (!playerData.value) return null
  const info = playerLookup.value[playerData.value.id] || {}
  return { ...playerData.value, ...info }
})

const posData = computed(() => playerData.value?.p || [])
const tradeData = computed(() => playerData.value?.t || [])
const inferredPositions = computed(() => playerData.value?.i || [])
const { sorted: sortedPos, toggle: tp, indicator: ip } = useTableSort(posData, 'rr')
const { sorted: sortedTrades, toggle: tt, indicator: it } = useTableSort(tradeData, 'td')
// 调仓按月分组: 组头显示月度买卖笔数汇总, 组内保持全局排序(默认日期降序)
const tradeGroups = computed(() => {
  const groups = new Map()
  for (const x of sortedTrades.value) {
    const m = String(x.td || '').slice(0, 7)
    if (!m) continue
    if (!groups.has(m)) groups.set(m, { month: m, rows: [], buy: 0, sell: 0 })
    const g = groups.get(m)
    g.rows.push(x)
    if (x.dr === '买入') g.buy++
    else g.sell++
  }
  return [...groups.values()]
})

function pct(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return n >= 0 ? `+${n.toFixed(2)}%` : `${n.toFixed(2)}%`
}
function posLabel(total) {
  if (!total || total === 0) return '空仓'
  if (total < 10) return '1成以下'
  if (total < 30) return '1-3成'
  if (total < 50) return '3-5成'
  if (total < 70) return '5-7成'
  if (total < 90) return '7-9成'
  return '9成以上'
}

async function loadPlayer(zhId) {
  loadingDetail.value = true
  try {
    playerData.value = await fetchPlayerDetail(zhId)
  } catch (e) {
    console.warn('选手详情加载失败:', e.message)
  } finally {
    loadingDetail.value = false
  }
}

watch(() => route.params.zh_id, (newId) => { if (newId) loadPlayer(newId) })

// 全局刷新信号：App.vue 下拉刷新/顶栏刷新后重拉选手详情(仅当前激活页面响应)
usePullRefresh(() => { if (route.params.zh_id) loadPlayer(route.params.zh_id) })

onMounted(() => { if (route.params.zh_id) loadPlayer(route.params.zh_id) })
</script>

<template>
  <div v-if="loadingDetail" class="loading-view" style="min-height:40vh;">
    <div class="loading-spinner"></div>
    <p class="loading-text">加载选手数据…</p>
  </div>
  <div v-else-if="!player && !loadingDetail" class="card">
    <div class="empty-state">📭 选手数据加载失败</div>
  </div>
  <div v-else-if="player">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
      <span style="font-size:22px;font-weight:520;color:#111;">{{ player.name || player.id || player.zh_id }}</span>
      <span v-for="r in player.ranks" :key="r" style="font-size:11px;color:#5b6daa;background:rgba(91,109,170,.08);padding:2px 10px;border-radius:100px;">{{ r }}</span>
    </div>


    <div class="grid-2">
      <div class="card">
        <h2>📦 当前持仓 <span class="badge">{{ sortedPos.length }}</span></h2>
        <div v-if="!sortedPos.length" class="empty-state">📭 暂无持仓数据</div>
        <div class="table-wrap" v-else>
        <table>
          <thead><tr>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tp('cp')">成本价{{ ip('cp') }}</th>
            <th style="cursor:pointer;" @click="tp('np')">现价{{ ip('np') }}</th>
            <th style="cursor:pointer;" @click="tp('pr')">盈亏{{ ip('pr') }}</th>
            <th style="cursor:pointer;" @click="tp('rr')">仓位{{ ip('rr') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="x in sortedPos" :key="x.sc">
              <td class="nowrap">
                <strong class="stock-name" @click="onClickName(x.sc, x.sn)" @pointerdown="onPressStart(x.sc)" @pointerup="onPressEnd" @pointerleave="onPressCancel" @pointercancel="onPressCancel" @contextmenu.prevent :title="'点击查看详情 · 长按打开东方财富'">{{ x.sn }}</strong>
                <button class="stock-copy" @click.stop="copyStockCode(x.sc, 'p:' + x.sc)" title="复制代码" :aria-label="'复制代码 ' + x.sc">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                <span v-if="copiedKey === 'p:' + x.sc" class="copied-tip">✓ 已复制</span>
              </td>
              <td class="nowrap" style="color:#999;">{{ x.sc }}</td>
              <td>{{ (x.cp || 0).toFixed(3) }}</td>
              <td>{{ (x.np || 0).toFixed(3) }}</td>
              <td v-html="pctHtml(x.pr)"></td>
              <td>
                <span class="progress-bar"><span class="fill" :style="{ width: Math.min(100, x.rr || 0) + '%' }"></span></span>
                {{ (x.rr || 0).toFixed(1) }}%
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
    <!-- 推测持仓 -->
    <div v-if="inferredPositions.length" class="card" style="margin-bottom:14px;">
      <h2>📎 推测持仓 <span class="badge">{{ inferredPositions.length }}</span></h2>
      <p class="hint">根据买卖记录推算，仅供参考，非 API 原始数据</p>
      <div style="max-height:300px;overflow-y:auto;">
        <div class="table-wrap"><table><thead><tr><th>股票</th><th>代码</th><th>估算仓位</th><th>状态</th><th>买入</th><th>卖出</th></tr></thead>
          <tbody>
            <tr v-for="s in inferredPositions" :key="s.cd">
              <td class="nowrap">
                <strong class="stock-name" @click="onClickName(s.cd, s.sn)" @pointerdown="onPressStart(s.cd)" @pointerup="onPressEnd" @pointerleave="onPressCancel" @pointercancel="onPressCancel" @contextmenu.prevent :title="'点击查看详情 · 长按打开东方财富'">{{ s.sn }}</strong>
                <button class="stock-copy" @click.stop="copyStockCode(s.cd, 'i:' + s.cd)" title="复制代码" :aria-label="'复制代码 ' + s.cd">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                <span v-if="copiedKey === 'i:' + s.cd" class="copied-tip">✓ 已复制</span>
              </td>
              <td class="nowrap" style="color:#666;">{{ s.cd }}</td>
              <td style="color:#666;">{{ s.le }}</td>
              <td><span :style="{ color: s.cf === 'mid' ? '#5b6daa' : '#999', fontSize:'12px' }">{{ s.st }}</span></td>
              <td>{{ s.bc }}笔</td>
              <td>{{ s.sc }}笔</td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
    </div>
      <div class="card">
        <h2>🔄 调仓记录 <span class="badge">{{ sortedTrades.length }}</span></h2>
        <div v-if="!sortedTrades.length" class="empty-state">📭 暂无调仓记录</div>
        <div class="table-wrap" v-else style="max-height:400px;overflow-y:auto;">
        <table>
          <thead><tr>
            <th style="cursor:pointer;" @click="tt('td')">日期{{ it('td') }}</th>
            <th style="cursor:pointer;" @click="tt('dr')">方向{{ it('dr') }}</th>
            <th>股票</th><th>代码</th>
            <th style="cursor:pointer;" @click="tt('tc')">笔数{{ it('trades_count') }}</th>
            <th style="cursor:pointer;" @click="tt('pr')">价格{{ it('price') }}</th>
            <th>仓位</th>
          </tr></thead>
          <tbody v-for="g in tradeGroups" :key="g.month">
            <tr class="trade-month">
              <td colspan="7">
                <span class="trade-month-label">{{ g.month }}</span>
                <span class="trade-month-buy">买入 {{ g.buy }}</span>
                <span class="trade-month-sell">卖出 {{ g.sell }}</span>
                <span v-if="g.buy !== g.sell" class="trade-month-net" :class="g.buy > g.sell ? 'buy' : 'sell'">{{ g.buy > g.sell ? '净买入' : '净卖出' }} {{ Math.abs(g.buy - g.sell) }}</span>
              </td>
            </tr>
            <tr v-for="x in g.rows" :key="x.id || x.td + x.sc">
              <td>{{ x.td }}</td>
              <td><span :class="x.dr === '买入' ? 'buy' : 'sell'">{{ x.dr }}</span></td>
              <td class="nowrap">
                <strong class="stock-name" @click="onClickName(x.sc, x.sn)" @pointerdown="onPressStart(x.sc)" @pointerup="onPressEnd" @pointerleave="onPressCancel" @pointercancel="onPressCancel" @contextmenu.prevent :title="'点击查看详情 · 长按打开东方财富'">{{ x.sn }}</strong>
                <button class="stock-copy" @click.stop="copyStockCode(x.sc, 't:' + (x.id || x.td + x.sc))" title="复制代码" :aria-label="'复制代码 ' + x.sc">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                <span v-if="copiedKey === 't:' + (x.id || x.td + x.sc)" class="copied-tip">✓ 已复制</span>
              </td>
              <td class="nowrap" style="color:#999;">{{ x.sc }}</td>
              <td>{{ x.tc || 1 }}笔</td>
              <td style="font-size:12px;color:#888;">{{ (x.pr && x.pr > 0) ? '¥' + x.pr.toFixed(2) : '—' }}</td>
              <td style="font-size:12px;color:#888;">{{ x.rr || '—' }}</td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>

    </div>

  </div>
    <div v-if="player" class="player-meta" style="margin-top:20px;">
      <div class="player-meta-item"><div class="val" :style="{ color: player.total_return >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(player.total_return) }}</div><div class="lbl">总收益</div></div>
      <div class="player-meta-item"><div class="val" :style="{ color: player.daily_return >= 0 ? '#e74c3c' : '#27ae60' }">{{ pct(player.daily_return) }}</div><div class="lbl">日收益</div></div>
      <div class="player-meta-item"><div class="val" :style="{ color: (player.net_value || 0) >= 1 ? '#e74c3c' : '#27ae60' }">{{ (player.net_value || 0).toFixed(3) }}</div><div class="lbl">净值</div></div>
      <div class="player-meta-item"><div class="val" :style="{ color: drawdownColor(player.max_drawdown) }">{{ (player.max_drawdown || 0).toFixed(1) }}%</div><div class="lbl">最大回撤</div></div>
      <div class="player-meta-item"><div class="val">{{ posLabel(player.total_position ?? player._total_position) }}</div><div class="lbl">当前仓位</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.win_rate || 0).toFixed(1) }}%</div><div class="lbl">胜率</div></div>
      <div class="player-meta-item"><div class="val">{{ player.days || 0 }}天</div><div class="lbl">运行天数</div></div>
      <div class="player-meta-item"><div class="val">{{ (player.followers || 0).toLocaleString() }}</div><div class="lbl">关注人数</div></div>
      <div class="player-meta-item" style="grid-column:span 3;">
        <div style="font-size:13px;color:#666;text-align:left;">{{ player.intro || player.concept || '暂无简介' }}</div>
        <div class="lbl">简介</div>
      </div>
      <div v-if="player.ranks?.length" class="player-meta-item" style="grid-column:span 3;">
        <div style="font-size:12px;color:#666;text-align:left;">
          上榜: <span v-for="r in player.ranks" :key="r" class="rank-tag">{{ r }}</span>
        </div>
        <div class="lbl">榜单</div>
      </div>
    </div>

</template>

<style scoped>
/* 下划线标识可点击 → 跳详情页 */
.stock-name {
  cursor: pointer;
  touch-action: manipulation;
  -webkit-touch-callout: none;   /* iOS 长按不弹系统菜单/文本选择 */
  -webkit-user-select: none;
  user-select: none;
  text-decoration: underline;
  text-underline-offset: 3px;
  text-decoration-color: rgba(41,128,185,.45);
}
.stock-name:hover { color: #2980b9; }
.stock-copy { border: none; background: none; cursor: pointer; color: #aaa; padding: 0 3px; vertical-align: middle; }
.stock-copy:hover { color: #2980b9; }
.copied-tip { color: #27ae60; font-size: 11px; margin-left: 4px; }
/* 调仓月度分组行 */
.trade-month td { background: #f6f8fa; padding: 4px 8px; font-size: 11px; }
.trade-month-label { font-weight: 600; color: #333; margin-right: 10px; }
.trade-month-buy { color: #e0484a; margin-right: 8px; }
.trade-month-sell { color: #38a869; margin-right: 8px; }
.trade-month-net { font-weight: 600; }
</style>
