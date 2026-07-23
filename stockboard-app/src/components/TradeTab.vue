<script setup>
import { ref, inject } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const { tradeConsensus: consensus, playerNameMap: playerIds } = inject('stockData')
function goPlayer(nameOrId) { router.push('/player/' + (playerIds.value[nameOrId] || nameOrId)) }

const search = ref('')
</script>

<template>
  <div class="card">
    <h2>调仓共识 <span class="badge">{{ consensus.length }}只股票</span></h2>
    <p class="hint">同一只股票被多个选手买入/卖出 = 共识信号。点击选手名可查看详情。</p>
    <div class="search-box">
      <input type="text" v-model="search" placeholder="🔍 搜索股票名..." />
    </div>
    <div style="max-height:600px;overflow-y:auto;">
      <div v-if="!consensus.length" class="empty-state">📭 今日暂无调仓 · 下个交易日 09:45 自动采集</div>
      <div v-for="c in consensus" :key="c.code"
           class="consensus-card"
           v-show="!search || (c.name+''+c.code).toLowerCase().includes(search.toLowerCase())">
        <h3>
          <span class="strength-tag" :style="{color: c.strengthColor, fontSize: '11px'}">{{ c.strength }}</span>
          {{ c.name }}
        </h3>
        <div class="code">{{ c.code }} · {{ c.buy_players.length + c.sell_players.length }}人交易</div>
        <div v-if="c.buy_players.length" class="bar">
          <span class="buy">🟢 买入 {{ c.buy_players.length }}人</span>
        </div>
        <div v-if="c.buy_players.length" class="players">
          <template v-for="(p, idx) in c.buy_players" :key="'b'+p">
            <span v-if="idx > 0">、</span>
            <span class="player-chip" @click.stop="goPlayer(p)">{{ p }}</span>
          </template>
        </div>
        <div v-if="c.sell_players.length" class="bar" style="margin-top:4px;">
          <span class="sell">🔴 卖出 {{ c.sell_players.length }}人</span>
        </div>
        <div v-if="c.sell_players.length" class="players">
          <template v-for="(p, idx) in c.sell_players" :key="'s'+p">
            <span v-if="idx > 0">、</span>
            <span class="player-chip" @click.stop="goPlayer(p)">{{ p }}</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
