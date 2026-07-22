<script setup>
import { ref } from 'vue'

const props = defineProps({
  consensus: { type: Array, default: () => [] },
})
const emit = defineEmits(['show-player'])

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
      <div v-if="!consensus.length" class="empty-state">📭 今日暂无调仓记录</div>
      <div v-for="c in consensus" :key="c.code"
           class="consensus-card"
           v-show="!search || (c.name+''+c.code).toLowerCase().includes(search.toLowerCase())">
        <h3>
          <template v-if="c.buy_players.length + c.sell_players.length >= 5">🔥</template>
          <template v-else-if="c.buy_players.length + c.sell_players.length >= 3">📈</template>
          {{ c.name }}
        </h3>
        <div class="code">{{ c.code }} · {{ c.buy_players.length + c.sell_players.length }}人交易</div>
        <div v-if="c.buy_players.length" class="bar">
          <span class="buy">🟢 买入 {{ c.buy_players.length }}人</span>
        </div>
        <div v-if="c.buy_players.length" class="players">{{ c.buy_players.join('、') }}</div>
        <div v-if="c.sell_players.length" class="bar" style="margin-top:4px;">
          <span class="sell">🔴 卖出 {{ c.sell_players.length }}人</span>
        </div>
        <div v-if="c.sell_players.length" class="players">{{ c.sell_players.join('、') }}</div>
      </div>
    </div>
  </div>
</template>
