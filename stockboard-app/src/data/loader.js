// 从 public/data/ 加载 JSON 数据
export async function fetchIndex() {
  const resp = await fetch('/data/index.json')
  return resp.json()
}

export async function fetchDateData(date) {
  const [players, positions, trades] = await Promise.all([
    fetch(`/data/${date}/players.json`).then(r => r.json()),
    fetch(`/data/${date}/positions.json`).then(r => r.json()),
    fetch(`/data/${date}/trades.json`).then(r => r.json()),
  ])
  return { players, positions, trades }
}
