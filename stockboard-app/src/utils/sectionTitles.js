// 盘面二级页标题唯一映射 — App.vue(顶栏) 与 MarketDetail.vue 共用
// 2026-09-13 去重: 此前两处各维护一份, App.vue 漏加 cycle 且残留已下线的 discipline,
// 导致 /market/cycle 顶栏显示兜底的"盘面详情"。改一处漏一处的教训: 只留这一份。
export const MARKET_SECTION_TITLES = {
  auction: '竞价抢筹', wind: '最强风口', ladder: '涨停天梯', reasons: '涨停原因',
  newhighs: '百日新高', global: '外围市场', institution: '机构增仓',
  mood: '市场情绪', live: '盘面动态', lhb: '龙虎榜', cycle: '情绪周期',
}
