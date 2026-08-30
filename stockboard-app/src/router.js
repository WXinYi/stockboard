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

// 离开页面瞬间的滚动位置(守卫阶段尚未滚顶, 读取准确)
// vue-router 只在浏览器前进/后退(popstate)时保存离开页滚动, push 进详情再返回时
// savedPosition 为空/0 → 列表回顶; 这里自记一份供 scrollBehavior 恢复
// 存 sessionStorage: 单标签内跨整页刷新存活(HMR/数据更新触发的 reload 不丢)
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
  // 返回/前进优先恢复自记滚动(KeepAlive 页面 DOM 完整, 可直接定位); 其余新导航回顶
  scrollBehavior(to, from, savedPosition) {
    const remembered = scrollMemory[to.fullPath]
    if (remembered) {
      delete scrollMemory[to.fullPath]
      saveScrollMap(scrollMemory)
      // 整页刷新后返回: 目标页全新挂载, 列表数据异步渲染未撑开高度, 立即定位会被钳回 0
      // → 轮询等页面高度足够再交还原位置(最多 3s); 期间路由已变则放弃
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
  // matched 为空 = 首次导航(START); Tab 页路由大多无 name, 不能用 from.name 判断
  if (from.matched.length) {
    scrollMemory[from.fullPath] = { left: window.scrollX, top: window.scrollY }
    saveScrollMap(scrollMemory)
  }
})

export default router
