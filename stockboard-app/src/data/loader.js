// 从 public/data/ 加载 JSON 数据
const BASE = import.meta.env.BASE_URL

export async function fetchIndex() {
  const resp = await fetch(`${BASE}data/index.json`)
  return resp.json()
}

export async function fetchDateData(date) {
  const [players, positions, trades] = await Promise.all([
    fetch(`${BASE}data/${date}/players.json`).then(r => r.json()),
    fetch(`${BASE}data/${date}/positions.json`).then(r => r.json()),
    fetch(`${BASE}data/${date}/trades.json`).then(r => r.json()),
  ])
  return { players, positions, trades }
}
