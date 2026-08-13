<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAuction } from '../data/loader.js'

const auction = ref(null)
const loading = ref(true)
const error = ref(false)
const router = useRouter()

onMounted(async () => {
  try {
    auction.value = await fetchAuction()
  } catch {
    error.value = true
  }
  loading.value = false
})

function openStock(code) {
  router.push(`/stock/${code}`)
}

function fmt(v, digits = 2) {
  if (v === null || v === undefined) return '—'
  return Number(v).toFixed(digits)
}
</script>

<template>
  <div class="auction-page">
    <!-- 加载 / 错误 -->
    <div v-if="loading" class="sd-loading">正在加载竞价快照…</div>
    <div v-else-if="error" class="sd-error">⚠️ 暂无竞价数据(非交易时段无快照)</div>

    <template v-else-if="auction">
      <!-- 环境结论条 -->
      <div class="env-strip" :class="{ bad: !auction.env.pass }">
        <div class="env-top">
          <span class="env-title">🏆 竞价环境</span>
          <span class="env-time">{{ auction.generated_at }}</span>
          <span class="env-badge" :class="auction.env.pass ? 'ok' : 'no'">
            {{ auction.env.pass ? '✅ 可出手' : '❌ 空仓' }}
          </span>
        </div>
        <div v-if="auction.env.pass" class="env-metrics">
          <span><i>情绪</i>{{ auction.env.data.strong }}</span>
          <span><i>连板</i>{{ auction.env.data.lbgd }}</span>
          <span><i>量能比</i>{{ fmt(auction.env.data.capacity_ratio) }}</span>
          <span><i>红盘</i>{{ fmt(auction.env.data.red_ratio * 100, 0) }}%</span>
        </div>
        <div v-else class="env-reasons">
          <div v-for="r in auction.env.reasons" :key="r">· {{ r }}</div>
        </div>
      </div>

      <!-- 空仓 → 直接结束 -->
      <div v-if="!auction.env.pass && auction.empty_reason" class="empty-line">
        {{ auction.empty_reason }}
      </div>

      <template v-if="auction.env.pass">
        <!-- 强势板块 -->
        <section v-if="auction.boards.length" class="auction-sec">
          <h3 class="auction-sec-title">🔥 强势板块 <em>{{ auction.boards.length }}</em></h3>
          <div class="board-chips">
            <span v-for="b in auction.boards" :key="b.code" class="chip"
                  :class="{ both: b.src === '爆量+强度' }">
              {{ b.name }}
              <i v-if="b.burst">{{ fmt(b.burst, 1) }}x</i>
              <em>{{ b.src }}</em>
            </span>
          </div>
        </section>

        <!-- 候选池 -->
        <section class="auction-sec">
          <h3 class="auction-sec-title">
            🎯 核心候选 <em>{{ auction.candidates.length }}</em>
            <span v-if="auction.stats" class="sec-sub">池{{ auction.stats.pool }} · 基因{{ auction.stats.genes }}</span>
          </h3>

          <div v-if="!auction.candidates.length" class="empty-cand">
            竞价无真金白银抢筹 — 观望
          </div>

          <div v-else class="cand-list">
            <div class="cand-head">
              <span class="h-rank">#</span>
              <span class="h-name">个股</span>
              <span class="h-f">竞价</span>
              <span class="h-f">净买</span>
              <span class="h-f">量比</span>
              <span class="h-score">得分</span>
            </div>
            <div v-for="(c, i) in auction.candidates" :key="c.code"
                 class="cand-row core" @click="openStock(c.code)">
              <div class="cand-line1">
                <span class="h-rank rank-num">{{ i + 1 }}</span>
                <span class="h-name">
                  <strong>{{ c.name }}</strong>
                  <span class="code">{{ c.code }}</span>
                  <span v-if="c.sub['S4身位']" class="bonus">身位+{{ c.sub['S4身位'] }}</span>
                  <span v-if="c.tag && c.tag.includes('板')" class="tag">{{ c.tag }}</span>
                </span>
                <span class="h-f f-bid">{{ c.factors.bid_pct !== null ? fmt(c.factors.bid_pct) + '%' : '—' }}</span>
                <span class="h-f f-net">{{ c.factors.bid_net !== null ? fmt(c.factors.bid_net / 1e4, 0) + '万' : '—' }}</span>
                <span class="h-f f-vol">{{ c.factors.vol_ratio !== null ? fmt(c.factors.vol_ratio) : '—' }}</span>
                <span class="h-score" :class="{ hot: c.score >= 10 }">{{ c.score }}<i>/{{ c.max }}</i></span>
              </div>
              <div class="cand-line2">
                <span v-for="(v, k) in c.sub" :key="k" class="sub" :class="{ on: v > 0, off: v < 0 }">
                  {{ k }}{{ v > 0 ? '+' : '' }}{{ v }}
                </span>
                <span class="gene" :title="c.gene.reason">🧬 {{ c.gene.reason }}</span>
                <span v-if="c.boards.length" class="boards">{{ c.boards.slice(0, 3).join('·') }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- 备选观察 -->
        <section v-if="auction.watch && auction.watch.length" class="auction-sec">
          <h3 class="auction-sec-title">👀 备选观察 <em>{{ auction.watch.length }}</em></h3>
          <div class="cand-list">
            <div class="cand-head">
              <span class="h-rank">#</span>
              <span class="h-name">个股</span>
              <span class="h-f">竞价</span>
              <span class="h-f">净买</span>
              <span class="h-f">量比</span>
              <span class="h-score">得分</span>
            </div>
            <div v-for="c in auction.watch" :key="c.code"
                 class="cand-row watch" @click="openStock(c.code)">
              <div class="cand-line1">
                <span class="h-rank rank-num watch">W</span>
                <span class="h-name">
                  <strong>{{ c.name }}</strong>
                  <span class="code">{{ c.code }}</span>
                  <span v-if="c.sub['S4身位']" class="bonus">身位+{{ c.sub['S4身位'] }}</span>
                  <span v-if="c.tag && c.tag.includes('板')" class="tag">{{ c.tag }}</span>
                </span>
                <span class="h-f f-bid">{{ c.factors.bid_pct !== null ? fmt(c.factors.bid_pct) + '%' : '—' }}</span>
                <span class="h-f f-net">{{ c.factors.bid_net !== null ? fmt(c.factors.bid_net / 1e4, 0) + '万' : '—' }}</span>
                <span class="h-f f-vol">{{ c.factors.vol_ratio !== null ? fmt(c.factors.vol_ratio) : '—' }}</span>
                <span class="h-score">{{ c.score }}<i>/{{ c.max }}</i></span>
              </div>
              <div class="cand-line2">
                <span v-for="(v, k) in c.sub" :key="k" class="sub" :class="{ on: v > 0, off: v < 0 }">
                  {{ k }}{{ v > 0 ? '+' : '' }}{{ v }}
                </span>
                <span class="gene" :title="c.gene.reason">🧬 {{ c.gene.reason }}</span>
                <span v-if="c.boards.length" class="boards">{{ c.boards.slice(0, 3).join('·') }}</span>
              </div>
            </div>
          </div>
        </section>

        <p class="foot-note">
          当日 09:29 结论快照 · 数据源 开盘啦(公开接口) · 阈值待回测校准, 仅供参考
        </p>
      </template>
    </template>
  </div>
</template>

<style scoped>
/* 竞价抢筹详情: 表头+行式紧凑列表(无卡片), 横向留白由外层容器提供 */
.auction-page { padding: 4px 0 0; max-width: 640px; margin: 0 auto; }
.sd-loading { padding: 40px 0; text-align: center; color: #999; font-size: 13px; }
.sd-error { padding: 40px 0; text-align: center; color: #c0392b; font-size: 13px; }

/* ── 环境结论条 ── */
.env-strip {
  border-radius: 12px; padding: 12px 14px; margin-bottom: 16px;
  background: linear-gradient(135deg, #eaf3fd 0%, #f4f9ff 100%);
  border: 1px solid #cfe0f5;
}
.env-strip.bad { background: linear-gradient(135deg, #fdf1f0 0%, #fef8f7 100%); border-color: #f3c9c5; }
.env-top { display: flex; align-items: center; gap: 8px; }
.env-title { font-size: 14px; font-weight: 600; color: #1a1a2e; }
.env-time { font-size: 11px; color: #94a3b8; margin-right: auto; }
.env-badge { font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 100px; flex: none; }
.env-badge.ok { background: #2980b9; color: #fff; }
.env-badge.no { background: #c0392b; color: #fff; }
.env-metrics { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.env-metrics span { font-size: 12px; color: #1a1a2e; background: rgba(255,255,255,.8); padding: 4px 10px; border-radius: 8px; }
.env-metrics i { font-style: normal; color: #64748b; margin-right: 4px; }
.env-reasons { margin-top: 8px; font-size: 12px; color: #a63a2e; line-height: 1.8; }
.empty-line { color: #a63a2e; font-size: 13px; padding: 4px 2px; margin-bottom: 12px; }

/* ── 区块 ── */
.auction-sec { margin-bottom: 18px; }
.auction-sec-title { font-size: 13px; font-weight: 600; color: #1a1a2e; margin: 0 0 8px; display: flex; align-items: baseline; gap: 6px; }
.auction-sec-title em { font-style: normal; font-size: 11px; color: #2980b9; }
.sec-sub { margin-left: auto; font-size: 11px; color: #94a3b8; font-weight: 400; }
.empty-cand { font-size: 13px; color: #999; text-align: center; padding: 18px 0; }

/* ── 强势板块 chips ── */
.board-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  font-size: 12px; color: #444; background: #f4f6f9; border: 1px solid #e4e8ef;
  padding: 4px 10px; border-radius: 100px; display: inline-flex; align-items: center; gap: 5px;
}
.chip.both { background: #eef4fb; border-color: #b9d4ee; color: #24608f; }
.chip i { font-style: normal; color: #c0392b; font-weight: 600; }
.chip em { font-style: normal; color: #999; font-size: 10px; }

/* ── 候选列表(表头 + 行) ── */
.cand-list { border: 1px solid #eceff3; border-radius: 10px; overflow: hidden; background: #fff; }
.cand-head { display: flex; align-items: center; gap: 8px; padding: 7px 12px; background: #f6f8fa; font-size: 11px; color: #8e8e9a; border-bottom: 1px solid #eceff3; }
.cand-row { border-bottom: 1px solid #f5f5f5; cursor: pointer; transition: background .15s; }
.cand-row:last-child { border-bottom: none; }
.cand-row:active { background: #f7f9fc; }
.cand-line1 { display: flex; align-items: center; gap: 8px; padding: 9px 12px 4px; }
.cand-line2 { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 0 12px 9px; }

/* 列宽(表头/数据行共用) */
.h-rank { flex: 0 0 22px; text-align: center; font-size: 11px; }
.h-name { flex: 1 1 auto; min-width: 0; display: flex; align-items: center; gap: 6px; overflow: hidden; }
.h-name strong { font-size: 13px; color: #1a1a2e; white-space: nowrap; }
.h-name .code { font-size: 10px; color: #94a3b8; flex: none; }
.h-f { flex: 0 0 50px; text-align: right; font-size: 12px; color: #333; white-space: nowrap; }
.h-score { flex: 0 0 42px; text-align: right; font-size: 14px; font-weight: 700; color: #2980b9; }
.h-score.hot { color: #c0392b; }
.h-score i { font-size: 10px; color: #94a3b8; font-weight: 400; }

.rank-num { font-size: 12px; font-weight: 700; color: #fff; background: #2980b9; border-radius: 6px; padding: 1px 6px; }
.rank-num.watch { background: #7f8c8d; }
.bonus { font-size: 10px; font-weight: 600; color: #8e44ad; background: #f6f0fb; border: 1px solid #e5d5f5; padding: 1px 6px; border-radius: 5px; flex: none; }
.tag { font-size: 10px; color: #8e44ad; background: #f6f0fb; padding: 1px 6px; border-radius: 5px; flex: none; }
.f-bid { color: #c0392b; font-weight: 600; }

.sub { font-size: 10px; color: #bbb; background: #f7f8fa; padding: 1px 7px; border-radius: 5px; }
.sub.on { color: #1e7e34; background: #ecf7ef; }
.sub.off { color: #c0392b; background: #fdf1f0; }
.gene { font-size: 11px; color: #555; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.boards { font-size: 11px; color: #999; flex: none; }

.foot-note { text-align: center; font-size: 11px; color: #aaa; margin: 4px 0 8px; }

@media (max-width: 400px) {
  .h-f { flex-basis: 44px; }
}
</style>
