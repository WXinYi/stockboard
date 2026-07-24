const BASE = import.meta.env.BASE_URL

let _summaryCache = null

export async function fetchSummary() {
  if (_summaryCache) return _summaryCache
  const resp = await fetch(`${BASE}data/latest/summary.json`)
  _summaryCache = await resp.json()
  return _summaryCache
}

export async function fetchPlayerDetail(zhId) {
  const resp = await fetch(`${BASE}data/latest/players/${zhId}.json`)
  return resp.json()
}

export async function fetchPlayerHistory(zhId) {
  const resp = await fetch(`${BASE}data/history/${zhId}.json`)
  return resp.json()
}

export async function fetchChanges() {
  const resp = await fetch(`${BASE}data/latest/changes.json`)
  return resp.json()
}
