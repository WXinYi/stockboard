import { createRouter, createWebHashHistory } from 'vue-router'
import MarketTab from './components/MarketTab.vue'
import MarketDetail from './components/MarketDetail.vue'
import BoardDetail from './components/BoardDetail.vue'
import RankingsTab from './components/RankingsTab.vue'
import StockTab from './components/StockTab.vue'
import PlayerDetail from './components/PlayerDetail.vue'
import StockDetailPage from './components/StockDetailPage.vue'
import StockH5Page from './components/StockH5Page.vue'
import AuctionTab from './components/AuctionTab.vue'
import InfoDetail from './components/InfoDetail.vue'

const routes = [
  { path: '/', redirect: '/market' },
  { path: '/market', component: MarketTab, meta: { keepAlive: true } },
  { path: '/market/:section', name: 'MarketDetail', component: MarketDetail },
  { path: '/board/:bk_code', name: 'BoardDetail', component: BoardDetail },
  // PRD-Q4=3B：/copy 路由与 CopyTradeTab.vue 已整体移除，不做简版、不留隐藏入口
  { path: '/rankings', component: RankingsTab, meta: { keepAlive: true } },
  { path: '/stocks', component: StockTab, meta: { keepAlive: true } },
  { path: '/auction', component: AuctionTab },
  { path: '/player/:zh_id', component: PlayerDetail },
  { path: '/stock/:code', component: StockDetailPage },
  { path: '/stock/:code/h5', component: StockH5Page },
  { path: '/info/:code/:iid', component: InfoDetail },
]

// 离开页面瞬间的滚动位置(守卫阶段尚未滚顶, 读取准确)
const SCROLL_KEY = 'sb-scroll-memory'
const loadScrollMap = () => {
  try { return JSON.parse(sessionStorage.getItem(SCROLL_KEY) || '{}') } catch { return {} }
}
const saveScrollMap = m => {
  try { sessionStorage.setItem(SCROLL_KEY, JSON.stringify(m)) } catch {}
}
let scrollMemory = loadScrollMap()

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior(to, from, savedPosition) {
    const remembered = scrollMemory[to.fullPath]
    if (remembered) {
      delete scrollMemory[to.fullPath]
      saveScrollMap(scrollMemory)
      return new Promise(resolve => {
        let tries = 0
        const poll = () => {
          if (router.currentRoute.value.fullPath !== to.fullPath) return resolve({ top: 0 })
          const max = document.documentElement.scrollHeight - window.innerHeight
          if (remembered.top <= max || tries++ > 30) return resolve(remembered)
          setTimeout(poll, 100)
        }
        poll()
      })
    }
    if (savedPosition) return savedPosition
    return { top: 0 }
  },
})

router.beforeEach((to, from) => {
  if (from.matched.length) {
    scrollMemory[from.fullPath] = { left: window.scrollX, top: window.scrollY }
    saveScrollMap(scrollMemory)
  }
})

export default router
