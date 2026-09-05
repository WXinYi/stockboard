const BASE = import.meta.env.BASE_URL

// 统一模块级缓存：按请求路径缓存，clearDataCache() 一次性清空
const _cache = {}

async function getJson(path) {
  if (_cache[path]) return _cache[path]
  const resp = await fetch(`${BASE}${path}`)
  const data = await resp.json()
  _cache[path] = data
  return data
}

export function clearDataCache() {
  for (const k of Object.keys(_cache)) delete _cache[k]
}

// 分片（按需加载）
export const fetchCore          = () => getJson('data/latest/core.json')
export const fetchCopy          = () => getJson('data/latest/copy.json')
export const fetchStocks        = () => getJson('data/latest/stocks.json')
export const fetchNameMap       = () => getJson('data/latest/name_map.json')
export const fetchChangesSummary = () => getJson('data/latest/changes_summary.json')
export const fetchAuction = () => getJson('data/latest/auction.json')
export const fetchMyPositions = () => getJson('data/latest/my_positions.json')
export const fetchLianbanBid = () => getJson('data/latest/lianban_bid.json')

// 全量（路由级懒加载）
export const fetchPlayersIndex  = () => getJson('data/latest/players_index.json')

// 按需新鲜数据（PlayerDetail，不缓存）
export const fetchPlayerDetail  = (zhId) => fetch(`${BASE}data/latest/players/${zhId}.json`).then((r) => r.json())
