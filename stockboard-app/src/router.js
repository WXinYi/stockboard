import { createRouter, createWebHashHistory } from 'vue-router'
import MarketTab from './components/MarketTab.vue'
import MarketDetail from './components/MarketDetail.vue'
import BoardDetail from './components/BoardDetail.vue'
import RankingsTab from './components/RankingsTab.vue'
import StockTab from './components/StockTab.vue'
import CopyTradeTab from './components/CopyTradeTab.vue'
import PlayerDetail from './components/PlayerDetail.vue'
import StockDetailPage from './components/StockDetailPage.vue'
import StockH5Page from './components/StockH5Page.vue'
import AuctionTab from './components/AuctionTab.vue'
import InfoDetail from './components/InfoDetail.vue'

const routes = [
  { path: '/', redirect: '/market' },   // 默认首页 = 盘面页(原 /copy)
  { path: '/market', component: MarketTab, meta: { keepAlive: true } },   // 盘面概览(静态优先于 :section)
  { path: '/market/:section', name: 'MarketDetail', component: MarketDetail },
  { path: '/board/:bk_code', name: 'BoardDetail', component: BoardDetail },
  { path: '/copy', component: CopyTradeTab, meta: { keepAlive: true } },   // 保留直达, 导航不暴露
  { path: '/rankings', component: RankingsTab, meta: { keepAlive: true } },
  { path: '/stocks', component: StockTab, meta: { keepAlive: true } },
  { path: '/auction', component: AuctionTab },  // 竞价快照: 当日结论, 不缓存; 入口在盘面页
  { path: '/player/:zh_id', component: PlayerDetail },
  { path: '/stock/:code', component: StockDetailPage },
  { path: '/stock/:code/h5', component: StockH5Page },
  { path: '/info/:code/:iid', component: InfoDetail },  // 资讯/研报/公告正文详情
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
  // 返回/前进恢复浏览器保存的滚动位置(配合 KeepAlive 的 StockDetailPage); 新导航回顶
  scrollBehavior(to, from, savedPosition) {
    if (savedPosition) return savedPosition
    return { top: 0 }
  },
})
