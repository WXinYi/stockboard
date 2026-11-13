const BASE = import.meta.env.BASE_URL

// 统一模块级缓存：按请求路径缓存，clearDataCache() 一次性清空
const _cache = {}

async function getJson(path) {
  if (_cache[path]) return _cache[path]
  // no-store：绕过 Pages max-age=600 的 HTTP 缓存，数据到达即最新；
  // 会话内仍有模块级 _cache，请求量不变
  const resp = await fetch(`${BASE}${path}`, { cache: 'no-store' })
  const data = await resp.json()
  _cache[path] = data
  return data
}

export function clearDataCache() {
  for (const k of Object.keys(_cache)) delete _cache[k]
}

// ── P3 四个契约（AQ1=A：market 四切片物理平铺在 latest/，路径零改动）──
export const fetchPlayers = () => getJson('data/latest/players.json')
export const fetchStocks = () => getJson('data/latest/stocks.json')
export const fetchToday = () => getJson('data/latest/today.json')
export const fetchMarket = (name) => getJson(`data/latest/${name}.json`)

// 保留：竞价 / 连板竞价仍由 auction.yml 独立生产（single-writer，F5）
export const fetchAuction = () => getJson('data/latest/auction.json')
export const fetchLianbanBid = () => getJson('data/latest/lianban_bid.json')
