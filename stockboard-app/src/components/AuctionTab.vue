<script setup>
// 09:25 盘前候选 · 全量页(选股域二级页)
// 环境结论 + 昨日连板·竞价换手 TOP5 + 出击选股(早盘存档);
// 状态词走全站唯一口径(可买/待确认/只看不买), 原始 Python 状态词在此归一。
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction } from '../data/loader.js'
import { statusWord } from '../utils/stockPicks.js'

const auction = ref(null)
const loading = ref(true)
const error = ref(false)
const router = useRouter()

onMounted(async () => {
  try { auction.value = await fetchAuction() } catch { error.value = true }
  loading.value = false
})

function openStock(code, name) {
  router.push({ path: `/stock/${code}`, query: name ? { name } : {} })
}

// 缺键防御(旧形状快照/加载竞态): 无键时渲染空态而非崩页
const env = computed(() => auction.value?.env || null)
const pass = computed(() => !!env.value?.pass)
const boards = computed(() => auction.value?.boards || [])
const strike = computed(() => auction.value?.strike || [])
const bidrank = computed(() => auction.value?.bidrank || [])
const reasons = computed(() => (pass.value ? (env.value?.reasons || []).slice(0, 1) : (env.value?.reasons || [])))
const metrics = computed(() => {
  const d = env.value?.data || {}
  return [
    { k: '情绪', v: d.strong ?? '—' },
    { k: '连板', v: d.lbgd ?? '—' },
    { k: '量能比', v: d.capacity_ratio != null ? d.capacity_ratio : '—' },
    { k: '红盘率', v: d.red_ratio != null ? Math.round(d.red_ratio * 100) + '%' : '—' },
  ]
})
const turnoverMax = computed(() => Math.max(...bidrank.value.map(r => r.turnover || 0), 1))
function fmt(v, digits = 2) {
  return v === null || v === undefined || (typeof v === 'number' && !isFinite(v)) ? '—' : Number(v).toFixed(digits)
}
function bidTxt(r) { return r.bid_pct != null ? `${r.bid_pct > 0 ? '+' : ''}${fmt(r.bid_pct, 1)}%` : '—' }
function hsTxt(r) { return r.turnover != null ? `${fmt(r.turnover, 1)}%` : (r.hs != null ? `${fmt(r.hs, 1)}%` : '—') }
</script>

<template>
  <div class="at-page">
    <div v-if="loading" class="sd-loading">正在加载竞价快照…</div>
    <div v-else-if="error" class="sd-error">⚠️ 暂无竞价数据(非交易时段无快照)</div>

    <template v-else-if="auction">
      <!-- 环境结论 -->
      <div class="at-env" :class="pass ? 'ok' : 'no'">
        <div class="r1">
          <span class="badge" :class="pass ? 'ok' : 'no'">{{ pass ? '✅ 可出手' : '❌ 空仓观望' }}</span>
          <span class="time">{{ auction.generated_at }}</span>
        </div>
        <div class="rs"><div v-for="(r, i) in reasons" :key="i">· {{ r }}</div></div>
        <div v-if="pass" class="mts">
          <span v-for="m in metrics" :key="m.k"><i>{{ m.k }}</i>{{ m.v }}</span>
        </div>
      </div>

      <div v-if="!pass" class="hold">
        {{ auction.empty_reason || '今日竞价判空仓 · 盘中出手以选股首页「可买/禁买」结论为准' }}
      </div>

      <template v-if="pass">
        <!-- 昨日连板 · 竞价换手 TOP5 -->
        <section class="at-sec">
          <div class="sec-head">
            <h3>🪜 昨日连板 · 竞价换手 TOP5</h3>
            <em>{{ auction.date }} 09:25 快照</em>
          </div>
          <div v-if="!bidrank.length" class="empty">今日无换手达标昨日连板股</div>
          <div v-else class="card">
            <div v-for="(r, i) in bidrank" :key="r.code" class="rk" @click="openStock(r.code, r.name)">
              <span class="rk-no">{{ i + 1 }}</span>
              <b class="nm">{{ r.name }}</b>
              <span v-if="r.height" class="lv">{{ r.height }}板</span>
              <span class="rk-r"><i>竞价 {{ bidTxt(r) }}</i><em>换手 {{ hsTxt(r) }}</em></span>
              <span class="bar"><span class="fill" :style="{ width: Math.min(100, (r.turnover || 0) / turnoverMax * 100) + '%' }"></span></span>
            </div>
          </div>
        </section>

        <!-- 出击选股(早盘存档) -->
        <section class="at-sec">
          <div class="sec-head">
            <h3>🎯 出击选股</h3>
            <em>早盘存档 · 盘中出手以首页结论为准</em>
          </div>
          <div v-if="!strike.length" class="empty">本阶段无出击候选(纪律优先)</div>
          <div v-else class="card">
            <div v-for="(c, i) in strike" :key="c.code" class="sk" @click="openStock(c.code, c.name)">
              <div class="sk-l1">
                <span class="rk-no plain">{{ i + 1 }}</span>
                <b class="nm">{{ c.name }}</b>
                <span v-if="c.height" class="lv">{{ c.height >= 2 ? c.height + '连板' : '首板' }}</span>
                <span class="bid">{{ bidTxt(c) }}</span>
                <span class="st" :class="statusWord(c.status).cls">{{ statusWord(c.status).txt }}</span>
              </div>
              <div v-if="c.reason" class="sk-l2">{{ c.reason }}</div>
            </div>
          </div>
          <div v-if="boards.length" class="chips">
            <span v-for="b in boards" :key="b.code" class="chip">{{ b.name }}<i v-if="b.burst">{{ fmt(b.burst, 1) }}x</i></span>
          </div>
        </section>

        <div class="next" @click="router.push('/market')">
          <b>→ 回选股首页看「可买 / 禁买」结论与今日出击清单</b>
        </div>
        <p class="foot">当日 09:29 出击结论快照(与钉钉推送同源) · 数据源 开盘啦(公开接口) · 周期闸门纪律优先, 仅供参考</p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.at-page { padding: 4px 0 0; max-width: 640px; margin: 0 auto; }
.sd-loading { padding: 40px 0; text-align: center; color: #999; font-size: 13px; }
.sd-error { padding: 40px 0; text-align: center; color: #c0392b; font-size: 13px; }

/* 环境结论 */
.at-env { border-radius: 12px; padding: 11px 14px; margin-bottom: 14px; background: linear-gradient(135deg, #eaf3fd 0%, #f4f9ff 100%); border: 1px solid #cfe0f5; }
.at-env.no { background: linear-gradient(135deg, #fdf1f0 0%, #fef8f7 100%); border-color: #f3c9c5; }
.r1 { display: flex; align-items: center; gap: 8px; }
.time { font-size: 11px; color: #94a3b8; margin-left: auto; }
.badge { font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 100px; }
.badge.ok { background: #2980b9; color: #fff; }
.badge.no { background: #c0392b; color: #fff; }
.rs { margin-top: 7px; font-size: 12px; color: #43505e; line-height: 1.7; }
.mts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.mts span { font-size: 11px; color: #1a1a2e; background: rgba(255,255,255,.85); border: 1px solid #e6edf6; padding: 3px 10px; border-radius: 8px; }
.mts i { font-style: normal; color: #64748b; margin-right: 3px; font-size: 10px; }
.hold { font-size: 12px; color: #a63a2e; padding: 6px 2px 14px; line-height: 1.7; }

/* 区块 */
.at-sec { margin-bottom: 16px; }
.sec-head { display: flex; align-items: baseline; gap: 7px; margin-bottom: 7px; }
.sec-head h3 { font-size: 13px; font-weight: 700; margin: 0; }
.sec-head em { font-size: 10.5px; color: #8a97a8; font-style: normal; }
.empty { font-size: 12px; color: #999; text-align: center; padding: 16px 0; }
.card { border: 1px solid #eceff3; border-radius: 12px; background: #fff; overflow: hidden; }

/* Top5 换手榜 */
.rk { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.rk:last-child { border-bottom: none; }
.rk:active { background: #f7f9fc; }
.rk-no { width: 20px; height: 20px; border-radius: 7px; background: #c0392b; color: #fff; font-size: 11px; font-weight: 800; display: flex; align-items: center; justify-content: center; flex: none; }
.rk-no.plain { background: #eef1f6; color: #667; }
.rk .nm { font-size: 13px; font-weight: 600; color: #333; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lv { font-size: 10px; color: #b8860b; font-weight: 800; flex: none; }
.rk-r { flex: none; text-align: right; line-height: 1.45; width: 96px; }
.rk-r i { font-style: normal; display: block; font-size: 12px; color: #c0392b; font-weight: 700; }
.rk-r em { display: block; font-style: normal; font-size: 10px; color: #8a97a8; }
.bar { flex: none; width: 52px; height: 5px; background: #f2f4f7; border-radius: 3px; overflow: hidden; }
.fill { display: block; height: 100%; background: linear-gradient(90deg, #2980b9, #5b6daa); border-radius: 3px; }

/* 出击选股(早盘存档) */
.sk { padding: 9px 12px; border-bottom: 1px solid #f5f5f5; cursor: pointer; }
.sk:last-child { border-bottom: none; }
.sk:active { background: #f7f9fc; }
.sk-l1 { display: flex; align-items: center; gap: 7px; }
.sk-l1 .nm { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; color: #333; }
.bid { font-size: 12px; color: #c0392b; font-weight: 700; flex: none; }
.st { font-size: 10px; font-weight: 800; padding: 2px 8px; border-radius: 5px; background: #eef1f5; color: #667; flex: none; }
.st.go { background: #c0392b; color: #fff; }
.st.alt { background: #f5a623; color: #fff; }
.sk-l2 { margin-top: 4px; font-size: 11px; color: #64748b; line-height: 1.6; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* 强势板块 chips */
.chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 9px; }
.chip { font-size: 11px; color: #444; background: #f4f6f9; border: 1px solid #e4e8ef; padding: 3px 10px; border-radius: 100px; display: inline-flex; align-items: center; gap: 4px; }
.chip i { font-style: normal; color: #c0392b; font-weight: 600; }

.next { border: 1px solid #dbe7f3; background: #f4f8fd; border-radius: 12px; padding: 10px 12px; text-align: center; font-size: 12px; color: #24608f; cursor: pointer; margin-bottom: 8px; }
.foot { text-align: center; font-size: 10.5px; color: #a0aab8; margin: 4px 0 10px; line-height: 1.7; }
</style>
