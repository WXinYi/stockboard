import { createRouter, createWebHashHistory } from 'vue-router'
import RankingsTab from './components/RankingsTab.vue'
import StockTab from './components/StockTab.vue'
import CopyTradeTab from './components/CopyTradeTab.vue'
import PlayerDetail from './components/PlayerDetail.vue'
import StockDetailPage from './components/StockDetailPage.vue'
import StockH5Page from './components/StockH5Page.vue'
import AuctionTab from './components/AuctionTab.vue'

const routes = [
  { path: '/', redirect: '/copy' },
  { path: '/copy', component: CopyTradeTab, meta: { keepAlive: true } },
  { path: '/rankings', component: RankingsTab, meta: { keepAlive: true } },
  { path: '/stocks', component: StockTab, meta: { keepAlive: true } },
  { path: '/auction', component: AuctionTab },  // 竞价快照: 当日结论, 不缓存
  { path: '/player/:zh_id', component: PlayerDetail },
  { path: '/stock/:code', component: StockDetailPage },
  { path: '/stock/:code/h5', component: StockH5Page },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior() {
    return { top: 0 }
  },
})
