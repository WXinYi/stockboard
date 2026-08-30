// 龙头博弈 + 今日出击 · 浏览器端纯规则引擎(不用大模型, 全部确定性规则, 可回测)
// ⚠️ 「今日出击」的阶段闸门/候选规则与 jiarenmens/src/analysis/stage_candidates.py 对齐, 改规则两边同步。
// 板块之争/高标对决/半路候选为 JS 展示层实现(暂无 Python 对应); 若日后移植到推送, 需成对维护。
// 评分权重为经验值, 待 auction.db 历史回测校准(M3); 评分=胜率优先排序, 非收益保证。

const yi = v => (v >= 1e8 ? (v / 1e8).toFixed(2) + '亿' : v >= 1e4 ? (v / 1e4).toFixed(0) + '万' : String(v || 0))
// unix秒 → 北京时间 'HH:MM' (KPL 时间戳为东八区)
const hhmm = ts => (ts ? new Date((ts + 8 * 3600) * 1000).toISOString().slice(11, 16) : null)
const sealRatio = r => (r && r.maxSeal > 0 ? r.seal / r.maxSeal : null)
const pctTxt = v => (v === null || v === undefined ? '' : Math.round(v * 100) + '%')

// 今日池(板位桶) + 天梯(精确level) 合并; 一字板 ztTime 可能缺失按 09:25 处理
function joinToday(ladderRows, todayPool) {
  const lvMap = new Map(ladderRows.map(r => [r.code, r]))
  const seen = new Set()
  const out = []
  for (const p of todayPool) {
    if (seen.has(p.code)) continue
    seen.add(p.code)
    const lad = lvMap.get(p.code)
    out.push({
      ...p,
      level: lad && lad.level ? lad.level : (p.pid >= 5 ? 5 : p.pid),
      bkName: lad?.bkName || p.plates[0] || '',
      plates: p.plates.length ? p.plates : (lad?.bkName ? [lad.bkName] : []),
    })
  }
  // 天梯里有而 RT 池没返回的(极少), 用天梯行补上保证梯队完整
  for (const lad of ladderRows) {
    if (!seen.has(lad.code)) out.push({ ...lad, seal: 0, maxSeal: 0, ztTime: null, mainNet: 0, amount: 0, reason: '', turnover: 0, circMv: 0 })
  }
  return out
}

// 按板块聚合: {board → {count, maxH, sealSum, members:Set}}
function boardAgg(rows) {
  const agg = {}
  for (const r of rows) {
    for (const b of r.plates || []) {
      if (!b) continue
      const a = agg[b] || (agg[b] = { board: b, count: 0, maxH: 0, seal: 0, members: new Set() })
      a.count++
      a.maxH = Math.max(a.maxH, r.level ?? r.pid ?? 0)
      a.seal += r.seal || 0
      a.members.add(r.code)
    }
  }
  return agg
}

/* ============ ⚔️ 板块之争 ============ */
function computeBoardWars(todayJoined, prevFull) {
  const t = boardAgg(todayJoined)
  const p = boardAgg(prevFull.map(r => ({ ...r, level: r.pid >= 5 ? 5 : r.pid })))
  const keys = new Set([...Object.keys(t).filter(b => t[b].count >= 2), ...Object.keys(p).filter(b => p[b].count >= 2)])
  const wars = [...keys].map(b => {
    const a = t[b] || { count: 0, maxH: 0, seal: 0 }
    const y = p[b] || { count: 0, maxH: 0 }
    return { board: b, count: a.count, prevCount: y.count, dCount: a.count - y.count, maxH: a.maxH, prevMaxH: y.maxH, sealSum: a.seal, members: a.members || new Set() }
  }).sort((x, y2) => y2.count - x.count || y2.maxH - x.maxH)

  const prevTop3 = wars.filter(w => w.prevCount >= 2).sort((x, y2) => y2.prevCount - x.prevCount).slice(0, 3).map(w => w.board)
  for (const w of wars) {
    const idx = wars.indexOf(w)
    if (idx <= 1 && w.count >= 3) w.tag = '主线'
    else if (w.dCount >= 2 && w.prevCount > 0 && !prevTop3.includes(w.board) && idx <= 2) w.tag = '卡位上位'
    else if (w.dCount >= 2) w.tag = '扩容'
    else if (w.dCount <= -2) w.tag = '萎缩·被抽血'
    else w.tag = '轮动'
  }

  // 关系对: 今日 top3 板块两两判定(联动/竞争/同源)
  const relations = []
  const top3 = wars.slice(0, 3)
  for (let i = 0; i < top3.length; i++) {
    for (let j = i + 1; j < top3.length; j++) {
      const A = top3[i], B = top3[j]
      const overlap = [...A.members].filter(c => B.members.has(c)).length
      if (A.dCount >= 1 && B.dCount >= 1) relations.push({ a: A.board, b: B.board, rel: '同涨共振', note: `双板块同日扩容(${A.dCount >= 0 ? '+' : ''}${A.dCount}/${B.dCount >= 0 ? '+' : ''}${B.dCount})，合力攻击` })
      else if (A.dCount >= 2 && B.dCount <= -1) relations.push({ a: A.board, b: B.board, rel: '竞争切换', note: `${A.board}吸${B.board}血：资金切换方向` })
      else if (B.dCount >= 2 && A.dCount <= -1) relations.push({ a: B.board, b: A.board, rel: '竞争切换', note: `${B.board}吸${A.board}血：资金切换方向` })
      else if (overlap >= 2) relations.push({ a: A.board, b: B.board, rel: '同源共振', note: `成分重叠 ${overlap} 票，实为同一题材` })
    }
  }
  // 主线切换: 昨日涨停数第一 vs 今日第一
  let mainSwitch = null
  const todayTop = wars[0], prevTop = wars.filter(w => w.prevCount >= 3).sort((x, y2) => y2.prevCount - x.prevCount)[0]
  if (todayTop && prevTop && todayTop.board !== prevTop.board && todayTop.count >= 3) {
    mainSwitch = { from: prevTop.board, to: todayTop.board, note: `主线切换 ${prevTop.board}(${prevTop.prevCount}只) → ${todayTop.board}(${todayTop.count}只)` }
  }
  return { wars, relations, mainSwitch }
}

/* ============ 🥊 高标对决 + 断板接棒 ============ */
function computeDuels(todayJoined, prevFull, boardWars) {
  const duels = []
  const highs = todayJoined.filter(r => r.level >= 3)
  const byBoardLv = {}
  for (const r of highs) {
    for (const b of r.plates || []) {
      const k = `${b}|${r.level}`
      ;(byBoardLv[k] || (byBoardLv[k] = [])).push(r)
    }
  }
  // 1) 同板块同高度之争: 先封+封单定压制
  for (const [k, arr] of Object.entries(byBoardLv)) {
    if (arr.length < 2) continue
    const [board] = k.split('|')
    const sorted = arr.sort((x, y) => (x.ztTime || 0) - (y.ztTime || 0))
    const w = sorted[0], l = sorted[1]
    let verdict = `${w.name} ${hhmm(w.ztTime) || '一字'} 先手 · 封单${yi(w.seal)}/${yi(l.seal)}`
    const rw = sealRatio(w), rl = sealRatio(l)
    if (rw !== null && rl !== null && rl < rw * 0.6) verdict += `；${l.name} 封单保持${pctTxt(rl)} vs ${pctTxt(rw)}，随时让位`
    duels.push({ type: '同板同高之争', board, a: w, b: l, verdict, level: w.level })
  }
  // 2) 同板块高度差1: 卡位接棒关系
  const seen = new Set(duels.map(d => `${d.board}|${d.level}`))
  for (const r of highs) {
    if (r.level < 4) continue
    for (const b of r.plates || []) {
      const subs = todayJoined.filter(x => x.level === r.level - 1 && x.level >= 2 && (x.plates || []).includes(b))
      if (!subs.length || seen.has(`${b}|${r.level}`)) continue
      seen.add(`${b}|${r.level}`)
      const y = subs.sort((x, z) => (x.ztTime || 0) - (z.ztTime || 0))[0]
      let verdict = `若 ${r.name}(${r.level}板)断板，${y.name}(${y.level}板)卡位接棒`
      const ratio = sealRatio(r)
      if ((r.level >= 5 && (r.seal || 0) > 0 && r.seal < 5e7) || (ratio !== null && ratio < 0.5)) verdict += `；⚠️${r.name}封单走弱，卡位概率升高`
      duels.push({ type: '梯队卡位', board: b, a: r, b: y, verdict, level: r.level })
    }
  }
  // 3) 跨主线龙头对标
  const topBoards = boardWars.wars.slice(0, 2)
  if (topBoards.length === 2) {
    const [A, B] = topBoards
    const la = todayJoined.filter(r => (r.plates || []).includes(A.board)).sort((x, y) => y.level - x.level)[0]
    const lb = todayJoined.filter(r => (r.plates || []).includes(B.board)).sort((x, y) => y.level - x.level)[0]
    if (la && lb && la.level >= 3 && lb.level >= 3) {
      let rel = '并行轮动', note = `${A.board}龙头 ${la.name}(${la.level}板) vs ${B.board}龙头 ${lb.name}(${lb.level}板)`
      if (A.dCount >= 1 && B.dCount >= 1) rel = '双主线共振'
      else if (A.dCount >= 2 && B.dCount <= -1) rel = `${A.board}吸${B.board}血`
      else if (B.dCount >= 2 && A.dCount <= -1) rel = `${B.board}吸${A.board}血`
      duels.push({ type: '跨主线对标', board: `${A.board}×${B.board}`, a: la, b: lb, verdict: `${note} → ${rel}`, level: Math.max(la.level, lb.level) })
    }
  }
  // 4) 昨日≥4板今断板 → 谁接棒
  const todayCodes = new Set(todayJoined.map(r => r.code))
  const brokenPrev = prevFull.filter(r => r.pid >= 4 && !todayCodes.has(r.code))
    .sort((x, y) => y.pid - x.pid).slice(0, 3)
  for (const r of brokenPrev) {
    const hTxt = r.pid >= 5 ? '≥5' : r.pid
    const succ = todayJoined.filter(x => (x.plates || []).some(b => (r.plates || []).includes(b)) && x.level >= 2)
      .sort((x, y) => y.level - x.level)[0]
    duels.push({
      type: '断板接棒', board: (r.plates || [])[0] || '', a: r, b: succ || null, level: r.pid,
      verdict: `昨日${hTxt}板 ${r.name} 今日断板 → ${succ ? `${succ.name}(${succ.level}板)已卡位接棒` : '板块无人接棒·退潮'}`,
    })
  }
  return duels.sort((x, y) => y.level - x.level).slice(0, 8)
}

/* ============ 🎯 今日出击(阶段闸门+确定性评分) ============ */
const STAGE_GATE = {
  退潮: { cap: 0, banner: '空仓纪律：退潮期不出击，高位接力亏损率最高，只观察空间锚' },
  冰点: { cap: 30, banner: '冰点期：只做 1进2 套利与新周期火种观察，仓位轻' },
  启动: { cap: 100, banner: '启动期：打低位首板/1进2 为主，情绪低点做龙头' },
  发酵: { cap: 100, banner: '发酵期：上主线龙头/同梯队强者，五板封住定龙头' },
  高潮: { cap: 100, banner: '高潮期：只做龙头接力(秒板/放量分歧板)，跟风不碰' },
  分歧: { cap: 60, banner: '分歧期：只抱团龙头低吸，避开中位股(核按钮高发)' },
}

function computeStrike(cycle, todayJoined, prevFull, unsealed, boardWars) {
  const stage = cycle.stage
  const gate = STAGE_GATE[stage] || { cap: 100, banner: '' }
  const mainBoards = (cycle.mainlines || []).slice(0, 2).map(m => m.board)
  const deltaOf = {}
  for (const w of boardWars.wars) deltaOf[w.board] = w.dCount
  const leaderCodes = new Set((cycle.leaders || []).map(l => l.code))
  const prevPid1 = new Set(prevFull.filter(r => r.pid === 1).map(r => r.code))
  const pool = new Map([...todayJoined.map(r => [r.code, r]), ...unsealed.map(u => [u.code, u])])
  const cands = []

  const add = (row, base, mode, logic) => {
    const c = cands.find(x => x.code === row.code)
    if (c) { if (!c.logic.includes(logic)) c.logic += `；${logic}`; return }
    cands.push({ code: row.code, name: row.name, level: row.level ?? row.pid ?? 0, base, mode, logic })
  }

  // 1) 龙头谱系: 状态由 阶段×角色 决定(与 stage_candidates.py 对齐)
  const leadMode = { 高潮: '排板接力', 发酵: '排板', 分歧: '低吸不追高' }
  for (const l of cycle.leaders || []) {
    const row = pool.get(l.code) || { code: l.code, name: l.name, level: l.pid, plates: [] }
    const role = l.role || ''
    if (role.includes('中军')) add(row, 50, '观察(容量)', `主线容量核心 ${l.note}`)
    else if (role.includes('补涨')) add(row, 45, stage === '发酵' || stage === '分歧' ? '低吸补涨' : '观察', `龙头被关时的板块内补涨位`)
    else add(row, role.includes('板块龙头') ? 60 : 70, leadMode[stage] || '观察', `龙头谱系[${role}] ${l.note}`)
  }
  // 2) 阶段扩展候选
  if (stage === '发酵') {
    for (const r of todayJoined) {
      if (leaderCodes.has(r.code) || !(r.plates || []).some(b => mainBoards.includes(b))) continue
      if (r.level >= 2 && r.level <= 5) add(r, 50, '排板/半路', `主线梯队 ${r.level}板`)
    }
  } else if (stage === '高潮') {
    const top = [...todayJoined].sort((x, y) => y.level - x.level).slice(0, 6)
    for (const r of top) {
      if (leaderCodes.has(r.code) || !(r.plates || []).some(b => mainBoards.includes(b)) || !(r.level >= 3)) continue
      add(r, 45, '谨慎接力', `主线高位 ${r.level}板(秒板接力对象)`)
    }
  } else if (stage === '启动' || stage === '冰点') {
    for (const r of todayJoined) {
      if (prevPid1.has(r.code) && r.level >= 2) add(r, 45, '1进2排板', `昨日首板今晋级${r.level}板(1进2确认)`)
      else if (stage === '冰点' && r.level >= 2) add(r, 30, '观察', `逆市连板(冰点火种) ${r.level}板`)
    }
  }
  // 3) 盘中半路(JS实时维度): 未涨停池主线票 涨幅3-7% 主力净买
  if (stage !== '退潮') {
    for (const u of unsealed) {
      if (u.pid !== 1 || !(u.plates || []).some(b => mainBoards.includes(b))) continue
      if (u.pct >= 3 && u.pct <= 7 && u.mainNet > 0) add({ ...u, level: 0 }, 40, '半路', `昨首板今涨${u.pct.toFixed(1)}%未封·主线内半路`)
    }
  }

  // 评分: 底分 + 封单/首封/主力/板块 加减分, 再乘阶段上限
  for (const c of cands) {
    const row = pool.get(c.code) || {}
    let score = c.base
    const strengths = []
    c.risk = ''
    const ratio = sealRatio(row)
    // 封单衰减对齐 emotion_cycle.py 口径: ≥5板 且 当前封单<5000万
    if ((row.level || 0) >= 5 && (row.seal || 0) > 0 && row.seal < 5e7) {
      score -= 15; c.risk = '封单衰减随时开板'
    } else if (ratio !== null && ratio > 0) {
      if (ratio >= 0.9) { score += 10; strengths.push('封单保持90%+') }
      else if (ratio >= 0.7) score += 5
    }
    const t = hhmm(row.ztTime)
    if (t && t <= '09:35') { score += 8; strengths.push(`早封${t}`) }
    else if (t && t <= '10:00') score += 4
    if ((row.mainNet || 0) > 0) { score += 4; strengths.push('主力净买') }
    const bd = (row.plates || []).map(b => deltaOf[b]).filter(v => v !== undefined).sort((x, y) => y - x)[0]
    if (bd !== undefined) {
      if (bd >= 2) { score += 8; strengths.push('板块扩容') }
      else if (bd <= -2) { score -= 10; if (!c.risk) c.risk = '板块被抽血' }
    }
    c.score = Math.max(0, Math.min(gate.cap, score))
    const buyable = c.mode !== '观察' && !c.mode.startsWith('观察')
    c.status = gate.cap === 0 || !buyable ? '观察' + (gate.cap === 0 ? '(阶段禁买)' : '') : c.score >= 75 ? '出击' : c.score >= 55 ? '备选' : '观察'
    c.strength = strengths.join(' · ')
    c.sealTxt = row.seal ? `封单${yi(row.seal)}` : ''
    c.platesTxt = (row.plates || []).slice(0, 2).join('/')
  }
  const anchor = (cycle.leaders || [])[0]
  return {
    gate: { stage, cap: gate.cap, banner: gate.banner, playbook: cycle.playbook },
    anchor: anchor ? `${anchor.name}(${anchor.pid}板)` : '',
    candidates: cands.sort((x, y) => y.score - x.score || y.level - x.level).slice(0, 8),
    disclaimer: '规则评分=胜率优先排序(待历史回测校准)，非收益保证；严格执行止损',
  }
}

/* ============ 🚨 高标开板 + 🔭 明日卡位雷达 ============ */
function computeRisks(todayJoined, unsealed, cycle) {
  const brokenHighs = unsealed.filter(u => u.pid >= 4)
    .sort((x, y) => y.pid - x.pid || y.pct - x.pct)
    .map(u => ({
      code: u.code, name: u.name, level: u.pid >= 5 ? '≥5' : u.pid, pct: u.pct,
      note: `${u.pid >= 5 ? '≥5' : u.pid}板开板 · 现${u.pct.toFixed(1)}%${u.mainNet < 0 ? ' · 主力净卖出' : ''}`,
    }))

  const maxLevel = Math.max(0, ...todayJoined.map(r => r.level))
  const mainBoards = (cycle.mainlines || []).slice(0, 2).map(m => m.board)
  const anchor = (cycle.leaders || [])[0]
  const watch = todayJoined.filter(r =>
    r.level === maxLevel - 1 && r.level >= 2 &&
    (r.plates || []).some(b => mainBoards.includes(b)))
    .map(r => ({ r, ratio: sealRatio(r), t: hhmm(r.ztTime) }))
    .filter(x => x.ratio === null || x.ratio >= 0.8)
    .filter(x => !x.t || x.t <= '10:30')
    .filter(x => (x.r.mainNet || 0) > 0)
    .sort((x, y) => (x.r.ztTime || 0) - (y.r.ztTime || 0))
    .slice(0, 5)
    .map(({ r, ratio, t }) => ({
      code: r.code, name: r.name, level: r.level, board: (r.plates || [])[0] || '',
      note: `${t ? `早封${t}` : '一字'} · ${r.seal ? `封单${yi(r.seal)}(保持${pctTxt(ratio)})` : ''} · 主力净买${yi(r.mainNet)}`,
      tip: anchor ? `若 ${anchor.name}(${anchor.pid}板)断板，${r.name}为第一顺位` : '',
    }))
  return { brokenHighs, watch }
}

/**
 * 龙头博弈总计算
 * @param {object} p  { ladderRows, todayPool, prevFull, unsealed, cycle }
 *   ladderRows/todayPool/prevFull/unsealed 见 useKplApi.fetchTianTi/fetchLimitPool/fetchUnsealedPool
 */
export function computeBattle({ ladderRows = [], todayPool = [], prevFull = [], unsealed = [], cycle = {} }) {
  const todayJoined = joinToday(ladderRows, todayPool)
  if (!todayJoined.length || !cycle.stage) {
    return { empty: true, strike: null, boardWars: { wars: [], relations: [], mainSwitch: null }, duels: [], risks: { brokenHighs: [], watch: [] } }
  }
  const boardWars = computeBoardWars(todayJoined, prevFull)
  const duels = computeDuels(todayJoined, prevFull, boardWars)
  const strike = computeStrike(cycle, todayJoined, prevFull, unsealed, boardWars)
  const risks = computeRisks(todayJoined, unsealed, cycle)
  return { empty: false, strike, boardWars, duels, risks }
}

/**
 * 装配: 今日 RT 涨停池(5板位) + 未涨停池(1/2/4/5) → computeBattle
 * 昨日全字段池复用 loadCycleData 的缓存结果(cd.prevFull)
 */
export async function loadBattleData(kpl, cd) {
  const [pools, unsealedLists] = await Promise.all([
    Promise.all([1, 2, 3, 4, 5].map(p => kpl.fetchLimitPool('', p, { rt: true, silent: true }))),
    Promise.all([1, 2, 4, 5].map(p => kpl.fetchUnsealedPool(p, true))),
  ])
  return computeBattle({
    ladderRows: cd.ladderRows, todayPool: pools.flat(),
    prevFull: cd.prevFull, unsealed: unsealedLists.flat(), cycle: cd.cycle,
  })
}
