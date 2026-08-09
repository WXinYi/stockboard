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
      <!-- 环境结论 -->
      <div class="env-card" :class="{ bad: !auction.env.pass }">
        <div class="env-head">
          <span class="env-title">🏆 竞价环境 {{ auction.generated_at }}</span>
          <span class="env-badge" :class="auction.env.pass ? 'ok' : 'no'">
            {{ auction.env.pass ? '✅ 可出手' : '❌ 空仓' }}
          </span>
        </div>
        <div v-if="auction.env.pass" class="env-metrics">
          <span>情绪 {{ auction.env.data.strong }}</span>
          <span>连板 {{ auction.env.data.lbgd }}</span>
          <span>量能比 {{ fmt(auction.env.data.capacity_ratio) }}</span>
          <span>红盘 {{ fmt(auction.env.data.red_ratio * 100, 0) }}%</span>
        </div>
        <div v-else class="env-reasons">
          <div v-for="r in auction.env.reasons" :key="r">· {{ r }}</div>
        </div>
      </div>

      <!-- 空仓 → 直接结束 -->
      <div v-if="!auction.env.pass && auction.empty_reason" class="card empty-card">
        {{ auction.empty_reason }}
      </div>

      <template v-if="auction.env.pass">
        <!-- 强势板块 -->
        <div class="card" v-if="auction.boards.length">
          <div class="card-title">🔥 强势板块({{ auction.boards.length }})</div>
          <div class="board-chips">
            <span v-for="b in auction.boards" :key="b.code" class="chip"
                  :class="{ both: b.src === '爆量+强度' }">
              {{ b.name }}
              <i v-if="b.burst">{{ fmt(b.burst, 1) }}x</i>
              <em>{{ b.src }}</em>
            </span>
          </div>
        </div>

        <!-- 候选池 -->
        <div class="card">
          <div class="card-title">
            🎯 候选池({{ auction.candidates.length }})
            <span v-if="auction.stats" class="pool-stats">
              池{{ auction.stats.pool }} 基因{{ auction.stats.genes }}
            </span>
          </div>

          <div v-if="!auction.candidates.length" class="empty-cand">
            漏斗筛尽 — 今日无候选, 空仓观望
          </div>

          <div v-for="(c, i) in auction.candidates" :key="c.code"
               class="cand-card" @click="openStock(c.code)">
            <div class="cand-head">
              <span class="rank">{{ i + 1 }}</span>
              <span class="name">{{ c.name }}</span>
              <span class="code">{{ c.code }}</span>
              <span v-if="c.bonus" class="bonus">身位+{{ c.bonus }}</span>
              <span class="score" :class="{ hot: c.score >= 10 }">{{ c.score }}<i>/{{ c.max }}</i></span>
            </div>
            <div class="cand-factors">
              <span v-if="c.factors.bid_pct !== null" class="f">竞价 {{ fmt(c.factors.bid_pct) }}%</span>
              <span v-if="c.factors.vol_ratio !== null" class="f">量比 {{ fmt(c.factors.vol_ratio) }}</span>
              <span v-if="c.factors.turnover !== null" class="f">换手 {{ fmt(c.factors.turnover) }}%</span>
              <span v-if="c.tag && c.tag.includes('板')" class="f tag">{{ c.tag }}</span>
            </div>
            <div class="cand-sub">
              <span v-for="(v, k) in c.sub" :key="k" class="sub" :class="{ on: v > 0, off: v < 0 }">
                {{ k }}{{ v > 0 ? '+' : '' }}{{ v }}
              </span>
            </div>
            <div class="cand-foot">
              <span class="gene" :title="c.gene.reason">🧬 {{ c.gene.reason }}</span>
              <span v-if="c.boards.length" class="boards">{{ c.boards.slice(0, 3).join('·') }}</span>
            </div>
          </div>
        </div>

        <p class="foot-note">
          当日 09:29 结论快照 · 数据源 开盘啦(公开接口) · 阈值待回测校准, 仅供参考
        </p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.auction-page { padding: 14px; max-width: 640px; margin: 0 auto; }
.sd-loading { padding: 40px 0; text-align: center; color: #999; font-size: 13px; }
.sd-error { padding: 40px 0; text-align: center; color: #c0392b; font-size: 13px; }
.card {
  background: #fff; border-radius: 14px; padding: 14px 16px; margin-bottom: 12px;
  box-shadow: 0 1px 4px rgba(0,0,0,.05);
}
.card-title { font-size: 14px; font-weight: 600; color: #222; margin-bottom: 10px; display: flex; align-items: baseline; gap: 8px; }
.pool-stats { font-size: 11px; color: #999; font-weight: 400; }

/* 环境卡 */
.env-card { border-radius: 14px; padding: 14px 16px; margin-bottom: 12px; background: #eef4fb; border: 1px solid #cfe0f5; }
.env-card.bad { background: #fdf1f0; border-color: #f3c9c5; }
.env-head { display: flex; align-items: center; justify-content: space-between; }
.env-title { font-size: 14px; font-weight: 600; color: #222; }
.env-badge { font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 100px; }
.env-badge.ok { background: #2980b9; color: #fff; }
.env-badge.no { background: #c0392b; color: #fff; }
.env-metrics { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; }
.env-metrics span { font-size: 12px; color: #333; background: rgba(255,255,255,.7); padding: 4px 10px; border-radius: 8px; }
.env-reasons { margin-top: 10px; font-size: 12px; color: #a63a2e; line-height: 1.8; }

/* 板块 chips */
.board-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  font-size: 12px; color: #444; background: #f4f6f9; border: 1px solid #e4e8ef;
  padding: 5px 10px; border-radius: 100px; display: inline-flex; align-items: center; gap: 5px;
}
.chip.both { background: #eef4fb; border-color: #b9d4ee; color: #24608f; }
.chip i { font-style: normal; color: #c0392b; font-weight: 600; }
.chip em { font-style: normal; color: #999; font-size: 10px; }

/* 候选卡 */
.cand-card { border: 1px solid #e8ebf0; border-radius: 12px; padding: 11px 13px; margin-bottom: 10px; cursor: pointer; transition: transform .15s; }
.cand-card:active { transform: scale(.985); }
.cand-head { display: flex; align-items: center; gap: 8px; }
.rank { font-size: 12px; font-weight: 700; color: #fff; background: #2980b9; border-radius: 6px; padding: 2px 7px; }
.name { font-size: 15px; font-weight: 600; color: #111; }
.code { font-size: 11px; color: #999; }
.score { margin-left: auto; font-size: 16px; font-weight: 700; color: #2980b9; }
.score.hot { color: #c0392b; }
.score i { font-size: 10px; color: #999; font-weight: 400; }
.bonus { font-size: 10px; font-weight: 600; color: #8e44ad; background: #f6f0fb; border: 1px solid #e5d5f5; padding: 2px 7px; border-radius: 5px; }
.cand-factors { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 6px; }
.f { font-size: 11px; color: #333; background: #f4f6f9; padding: 3px 8px; border-radius: 6px; }
.f.tag { color: #8e44ad; background: #f6f0fb; }
.cand-sub { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 6px; }
.sub { font-size: 10px; color: #bbb; background: #f7f8fa; padding: 2px 7px; border-radius: 5px; }
.sub.on { color: #1e7e34; background: #ecf7ef; }
.sub.off { color: #c0392b; background: #fdf1f0; }
.cand-foot { margin-top: 8px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.gene { font-size: 11px; color: #555; }
.boards { font-size: 11px; color: #999; }
.empty-cand { font-size: 13px; color: #999; text-align: center; padding: 22px 0; }
.empty-card { color: #a63a2e; font-size: 13px; }
.foot-note { text-align: center; font-size: 11px; color: #aaa; margin: 16px 0 8px; }
</style>
