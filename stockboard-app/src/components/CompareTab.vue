<script setup>
import { computed, inject } from 'vue'
import { useTableSort } from '../composables/useTableSort.js'

const { stockCompare: compare } = inject('stockData')

const concData = computed(() => compare.value.concentration)
const divData = computed(() => compare.value.divergence)

const { sorted: sortedConc, toggle: tc, indicator: ic } = useTableSort(concData, 'totalHolders')
const { sorted: sortedDiv, toggle: td, indicator: id } = useTableSort(divData, 'gap')
</script>

<template>
  <div class="card" style="margin-bottom:20px;background:#f8f9fa;">
    <h2 style="border:none;margin-bottom:4px;">📊 多空对比</h2>
    <p class="hint">只做客观数据并列展示，不做判断。高质量选手 × {{ compare.qualityCount }} 人（运营≥200天，风险调整得分≥0.15）。点击表头排序。</p>
  </div>

  <div class="card" style="margin-bottom:20px;">
    <h2>📋 持仓拥挤度 <span class="badge">Top 30</span></h2>
    <div style="max-height:500px;overflow-y:auto;">
      <table><thead><tr>
        <th>#</th><th>股票</th><th>代码</th>
        <th style="cursor:pointer;" @click="tc('totalHolders')">总持仓{{ ic('totalHolders') }}</th>
        <th style="cursor:pointer;" @click="tc('qualityHolders')">高手持仓{{ ic('qualityHolders') }}</th>
        <th style="cursor:pointer;" @click="tc('allBuying')">全体买入{{ ic('allBuying') }}</th>
        <th style="cursor:pointer;" @click="tc('allSelling')">全体卖出{{ ic('allSelling') }}</th>
        <th style="cursor:pointer;" @click="tc('qualityBuying')">高手买入{{ ic('qualityBuying') }}</th>
        <th style="cursor:pointer;" @click="tc('qualitySelling')">高手卖出{{ ic('qualitySelling') }}</th>
      </tr></thead>
        <tbody>
          <tr v-for="(s, i) in sortedConc.slice(0,30)" :key="s.code">
            <td>{{ i + 1 }}</td>
            <td><strong>{{ s.name }}</strong></td>
            <td style="color:#999;">{{ s.code }}</td>
            <td>{{ s.totalHolders }}人</td>
            <td :style="{ color: s.qualityHolders >= s.totalHolders * 0.3 ? '#27ae60' : '#999' }">{{ s.qualityHolders }}人</td>
            <td :style="{ color: s.allBuying > s.allSelling ? '#e74c3c' : s.allBuying > 0 ? '#666' : '#999' }">{{ s.allBuying || '—' }}</td>
            <td :style="{ color: s.allSelling > s.allBuying ? '#27ae60' : s.allSelling > 0 ? '#666' : '#999' }">{{ s.allSelling || '—' }}</td>
            <td :style="{ color: s.qualityBuying > s.qualitySelling ? '#e74c3c' : s.qualityBuying > 0 ? '#666' : '#999' }">{{ s.qualityBuying || '—' }}</td>
            <td :style="{ color: s.qualitySelling > s.qualityBuying ? '#27ae60' : s.qualitySelling > 0 ? '#666' : '#999' }">{{ s.qualitySelling || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <h2>🔀 买卖方向分歧 <span class="badge">Top 20</span></h2>
    <p class="hint">高手与全体选手的买卖净差差距排名。点击表头可切换排序。</p>
    <div style="max-height:500px;overflow-y:auto;">
      <table><thead><tr>
        <th>#</th><th>股票</th><th>代码</th>
        <th style="cursor:pointer;" @click="td('totalHolders')">持仓{{ id('totalHolders') }}</th>
        <th style="cursor:pointer;" @click="td('allNet')">全体净买卖{{ id('allNet') }}</th>
        <th style="cursor:pointer;" @click="td('qualityNet')">高手净买卖{{ id('qualityNet') }}</th>
        <th style="cursor:pointer;" @click="td('gap')">方向差距{{ id('gap') }}</th>
      </tr></thead>
        <tbody>
          <tr v-for="(s, i) in sortedDiv.slice(0,20)" :key="s.code">
            <td>{{ i + 1 }}</td>
            <td><strong>{{ s.name }}</strong></td>
            <td style="color:#999;">{{ s.code }}</td>
            <td>{{ s.totalHolders }}人</td>
            <td :style="{ color: s.allNet > 0 ? '#e74c3c' : s.allNet < 0 ? '#27ae60' : '#999' }">
              {{ s.allNet > 0 ? '+' : '' }}{{ s.allNet || 0 }}
            </td>
            <td :style="{ color: s.qualityNet > 0 ? '#e74c3c' : s.qualityNet < 0 ? '#27ae60' : '#999' }">
              {{ s.qualityNet > 0 ? '+' : '' }}{{ s.qualityNet || 0 }}
            </td>
            <td style="font-weight:600;color:#e67e22;">{{ s.gap }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
