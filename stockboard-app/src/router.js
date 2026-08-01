import { createRouter, createWebHashHistory } from 'vue-router'
import RankingsTab from './components/RankingsTab.vue'
import StockTab from './components/StockTab.vue'
import CopyTradeTab from './components/CopyTradeTab.vue'
import PlayerDetail from './components/PlayerDetail.vue'

const routes = [
  { path: '/', redirect: '/copy' },
  { path: '/copy', component: CopyTradeTab, meta: { keepAlive: true } },
  { path: '/rankings', component: RankingsTab, meta: { keepAlive: true } },
  { path: '/stocks', component: StockTab, meta: { keepAlive: true } },
  { path: '/player/:zh_id', component: PlayerDetail },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})
