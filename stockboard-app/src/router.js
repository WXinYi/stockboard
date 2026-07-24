import { createRouter, createWebHashHistory } from 'vue-router'
import OverviewTab from './components/OverviewTab.vue'
import RankingsTab from './components/RankingsTab.vue'
import StockTab from './components/StockTab.vue'
import TradeTab from './components/TradeTab.vue'
import SectorTab from './components/SectorTab.vue'
import CopyTradeTab from './components/CopyTradeTab.vue'
import CompareTab from './components/CompareTab.vue'
import PositionTracking from './components/PositionTracking.vue'
import PlayerDetail from './components/PlayerDetail.vue'

const routes = [
  { path: '/', redirect: '/copy' },
  { path: '/overview', component: OverviewTab, meta: { keepAlive: true } },
  { path: '/copy', component: CopyTradeTab, meta: { keepAlive: true } },
  { path: '/rankings', component: RankingsTab, meta: { keepAlive: true } },
  { path: '/stocks', component: StockTab, meta: { keepAlive: true } },
  { path: '/sectors', component: SectorTab, meta: { keepAlive: true } },
  { path: '/trades', component: TradeTab, meta: { keepAlive: true } },
  { path: '/compare', component: CompareTab, meta: { keepAlive: true } },
  { path: '/tracking', component: PositionTracking, meta: { keepAlive: true } },
  { path: '/player/:zh_id', component: PlayerDetail },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})
