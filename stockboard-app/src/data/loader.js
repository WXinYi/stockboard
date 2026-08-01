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
export const fetchTrades        = () => getJson('data/latest/trades.json')
export const fetchSectors       = () => getJson('data/latest/sectors.json')
export const fetchCompare       = () => getJson('data/latest/compare.json')
export const fetchOverview      = () => getJson('data/latest/overview.json')
export const fetchNameMap       = () => getJson('data/latest/name_map.json')
export const fetchChangesSummary = () => getJson('data/latest/changes_summary.json')

// 全量（路由级懒加载）
export const fetchPlayersIndex  = () => getJson('data/latest/players_index.json')
export const fetchChanges       = () => getJson('data/latest/changes.json')

// 按需新鲜数据（PlayerDetail，不缓存）
export const fetchPlayerDetail  = (zhId) => fetch(`${BASE}data/latest/players/${zhId}.json`).then((r) => r.json())
export const fetchPlayerHistory = (zhId) => fetch(`${BASE}data/history/${zhId}.json`).then((r) => r.json())

// 兼容保留（新架构下前端不再调用）
export const fetchSummary = () => getJson('data/latest/summary.json')
