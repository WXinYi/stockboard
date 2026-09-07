// 六情绪 · 浏览器实时版(与 jiarenmens/src/analysis/six_emotions.py 成对维护)
// ⚠️ 双实现同步警示: 本文件按 Python 同一管线逐日复刻 —— 原始分量 → 历史分位 → 加权合成
//    → 3日整体 → 阈值自校准(四分位) → 主导条件。改公式/阈值必须两边同步(先例: isQuality/cycle)。
// 数据来源(与「仅观察」实时引擎同一次刷新, 不新增 KPL 请求):
//   - 历史 15 分量序列: data/latest/six_history.json(收盘后 export 刷新, 盘中恒定可缓存)
//   - 今日实时分量: battle._inputs(今日实时池/昨日全字段池/炸板池) + mood(情绪分/跌停)
//     + cycle.metrics(涨停/真实高度/破板率) + auction env.bid_total(竞价额) + 指数趋势(东财K线, 10min缓存)
// 语义: 盘中数值随盘面漂移, 跟随页面 30s 刷新节奏; 盘外/非交易日自动退化为最近可得口径。

/* ────────────── 基础算子(与 Python _pct_rank/_wsum/_q 一致) ────────────── */
function pctRank(series, value) {
  if (value === null || value === undefined) return null
  const vals = series.filter(v => v !== null && v !== undefined).sort((a, b) => a - b)
  if (!vals.length) return null
  let lo = 0, hi = vals.length
  while (lo < hi) { const mid = (lo + hi) >> 1; if (vals[mid] < value) lo = mid + 1; else hi = mid }
  return Math.round(lo / vals.length * 1000) / 10
}
function wsum(parts) {
  let num = 0, den = 0
  for (const [v, w] of parts) {
    if (v === null || v === undefined) continue
    num += v * w; den += w
  }
  return den ? Math.round(num / den * 10) / 10 : null
}
function q(vals, quant) {
  const xs = vals.filter(v => v !== null && v !== undefined).sort((a, b) => a - b)
  if (!xs.length) return 0
  return xs[Math.min(xs.length - 1, Math.floor(quant * xs.length))]
}

/* ────────────── 历史基准归一(six_history.json 紧凑键 → 全名键) ────────────── */
export function normHistory(hist) {
  return (hist?.rows || []).map(r => ({
    date: r.d, strong: r.s ?? null, dt: r.dt ?? null, broke: r.broke ?? null,
    zt: r.zt ?? null, height: r.h ?? null, promo: r.promo ?? null, relay: r.relay ?? null,
    zhaban: r.zb ?? null, top_cnt: r.tc ?? null, tc_prev: r.tc_prev ?? null,
    top_amt: r.ta ?? null, top_board: r.tb ?? null, run_days: r.run ?? null,
    switches_5d: r.sw ?? null, idx_trend: r.idx ?? null, bid_amt: r.bid ?? null,
  }))
}

/* ────────────── 今日实时分量(复用 battle._inputs 等, 不新增 KPL 请求) ────────────── */
const num = v => (typeof v === 'number' && isFinite(v) ? v : null)

export function buildLiveRow({ todayPool = [], prevFull = [], unsealed = [], history = [],
                                mood = {}, cycle = null, bidAmt = null, idxTrend = null, date = '' }) {
  const codeOf = r => r.code || r.symbol
  const codes = new Set(todayPool.map(codeOf).filter(Boolean))
  // 主线板块: 按板块聚合 家数/成交额, 家数最大者为主线(平手取成交额大)
  const cnt = {}, amt = {}
  for (const r of todayPool) {
    for (const b of (r.plates || (r.bkName ? [r.bkName] : []))) {
      if (!b) continue
      cnt[b] = (cnt[b] || 0) + 1
      amt[b] = (amt[b] || 0) + (num(r.amount) || 0)
    }
  }
  let topBoard = null
  for (const b of Object.keys(cnt)) {
    if (!topBoard || cnt[b] > cnt[topBoard] || (cnt[b] === cnt[topBoard] && (amt[b] || 0) > (amt[topBoard] || 0))) topBoard = b
  }
  // 晋级率 = 今日≥2板家数 / 昨日涨停家数; 昨高位续板率 = 昨最高板股今日仍封板比例
  const twoPlus = new Set(todayPool.filter(r => (num(r.pid) ?? num(r.level) ?? 0) >= 2).map(codeOf))
  const prevCnt = new Set(prevFull.map(codeOf).filter(Boolean)).size
  const prevMax = prevFull.reduce((m, r) => Math.max(m, num(r.pid) ?? 0), 0)
  const prevTops = prevFull.filter(r => (num(r.pid) ?? 0) === prevMax && prevMax > 0).map(codeOf)
  const relay = prevTops.length ? prevTops.filter(c => codes.has(c)).length / prevTops.length : null
  // 主线连任/近5日切换: 接历史 tb 序列续算
  const tail = history.slice(-4)
  const lastTb = tail.length ? tail[tail.length - 1].top_board : null
  const lastRun = tail.length ? (tail[tail.length - 1].run_days ?? 0) : 0
  const runDays = topBoard ? (topBoard === lastTb ? lastRun + 1 : 1) : 0
  const win = [...tail.map(r => r.top_board), topBoard]
  let sw = 0
  for (let i = 1; i < win.length; i++) if (win[i - 1] && win[i] && win[i - 1] !== win[i]) sw++
  // tc_prev = 昨日「同一主线」的家数(Python top_cnt_prev 语义, 非"昨日主线家数")
  const tcPrev = topBoard ? prevFull.reduce((n, r) => n + ((r.plates || (r.bkName ? [r.bkName] : [])).includes(topBoard) ? 1 : 0), 0) : null
  return {
    date, strong: num(mood.strong), dt: num(mood.df), broke: num(cycle?.metrics?.brokeRate),
    zt: num(cycle?.metrics?.zt), height: num(cycle?.metrics?.height),
    promo: prevCnt ? twoPlus.size / prevCnt : null, relay,
    zhaban: unsealed.length || null, top_cnt: topBoard ? cnt[topBoard] : null,
    tc_prev: tcPrev, top_amt: topBoard ? (amt[topBoard] || null) : null, top_board: topBoard,
    run_days: runDays, switches_5d: sw, idx_trend: num(idxTrend), bid_amt: num(bidAmt),
  }
}

/* ────────────── 六情绪管线(逐日复刻 compute_all, 返回最新一日) ────────────── */
export function computeSixLive(histRows, liveRow) {
  const rows = [...(histRows || [])]
  if (liveRow && liveRow.date && !rows.some(r => r.date === liveRow.date)) rows.push(liveRow)
  rows.sort((a, b) => (a.date < b.date ? -1 : 1))
  if (!rows.length) return null
  const key = k => rows.map(r => (r[k] ?? null))
  const series = {
    strong: key('strong'), dt: key('dt'), broke: key('broke'), zt: key('zt'),
    height: key('height'), promo: key('promo'), relay: key('relay'), zhaban: key('zhaban'),
    top_cnt: key('top_cnt'), top_amt: key('top_amt'), run_days: key('run_days'),
    switches_5d: key('switches_5d'), idx_trend: key('idx_trend'), bid_amt: key('bid_amt'),
    top_cnt_delta: rows.map(r => (r.top_cnt != null && r.tc_prev != null ? r.top_cnt - r.tc_prev : null)),
  }
  const dtNeg = series.dt.filter(v => v != null).map(v => -v)
  const brokeNeg = series.broke.filter(v => v != null).map(v => -v)
  const zbNeg = series.zhaban.filter(v => v != null).map(v => -v)
  const swNeg = series.switches_5d.filter(v => v != null).map(v => -v)
  const out = rows.map((r, i) => {
    const m = r
    const market = wsum([
      [pctRank(series.strong, m.strong), .3],
      [m.dt != null ? pctRank(dtNeg, -m.dt) : null, .2],
      [m.broke != null ? pctRank(brokeNeg, -m.broke) : null, .2],
      [pctRank(series.idx_trend, m.idx_trend), .15],
      [pctRank(series.bid_amt, m.bid_amt), .15],
    ])
    const spec = wsum([
      [pctRank(series.zt, m.zt), .25],
      [pctRank(series.height, m.height), .2],
      [pctRank(series.promo, m.promo), .2],
      [m.broke != null ? pctRank(brokeNeg, -m.broke) : null, .15],
      [m.zhaban != null ? pctRank(zbNeg, -m.zhaban) : null, .1],
      [pctRank(series.relay, m.relay), .1],
    ])
    const sector = wsum([
      [pctRank(series.top_cnt, m.top_cnt), .35],
      [pctRank(series.height, m.height), .25],
      [pctRank(series.top_cnt_delta, series.top_cnt_delta[i]), .25],
      [pctRank(series.top_amt, m.top_amt), .15],
    ])
    return { date: r.date, market, spec, sector }
  })
  const mktDist = out.map(o => o.market)
  const specDist = out.map(o => o.spec)
  const sectorDist = out.map(o => o.sector)
  const ztSorted = series.zt.filter(v => v != null).sort((a, b) => a - b)
  const dtSorted = series.dt.filter(v => v != null).sort((a, b) => a - b)
  const zbSorted = series.zhaban.filter(v => v != null).sort((a, b) => a - b)
  const last = rows.length - 1
  const hp = last > 0 ? rows[last - 1].height : null
  const chaosSig = [
    pctRank(ztSorted, rows[last].zt), pctRank(dtSorted, rows[last].dt), pctRank(zbSorted, rows[last].zhaban),
    (hp && rows[last].height) ? hp - rows[last].height : null,
  ]
  const th = {
    spec_p30: q(out.map(o => o.spec), .30), spec_p50: q(out.map(o => o.spec), .50),
    spec_p90: q(out.map(o => o.spec), .90), sector_p40: q(out.map(o => o.sector), .40),
    sector_p90: q(out.map(o => o.sector), .90), market_p75: q(out.map(o => o.market), .75),
    zhaban_p75: q(series.zhaban, .75),
  }
  // m_* 先放入 out(供阈值分位用), 再对最新日判主导(与 Python 两遍结构等价)
  for (let i = 0; i < rows.length; i++) {
    const w = []
    for (let k = Math.max(0, i - 2); k <= i; k++) w.push(k)   // 长度 1..3, 与 Python win3 切片一致
    const arr = w.map(k => out[k])
    const mk = arr.map(o => o.market).filter(v => v != null)
    const sp = arr.map(o => o.spec).filter(v => v != null)
    const sec = arr.map(o => o.sector).filter(v => v != null)
    out[i].m_market = mk.length ? pctRank(mktDist, mk.reduce((a, b) => a + b, 0) / w.length) : null
    out[i].m_spec = sp.length ? pctRank(specDist, sp.reduce((a, b) => a + b, 0) / w.length) : null
    out[i].m_sector = sec.length ? wsum([
      [pctRank(sectorDist, sec.reduce((a, b) => a + b, 0) / w.length), .5],
      [pctRank(series.run_days, rows[i].run_days), .3],
      [rows[i].switches_5d != null ? pctRank(swNeg, -rows[i].switches_5d) : null, .2],
    ]) : null
  }
  th.m_spec_p75 = q(out.map(o => o.m_spec), .75)
  th.m_sector_p75 = q(out.map(o => o.m_sector), .75)
  const res = { ...out[last], date: rows[last].date, chaos_sig: chaosSig,
    top_board: rows[last].top_board, top_cnt: rows[last].top_cnt, height: rows[last].height,
    zt: rows[last].zt, dt: rows[last].dt, zhaban: rows[last].zhaban, broke: rows[last].broke }
  Object.assign(res, dominantOf(res, rows[last], th))
  return res
}

function dominantOf(o, m, th) {
  const [ztPct, dtPct, zbPct, drop] = o.chaos_sig || []
  if (ztPct != null && ztPct >= 75 && drop != null && 1 <= drop && drop <= 2
      && ((dtPct != null && dtPct >= 60) || (zbPct != null && zbPct >= 70))
      && m.zt && (m.dt || 0) / m.zt <= 0.25) {
    return { dominant: '混沌过渡', note: '退潮尾声→新周期试错：只做辨识度/低位火种，试探仓，等放量确认再加' }
  }
  const specCollapse = o.m_spec != null && o.spec != null && o.spec <= o.m_spec - 30
  const dtExtreme = dtPct != null && dtPct >= 95
  if ((((o.spec != null && o.spec <= th.spec_p30) || dtExtreme)
        && ((m.dt || 0) >= 5 || (m.zt != null && m.zt <= 30))) || specCollapse) {
    return { dominant: '退潮防守', note: '不强行交易，等新情绪确认/新核心出现' }
  }
  if ((o.sector || 0) >= th.sector_p90 && (o.m_sector || 0) >= th.m_sector_p75) {
    return { dominant: '板块情绪极强', note: '主攻龙头板/换手核心/中军，低位补涨前排' }
  }
  if ((o.spec || 0) >= th.spec_p90 && (o.m_spec || 0) >= th.m_spec_p75) {
    return { dominant: '投机情绪极强', note: '低位超强前排/高板块情绪点/换手核心' }
  }
  if ((o.market || 0) >= th.market_p75 && (o.sector || 0) <= th.sector_p40) {
    return { dominant: '市场强但板块不强', note: '降低预期，只做低风险活跃点' }
  }
  if ((m.zhaban || 0) >= th.zhaban_p75 && (o.spec || 0) >= th.spec_p50) {
    return { dominant: '分歧但情绪不差', note: '等确认，选确定性出手' }
  }
  return { dominant: '混沌观察', note: '只做辨识度最高标的' }
}

/* ────────────── 指数趋势(东财日K, 10min 缓存; 非 KPL 接口, 不与盘面请求重叠) ────────────── */
let _idxCache = { at: 0, val: null }
export async function fetchIndexTrend(now = Date.now()) {
  if (now - _idxCache.at < 10 * 60 * 1000) return _idxCache.val
  const { jsonp } = await import('./eastmoney.js')
  const url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001'
    + '&fields1=f1,f2,f3&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt=8'
  try {
    const j = await jsonp(url, 'cb')
    const ks = (j?.data?.klines || []).map(s => parseFloat(String(s).split(',')[1])).filter(v => isFinite(v))
    if (ks.length >= 6) {
      const last = ks[ks.length - 1]
      const ma5 = ks.slice(-6, -1).reduce((a, b) => a + b, 0) / 5
      _idxCache = { at: now, val: ma5 ? Math.round((last / ma5 - 1) * 10000) / 100 : null }
    }
  } catch { _idxCache = { at: now, val: null } }
  return _idxCache.val
}

/** auction env.data.bid_total('164亿') → 数字(亿) */
export function parseBidYi(txt) {
  const m = String(txt || '').match(/([\d.]+)\s*亿/)
  return m ? parseFloat(m[1]) : null
}
