// 技术指标纯函数: 从 kline [{time,open,close,high,low,volume}] 浏览器端现算
// 所有函数返回与 kline 等长的数组, 周期不足处为 null
// 口径: 通达信/同花顺常用公式, 定位为参考

function sma(vals, n) {
  const out = []
  let sum = 0
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i]
    if (i >= n) sum -= vals[i - n]
    out.push(i >= n - 1 ? sum / n : null)
  }
  return out
}

function ema(vals, n) {
  const out = []
  const k = 2 / (n + 1)
  let prev = null
  for (let i = 0; i < vals.length; i++) {
    prev = prev === null ? vals[i] : vals[i] * k + prev * (1 - k)
    out.push(prev)
  }
  return out
}

// 中国式加权平均 SMA(X,N,M): Y=(M*X+(N-M)*Y')/N (通达信 RSI 用)
function tnxSma(vals, n, m) {
  const out = []
  let prev = null
  for (let i = 0; i < vals.length; i++) {
    prev = prev === null ? vals[i] : (m * vals[i] + (n - m) * prev) / n
    out.push(prev)
  }
  return out
}

function stddev(vals, n) {
  const out = []
  for (let i = 0; i < vals.length; i++) {
    if (i < n - 1) { out.push(null); continue }
    let s = 0
    for (let j = i - n + 1; j <= i; j++) s += vals[j]
    const avg = s / n
    let v = 0
    for (let j = i - n + 1; j <= i; j++) v += (vals[j] - avg) ** 2
    out.push(Math.sqrt(v / n))
  }
  return out
}

// 滑动窗口极值
function rollingExtremes(kl, n, get) {
  const out = []
  for (let i = 0; i < kl.length; i++) {
    if (i < n - 1) { out.push(null); continue }
    let hh = -Infinity, ll = Infinity
    for (let t = i - n + 1; t <= i; t++) {
      const v = get(kl[t])
      hh = Math.max(hh, v); ll = Math.min(ll, v)
    }
    out.push({ hh, ll })
  }
  return out
}

// ── 均线 MA5/10/20/60 ──
export function calcMA(kl, ns = [5, 10, 20, 60]) {
  const closes = kl.map(k => k.close)
  const res = {}
  for (const n of ns) res[n] = sma(closes, n)
  return res
}

// ── BOLL(20,2) 布林带 ──
export function calcBOLL(kl, n = 20, mult = 2) {
  const closes = kl.map(k => k.close)
  const mid = sma(closes, n)
  const sd = stddev(closes, n)
  const up = [], lo = []
  for (let i = 0; i < closes.length; i++) {
    if (mid[i] === null) { up.push(null); lo.push(null) }
    else { up.push(mid[i] + mult * sd[i]); lo.push(mid[i] - mult * sd[i]) }
  }
  return { up, mid, lo }
}

// ── MACD(12,26,9): DIF/DEA/柱=2×(DIF-DEA) ──
export function calcMACD(kl, fast = 12, slow = 26, signal = 9) {
  const closes = kl.map(k => k.close)
  const eF = ema(closes, fast), eS = ema(closes, slow)
  const dif = closes.map((_, i) => eF[i] - eS[i])
  const dea = ema(dif, signal)
  const hist = dif.map((v, i) => (v - dea[i]) * 2)
  return { dif, dea, hist }
}

// ── KDJ(9,3,3) ──
export function calcKDJ(kl, n = 9, k1 = 3, d1 = 3) {
  const k = [], d = [], j = []
  let pk = 50, pd = 50
  for (let i = 0; i < kl.length; i++) {
    let rsv = 50
    if (i >= n - 1) {
      let hh = -Infinity, ll = Infinity
      for (let t = i - n + 1; t <= i; t++) { hh = Math.max(hh, kl[t].high); ll = Math.min(ll, kl[t].low) }
      rsv = hh === ll ? 50 : ((kl[i].close - ll) / (hh - ll) * 100)
    }
    pk = (pk * (k1 - 1) + rsv) / k1
    pd = (pd * (d1 - 1) + pk) / d1
    k.push(+pk.toFixed(3)); d.push(+pd.toFixed(3)); j.push(+(3 * pk - 2 * pd).toFixed(3))
  }
  return { k, d, j }
}

// ── RSI(6/12/24) 通达信口径 ──
export function calcRSI(kl, ns = [6, 12, 24]) {
  const closes = kl.map(k => k.close)
  const up = [0], dn = [0]
  for (let i = 1; i < closes.length; i++) {
    const chg = closes[i] - closes[i - 1]
    up.push(Math.max(chg, 0)); dn.push(Math.abs(chg))
  }
  const res = {}
  for (const n of ns) {
    const su = tnxSma(up, n, 1), sd = tnxSma(dn, n, 1)
    res[n] = closes.map((_, i) => sd[i] === 0 ? (su[i] === 0 ? 50 : 100) : +(su[i] / sd[i] * 100).toFixed(2))
  }
  return res
}

// ── WR 威廉(10/6) 0-100 ──
export function calcWR(kl, ns = [10, 6]) {
  const res = {}
  for (const n of ns) {
    const ex = rollingExtremes(kl, n, k => k.high)
    const out = []
    for (let i = 0; i < kl.length; i++) {
      const e = ex[i]
      if (!e) { out.push(null); continue }
      let ll = Infinity
      for (let t = i - n + 1; t <= i; t++) ll = Math.min(ll, kl[t].low)
      out.push(e.hh === ll ? 0 : +((e.hh - kl[i].close) / (e.hh - ll) * 100).toFixed(2))
    }
    res[n] = out
  }
  return res
}

// ── 成交量均量 VOL5/VOL10 ──
export function calcVOLMA(kl, ns = [5, 10]) {
  const vols = kl.map(k => k.volume)
  const res = {}
  for (const n of ns) res[n] = sma(vols, n)
  return res
}

// ══════════════════════════════════════════════════════════════
// 缠论(简化主流口径, 定位为参考标记, 非唯一标准)
// ══════════════════════════════════════════════════════════════

// 顶底分型: 中间bar的最高/最低为三根中极值
// 返回 [{ i, type: 1=顶分型, -1=底分型 }] 按索引升序
export function calcFractals(kl) {
  const out = []
  for (let i = 1; i < kl.length - 1; i++) {
    const a = kl[i - 1], b = kl[i], c = kl[i + 1]
    if (b.high > a.high && b.high > c.high) out.push({ i, type: 1 })
    else if (b.low < a.low && b.low < c.low) out.push({ i, type: -1 })
  }
  return out
}

// 笔: 相邻同向分型合并(保留更极端), 顶底交替且间隔(中bar索引差)≥5
// 返回 [{ from, to }] from/to 为分型对象 { i, type }
export function calcBis(kl) {
  const fs = calcFractals(kl)
  const merged = []
  for (const f of fs) {
    const last = merged[merged.length - 1]
    if (last && last.type === f.type) {
      const moreExtreme = f.type === 1 ? kl[f.i].high > kl[last.i].high : kl[f.i].low < kl[last.i].low
      if (moreExtreme) merged[merged.length - 1] = f
    } else merged.push(f)
  }
  const bis = []
  let prev = null
  for (const f of merged) {
    if (prev && prev.type !== f.type && f.i - prev.i >= 5) {
      bis.push({ from: prev, to: f })
      prev = f
    } else if (!prev) prev = f
  }
  return bis
}

// 笔的价格区间(分型极值)
function bisRange(kl, b) {
  const p1 = b.from.type === 1 ? kl[b.from.i].high : kl[b.from.i].low
  const p2 = b.to.type === 1 ? kl[b.to.i].high : kl[b.to.i].low
  return { lo: Math.min(p1, p2), hi: Math.max(p1, p2) }
}

// 中枢: 连续3笔区间有共同重叠 → { zg上沿, zd下沿, from, to }
export function calcZhongshu(kl, bis) {
  const zs = []
  let start = 0
  while (start + 2 < bis.length) {
    const segs = []
    for (let k = start; k < start + 3; k++) segs.push(bisRange(kl, bis[k]))
    const zg = Math.min(...segs.map(s => s.hi))
    const zd = Math.max(...segs.map(s => s.lo))
    if (zg > zd) {
      zs.push({ zg, zd, from: bis[start].from.i, to: bis[start + 2].to.i })
      start += 3   // 简化: 中枢不重叠
    } else start += 1
  }
  return zs
}

// 三类买卖点(启发式简化, 参考):
// 1买 = 底背驰底分型(价创新低 + MACD DIF 抬高); 1卖 = 顶背驰顶分型(对称)
// 2买 = 1买后第一个底分型; 2卖 = 1卖后第一个顶分型
// 3买 = 1买后价格高于最近中枢上沿的底分型(回踩不破); 3卖对称
// 返回 [{ i, type: '1buy'|'2buy'|'3buy'|'1sell'|'2sell'|'3sell' }]
export function calcChanSignals(kl, macd, bis, zhongshu) {
  const signals = []
  const fs = calcFractals(kl)
  const bottoms = fs.filter(f => f.type === -1)
  const tops = fs.filter(f => f.type === 1)

  // 买点
  let lastBuy1 = -1
  for (let b = 0; b < bottoms.length; b++) {
    const f = bottoms[b]
    if (lastBuy1 >= 0 && f.i > lastBuy1) {
      signals.push({ i: f.i, type: '2buy' })
      lastBuy1 = -1   // 该轮 2买 完成, 重置等下一个 1买
    } else {
      const prev = b > 0 ? bottoms[b - 1] : null
      if (prev && kl[f.i].low < kl[prev.i].low && macd.dif[f.i] > macd.dif[prev.i]) {
        signals.push({ i: f.i, type: '1buy' })
        lastBuy1 = f.i
        // 3买: 1买之后, 价格高于其前最近中枢上沿的底分型(取首个)
        for (const g of bottoms) {
          if (g.i <= f.i) continue
          const recent = [...zhongshu].reverse().find(z => z.to < g.i)
          if (recent && kl[g.i].low > recent.zg) { signals.push({ i: g.i, type: '3buy' }); break }
        }
      }
    }
  }
  // 卖点(对称)
  let lastSell1 = -1
  for (let t = 0; t < tops.length; t++) {
    const f = tops[t]
    if (lastSell1 >= 0 && f.i > lastSell1) {
      signals.push({ i: f.i, type: '2sell' })
      lastSell1 = -1
    } else {
      const prev = t > 0 ? tops[t - 1] : null
      if (prev && kl[f.i].high > kl[prev.i].high && macd.dif[f.i] < macd.dif[prev.i]) {
        signals.push({ i: f.i, type: '1sell' })
        lastSell1 = f.i
        for (const g of tops) {
          if (g.i <= f.i) continue
          const recent = [...zhongshu].reverse().find(z => z.to < g.i)
          if (recent && kl[g.i].high < recent.zd) { signals.push({ i: g.i, type: '3sell' }); break }
        }
      }
    }
  }
  return signals
}

// ══════════════════════════════════════════════════════════════
// 波浪理论(艾略特, 启发式简化, 定位为参考标记, 无法判定时明示)
// ══════════════════════════════════════════════════════════════

// 摆动点序列: 合并同向分型(保留更极端) + 异向间隔≥2, 交替顶底
function swingPoints(kl) {
  const fs = calcFractals(kl)
  const pts = []
  for (const f of fs) {
    const last = pts[pts.length - 1]
    if (last && last.type === f.type) {
      const more = f.type === 1 ? kl[f.i].high > kl[last.i].high : kl[f.i].low < kl[last.i].low
      if (more) pts[pts.length - 1] = f
    } else if (!last || f.i - last.i >= 2) {
      pts.push(f)
    } else {
      const more = f.type === 1 ? kl[f.i].high > kl[last.i].high : kl[f.i].low < kl[last.i].low
      if (more) pts[pts.length - 1] = f
    }
  }
  return pts
}

// 自动计数 5浪推动 + 3浪调整(1-2-3-4-5-A-B-C), 从最近摆动点向前找最后一个匹配:
// 上升推动: 2浪不破1浪起点, 3浪新高, 4浪不破1浪顶, 5浪新高, A-C 调整(A下B反弹C破A低)
// 下跌推动镜像。均满足才判定, 否则 status 'unknown' → 前端显示「无法判定」
export function calcWaves(kl) {
  const pts = swingPoints(kl)
  if (pts.length < 9) return { status: 'unknown', waves: [] }
  const hi = i => kl[pts[i].i].high
  const lo = i => kl[pts[i].i].low
  for (let s = pts.length - 9; s >= 0; s--) {
    const up = pts[s].type === -1   // 起于底 → 上升推动浪
    if (up) {
      if (!(lo(s + 2) > lo(s))) continue           // 2浪不破1浪起点
      if (!(hi(s + 3) > hi(s + 1))) continue       // 3浪创新高
      if (!(lo(s + 4) > hi(s + 1))) continue       // 4浪不破1浪顶(简化不重叠)
      if (!(hi(s + 5) > hi(s + 3))) continue       // 5浪创新高
      if (!(hi(s + 7) < hi(s + 5))) continue       // B浪不破5浪顶
      if (!(lo(s + 8) < lo(s + 6))) continue       // C浪破A浪低点
    } else {
      if (!(hi(s + 2) < hi(s))) continue           // 2浪不破1浪起点
      if (!(lo(s + 3) < lo(s + 1))) continue       // 3浪创新低
      if (!(hi(s + 4) < lo(s + 1))) continue       // 4浪不破1浪底
      if (!(lo(s + 5) < lo(s + 3))) continue       // 5浪创新低
      if (!(lo(s + 7) > lo(s + 5))) continue       // B浪不破5浪底
      if (!(hi(s + 8) > hi(s + 6))) continue       // C浪破A浪高点
    }
    // 从后向前扫, 首次命中即最新完整结构
    const labels = ['起', '1', '2', '3', '4', '5', 'A', 'B', 'C']
    const waves = pts.slice(s, s + 9).map((p, k) => ({ i: p.i, type: p.type, label: labels[k] }))
    return { status: 'ok', waves, dir: up ? 1 : -1 }
  }
  return { status: 'unknown', waves: [] }
}
