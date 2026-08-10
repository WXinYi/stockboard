// 竞价分时 → SVG 点序列(涨停~跌停 Y 轴, 与分时一致)
// 输入: bid=[{time, price, side, cumVol}](09:15-09:25), prevClose(红涨绿跌/坐标基准)
// 输出: { line:[{x,y}], bars:[{x,y,h,color}] } — viewBox 0 0 w h; 昨收参考线 y=(up-昨收)/(up-down)*(h-8)
export function bidToPoints(bid, prevClose, w = 220, h = 64) {
  const n = bid.length
  if (!n) return { line: [], bars: [] }
  const up = prevClose * 1.1, down = prevClose * 0.9   // 涨停/跌停(近似, 详细板规则可后续接 calcLimitPx)
  const xw = n > 1 ? w / (n - 1) : 0
  const yp = (px) => (up - px) / (up - down) * (h - 8)
  const line = bid.map((p, i) => ({ x: i * xw, y: yp(p.price) }))
  const maxVol = Math.max(...bid.map(p => p.cumVol), 1)
  const bars = bid.map((p, i) => ({
    x: i * xw,
    y: h - 8 - (p.cumVol / maxVol) * (h - 8),
    h: (p.cumVol / maxVol) * (h - 8),
    color: p.price >= prevClose ? '#e74c3c' : '#27ae60',
  }))
  return { line, bars }
}
