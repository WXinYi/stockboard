// 龙头博弈 + 今日出击 · 浏览器端纯规则引擎(不用大模型, 全部确定性规则, 可回测)
// ⚠️ 「今日出击」的阶段闸门/候选规则与 jiarenmens/src/analysis/stage_candidates.py 对齐, 改规则两边同步。
//    矩阵分层闸门 MATRIX_GATE 同样两边镜像(语义对齐 MarketDetail.vue 的 MATRIX_DESC 九宫格)。
// 板块之争/高标对决/半路候选/定位标签(龙头·中军·补涨·跟风)/买点三件套为 JS 展示层实现(暂无 Python 对应, Python 仅同步候选范围与状态语义); 若日后移植到推送, 需成对维护。
// 评分权重为经验值, 待 auction.db 历史回测校准(M3); 评分=胜率优先排序, 非收益保证。

import { tierOf } from './emotionCycle.js'

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

// 高中位矩阵分层闸门: 同一阶段下, 高位/中位/低位可出击的类型不同(key=高位|中位, 语义对齐 MATRIX_DESC)
// tier: low=1-2板 mid=3-5板 high=≥6板(与 emotionCycle.tierOf 同口径)
// 级别: go=可出击 care=最高备选(封顶70) watch=只观察 ban=禁买; 与阶段闸门取更严者
const MATRIX_GATE = {
  '强|强': { cap: 100, tier: { high: 'go', mid: 'go', low: 'go' }, note: '上升前期：高中低位全谱系可接力' },
  '强|平衡': { cap: 100, tier: { high: 'go', mid: 'care', low: 'go' }, note: '上升中后期：抱团龙头+低切低位，中位梯队转备选' },
  '强|弱': { cap: 70, tier: { high: 'care', mid: 'ban', low: 'go' }, note: '情绪末端：中位核按钮高发禁买，只做龙头(备选)+低位' },
  '平衡|强': { cap: 100, tier: { high: 'care', mid: 'go', low: 'go' }, note: '中低位活跃：主做中位梯队+低位，高位只跟龙头' },
  '平衡|平衡': { cap: 70, tier: { high: 'watch', mid: 'care', low: 'go' }, note: '混沌盘面：只做龙头与低位套利' },
  '平衡|弱': { cap: 45, tier: { high: 'ban', mid: 'ban', low: 'care' }, note: '情绪很差：高中位禁买，仅低位轻仓' },
  '弱|强': { cap: 70, tier: { high: 'watch', mid: 'go', low: 'go' }, note: '空间受压：中位补涨为主，高位不接力' },
  '弱|平衡': { cap: 45, tier: { high: 'ban', mid: 'care', low: 'care' }, note: '试探期：低位1进2/首板轻仓试错' },
  '弱|弱': { cap: 30, tier: { high: 'ban', mid: 'ban', low: 'watch' }, note: '全面退潮：寸草不生，全谱系观察' },
}
const TIER_NAME = { low: '低位', mid: '中位', high: '高位' }

// 买点三件套(买法/触发/止损)按 mode 定制; posTxt 由阶段闸门 cap 换算, 前端 candTip 直接透传
const TIP_BY_MODE = {
  '排板接力': { buy: '龙头接力', trigger: '竞价高开2-5%抢筹或回封排板；高开>7%只等回踩', stop: '断板即走 · 水下-2%' },
  '排板': { buy: '龙头接力', trigger: '封单回封排板，不追盘中冲高', stop: '断板即走' },
  '低吸不追高': { buy: '龙头低吸', trigger: '分时回踩均价线企稳再接，不追高开', stop: '跌破昨日低点' },
  '低吸补涨': { buy: '补涨快进', trigger: '龙头封死后板块内卡位首日，快进快出', stop: '当日炸板即走' },
  '首板试错': { buy: '首板试错', trigger: '竞价高开0-4%轻仓打，板块梯队≥3才有效', stop: '-3% 不过夜' },
  '1进2排板': { buy: '1进2确认', trigger: '竞价强(+1.5~7%)→打板确认，炸板即走', stop: '炸板/水下-3%' },
  '半路': { buy: '半路低吸', trigger: '主力净买为证，封板则持有至一致转分歧', stop: '-3% 不过夜' },
  '排板/半路': { buy: '梯队排板', trigger: '封单回封排板或半路强转一致，不追分时冲高', stop: '断板即走 · -3%不过夜' },
  '谨慎接力': { buy: '秒板接力', trigger: '只排板不追高，一致加速段兑现', stop: '断板即走' },
  '火种观察': { buy: '火种试错', trigger: '试错许可(A+B)亮灯后，竞价高开0-5%试错', stop: '-3% 无条件' },
  '观察(容量)': { buy: '中军低吸', trigger: '回踩5日线/分时均线低吸，不追涨', stop: '破位前低' },
}
function posFor(cap, status) {
  if (cap === 0) return '0 成 · 阶段禁买'
  if (status.startsWith('出击')) return cap >= 100 ? '单票≤2成' : cap >= 60 ? '单票≤1.5成' : cap >= 45 ? '单票≤1成' : '单票≤0.5成'
  if (status.startsWith('备选')) return cap >= 100 ? '半仓试 · 单票≤1成' : '半仓试 · 单票≤0.5成'
  return '0 成 · ' + status
}

// 买点三件套展示(盘面页出击Tab/详情页共用): 引擎 buyTip/posTxt 透传, 无引擎字段时按 mode/高度兜底
export function candTipOf(c, cap = 100) {
  if (c.buyTip) return { ...c.buyTip, pos: c.posTxt || (cap === 0 ? '0 成 · 阶段禁买' : '首仓 1 成') }
  const lv = c.level || 0
  const off = cap === 0 ? '0 成 · 阶段禁买' : '首仓 1 成'
  if ((c.mode || '').includes('半路')) {
    return { buy: '半路低吸', trigger: '主力净买为证，封板则持有至一致转分歧', stop: '-3% 不过夜', pos: off }
  }
  if (lv <= 1) {
    return { buy: '首板试错', trigger: '板块梯队≥3 才有效，次日溢价为正再加', stop: '-3% 不过夜', pos: off }
  }
  if (lv === 2) {
    return { buy: '1进2 确认', trigger: '分歧转一致打板，炸板即走', stop: '-3% 不过夜', pos: off }
  }
  return { buy: '龙头接力', trigger: '只排板不追高，一致转分歧兑现', stop: '断板即走', pos: cap === 0 ? off : '首仓 1 成（阶段上限内）' }
}

function computeStrike(cycle, todayJoined, prevFull, unsealed, boardWars, now = null, prevBroken = null) {
  const stage = cycle.stage
  const gate = STAGE_GATE[stage] || { cap: 100, banner: '' }
  // 矩阵分层闸门: 与阶段闸门取更严者(同类型票在高位弱/中位弱时降级或禁买)
  const mtx = cycle.matrix && MATRIX_GATE[`${cycle.matrix.high}|${cycle.matrix.mid}`]
  const cap = Math.min(gate.cap, mtx ? mtx.cap : 100)
  const banner = mtx ? `${gate.banner} 📐 高位${cycle.matrix.high}×中位${cycle.matrix.mid}：${mtx.note}` : gate.banner
  const mainBoards = (cycle.mainlines || []).slice(0, 2).map(m => m.board)
  const deltaOf = {}
  for (const w of boardWars.wars) deltaOf[w.board] = w.dCount
  const leaderCodes = new Set((cycle.leaders || []).map(l => l.code))
  const prevPid1 = new Set(prevFull.filter(r => r.pid === 1).map(r => r.code))
  const pool = new Map([...todayJoined.map(r => [r.code, r]), ...unsealed.map(u => [u.code, u])])
  const cands = []

  // 定位四分类(龙头/中军/补涨/跟风): 龙头谱系角色优先, 否则按 板块高度/市值/板块扩缩 推断
  const leadRole = new Map((cycle.leaders || []).map(l => [l.code, l.role || '']))
  const warOf = new Map(boardWars.wars.map(w => [w.board, w]))
  const prevMap = new Map(prevFull.map(r => [r.code, r]))
  const primaryBoard = row => (row.plates || []).find(b => warOf.has(b)) || (row.plates || [])[0] || ''
  const classify = row => {
    const role = leadRole.get(row.code)
    if (role) {
      if (role.includes('空间锚') || role.includes('总龙头')) return '总龙头'
      if (role.includes('板块龙头')) return '龙头'
      if (role.includes('中军')) return '中军'
      if (role.includes('补涨')) return '补涨'
    }
    const lvl = row.level ?? row.pid ?? 0
    const w = warOf.get(primaryBoard(row))
    const maxH = w ? w.maxH : 0, dC = w ? w.dCount : null
    if (lvl >= 3) return maxH && lvl >= maxH ? '龙头' : '梯队'
    if ((row.circMv || 0) >= 150e8) return '中军'
    if (dC !== null && dC <= -2) return '跟风'
    if (maxH >= 3 && dC !== null && dC >= 1) return '补涨'
    return lvl === 1 ? '首板' : '梯队'
  }

  const add = (row, base, mode, logic) => {
    const c = cands.find(x => x.code === row.code)
    if (c) { if (!c.logic.includes(logic)) c.logic += `；${logic}`; return }
    cands.push({ code: row.code, name: row.name, level: row.level ?? row.pid ?? 0, base, mode, logic })
  }

  // 1) 龙头谱系: 状态由 阶段×角色 决定(与 stage_candidates.py 对齐); 启动期龙头分歧转一致可上
  const leadMode = { 高潮: '排板接力', 发酵: '排板', 分歧: '低吸不追高', 启动: '低吸不追高' }
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
      else if (stage === '冰点' && r.level >= 2) add(r, 30, '火种观察', `逆市连板(冰点火种) ${r.level}板`)
    }
    if (stage === '启动') {
      // 首板试错: 主线首板 + 早封(≤10:00, 一字视为最强) + 主力净买 —— 新龙头苗子
      for (const r of todayJoined) {
        if (r.level !== 1 || !(r.plates || []).some(b => mainBoards.includes(b))) continue
        const t = hhmm(r.ztTime)
        if ((t && t > '10:00') || (r.mainNet || 0) <= 0) continue
        add(r, 55, '首板试错', `主线首板·早封${t || '一字'} 主力净买${yi(r.mainNet)}(新龙头苗子)`)
      }
    }
  }
  // 2b) 退潮火种: 禁买期也要"看什么"——今日逆市连板即新周期候选载体
  if (stage === '退潮') {
    for (const r of [...todayJoined].filter(x => x.level >= 2).sort((x, y) => y.level - x.level).slice(0, 4))
      add(r, 35, '火种观察', `逆市连板=新周期火种候选(禁买期只看) ${r.level}板`)
  }
  // 2c) 反核撬板(陈小群): 退潮/分歧/冰点期 总龙头深水(≤-5%)——跌停放量被撬+大资金牵头才小仓试错, 严禁后排反核
  if (['退潮', '分歧', '冰点'].includes(stage)) {
    const anchor = (cycle.leaders || [])[0]
    const deep = anchor && unsealed.find(u => u.code === anchor.code && u.pct <= -5)
    if (deep) add({ ...deep, level: deep.pid }, 30, '反核观察',
      `总龙头${anchor.name}现${deep.pct.toFixed(1)}%深水——反核只评估核心: 跌停放量+大资金牵头+合力承接才试错`)
  }
  // 2d) 尾盘修复(92科比): 14:30后 杀跌日核心获承接(拉回>-2%且主力净买)——小仓先手, 次日不修复快速处理
  const bj = new Date((now ?? Date.now()) + 8 * 3600e3) // 东八区钟点(测试注入原始时间戳, 统一+8h)
  const hm = bj.toISOString().slice(11, 16)
  if (['分歧', '退潮', '冰点'].includes(stage) && hm >= '14:30' && hm <= '15:01') {
    const anchor = (cycle.leaders || [])[0]
    const fix = anchor && unsealed.find(u => u.code === anchor.code && u.pct > -2 && (u.mainNet || 0) > 0)
    if (fix) {
      const existed = cands.find(c => c.code === fix.code)
      if (existed) { if (!existed.logic.includes('尾盘承接')) existed.logic += '；尾盘承接确认(先手小仓, 次日不修复快速处理)' }
      else add({ ...fix, level: fix.pid }, 35, '尾盘修复',
        `尾盘核心承接(现${fix.pct.toFixed(1)}% 主力净买${yi(fix.mainNet)})——先手小仓, 次日不修复快速处理`)
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
    // 换手检验(著名刺客: 换手连板优于缩量板——换手才证明真实承接, 最强的板让次日接力者赚钱)
    const to = row.turnover || 0
    if ((row.level || 0) >= 2 && (row.seal > 0 || (row.amount || 0) > 0)) {
      if (to > 0 && to < 3) { score -= 12; if (!c.risk) c.risk = '缩量板·换手未检验(次日接力存疑)' }
      else if (to >= 5) { score += 4; strengths.push(`换手${to.toFixed(1)}%`) }
    }
    // 弱转强(陈小群): 昨日烂板(封单保持<0.15)或昨日炸板 今日回封 = 分歧转一致
    const pr = prevMap.get(c.code)
    const rottenPrev = pr && (pr.seal || 0) > 0 && (pr.maxSeal || 0) > 0 && pr.seal / pr.maxSeal < 0.15
    if ((row.level || 0) >= 2 && (rottenPrev || (prevBroken && prevBroken.has(c.code)))) {
      score += 6; strengths.push(rottenPrev ? '弱转强·烂板回封' : '弱转强·炸板回封')
    }
    c.score = Math.max(0, Math.min(cap, score))
    const tierAct = mtx ? (mtx.tier[tierOf(c.level)] || 'go') : 'go'
    if (tierAct === 'care') c.score = Math.min(c.score, 70)
    const buyable = c.mode !== '观察' && !c.mode.startsWith('观察')
    c.status = cap === 0 || !buyable ? '观察' + (cap === 0 ? '(阶段禁买)' : '') : c.score >= 75 ? '出击' : c.score >= 55 ? '备选' : '观察'
    if (buyable && tierAct === 'watch') c.status = '观察(矩阵)'
    if (buyable && tierAct === 'ban') {
      c.status = '观察(矩阵禁买)'
      c.risk = c.risk ? `${c.risk}；矩阵${TIER_NAME[tierOf(c.level)]}禁买` : `矩阵${TIER_NAME[tierOf(c.level)]}禁买`
    }
    // 定位标签 + 跟风回避 + 买点三件套/建议仓位(状态语义与 stage_candidates.py 对齐)
    c.roleTxt = classify(row)
    if (c.roleTxt === '跟风') {
      c.score = Math.min(c.score, 30)
      if (c.status === '出击' || c.status === '备选' || c.status === '观察') c.status = '观察(跟风回避)'
      c.risk = c.risk ? `跟风·回避：${c.risk}` : '跟风·回避：板块退潮末端杂毛，一致时最先掉队'
    }
    c.posTxt = posFor(cap, c.status)
    c.buyTip = TIP_BY_MODE[c.mode] || (c.roleTxt === '中军' ? TIP_BY_MODE['观察(容量)'] : null)
    c.strength = strengths.join(' · ')
    c.sealTxt = row.seal ? `封单${yi(row.seal)}` : ''
    c.platesTxt = (row.plates || []).slice(0, 2).join('/')
  }
  const anchor = (cycle.leaders || [])[0]
  // 接力盈利仪表(著名刺客: 最强的板是让次日接力者赚钱的板)——昨日涨停股今日可得涨幅均值
  const limPctOf = c => /^(4|8|92)/.test(c) ? 30 : /^(688|689|300|301)/.test(c) ? 20 : 10
  const todayPct = new Map()
  for (const r of unsealed) todayPct.set(r.code, r.pct)
  for (const r of todayJoined) if (!todayPct.has(r.code)) todayPct.set(r.code, limPctOf(r.code))
  const known = prevFull.map(r => todayPct.get(r.code)).filter(v => v !== undefined)
  const relayAvg = known.length ? known.reduce((a, b) => a + b, 0) / known.length : null
  const relayTxt = relayAvg === null ? '' : `接力盈利: 昨日涨停 ${known.length} 只今日均值 ${relayAvg >= 0 ? '+' : ''}${relayAvg.toFixed(1)}% —— ${relayAvg >= 2 ? '接力者赚钱·生态健康' : relayAvg >= 0 ? '接力微利·只做核心' : '接力亏损·防守(只看不追)'}`
  return {
    gate: { stage, cap, banner, playbook: cycle.playbook,
      matrix: mtx && cycle.matrix ? { ...cycle.matrix, note: mtx.note } : null },
    anchor: anchor ? `${anchor.name}(${anchor.pid}板)` : '',
    relay: { n: known.length, avg: relayAvg, txt: relayTxt },
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
export function computeBattle({ ladderRows = [], todayPool = [], prevFull = [], unsealed = [], cycle = {}, lianbanBid = null, prevBroken = null, now = null }) {
  const todayJoined = joinToday(ladderRows, todayPool)
  if (!todayJoined.length || !cycle.stage) {
    return { empty: true, strike: null, boardWars: { wars: [], relations: [], mainSwitch: null }, duels: [], risks: { brokenHighs: [], watch: [] } }
  }
  const boardWars = computeBoardWars(todayJoined, prevFull)
  const duels = computeDuels(todayJoined, prevFull, boardWars)
  const strike = computeStrike(cycle, todayJoined, prevFull, unsealed, boardWars, now,
    Array.isArray(prevBroken) ? new Set(prevBroken) : null)
  // 昨日连板 × 竞价实际换手 Top5 特殊标记(数据: latest/lianban_bid.json, 口径=scripts/lianban_bid_hs.py; 日期不匹配视为过期不标)
  if (lianbanBid && lianbanBid.date === cycle.date && Array.isArray(lianbanBid.top)) {
    const rank = new Map(lianbanBid.top.map((t, i) => [t.code, { rank: i + 1, hs: t.hs, prevPid: t.prev_pid }]))
    for (const c of strike.candidates) {
      const r = rank.get(c.code)
      if (r) c.bidTop = r
    }
  }
  const risks = computeRisks(todayJoined, unsealed, cycle)
  return { empty: false, strike, boardWars, duels, risks }
}

// 昨日可买复核判定(战法: 低于预期即卖·卖在一致·周期转防守只卖不买)。pct=今日实时涨幅
export function reviewVerdict(pct, code, stage) {
  if (['退潮', '冰点'].includes(stage)) return { cls: 'sell', tag: '清仓', txt: '周期转防守：只卖不买' }
  if (pct === null || pct === undefined) return { cls: 'warn', tag: '看竞价', txt: '无实时行情，人工确认' }
  const lim = /^(4|8|92)/.test(code) ? 30 : /^(688|689|300|301)/.test(code) ? 20 : 10
  if (pct >= lim - 0.5) return { cls: 'hold', tag: '持有', txt: '一致加速：尾盘一致转分歧再兑现' }
  if (pct >= 3) return { cls: 'hold', tag: '持有·冲高兑现', txt: '卖在一致：冲高破分时均价即走' }
  if (pct >= 0) return { cls: 'warn', tag: '减半', txt: '弱于预期：接力不赚钱原则，先出一半' }
  return { cls: 'sell', tag: '开盘走', txt: '低于预期即卖，不等回本' }
}

/**
 * 装配: 今日 RT 涨停池(5板位) + 未涨停池(1/2/4/5) → computeBattle
 * 昨日全字段池复用 loadCycleData 的缓存结果(cd.prevFull); lianbanBid 为竞价换手标记数据(fetchLianbanBid), 可空
 */
export async function loadBattleData(kpl, cd, lianbanBid = null, prevBroken = null) {
  const [pools, unsealedLists] = await Promise.all([
    Promise.all([1, 2, 3, 4, 5].map(p => kpl.fetchLimitPool('', p, { rt: true, silent: true }))),
    Promise.all([1, 2, 4, 5].map(p => kpl.fetchUnsealedPool(p, true))),
  ])
  return computeBattle({
    ladderRows: cd.ladderRows, todayPool: pools.flat(),
    prevFull: cd.prevFull, unsealed: unsealedLists.flat(), cycle: cd.cycle, lianbanBid, prevBroken,
  })
}
