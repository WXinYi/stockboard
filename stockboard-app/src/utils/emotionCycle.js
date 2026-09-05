// 超短情绪周期引擎 · 浏览器端 JS 移植
// ⚠️ 双实现同步警示：本文件与 jiarenmens/src/analysis/emotion_cycle.py 成对维护
//   （先例：isQuality 有 export_json.py 与 useData.js 两份）。改阈值/规则必须两处同步。
//   JS 版仅作页面展示；钉钉推送与回测以 Python 版为准。
// 与 Python 的差异（展示口径，均有意简化）：
//   - 当日梯队直接用天梯接口的 level（KPL 已算好连板数），无需逐日连续反推
//   - 昨日池高位受 PidType=5 封顶桶影响，高位晋级率按"昨≥5板 → 今≥6板"近似
//   - 中军用流通市值（天梯 cap）而非成交额

export const CYCLE_CFG = {
  iceHeight: 3,
  iceZtRatio: 0.70,
  iceBroke: 30.0,
  gaochaoHeight: 6,
  gaochaoZtRatio: 1.40,
  tuiHeightDrop: 2,
  tuiZtRatio: 0.80,
  fajiaoHeight: 5,
  promoteStrong: 0.5,
  promoteWeak: 0.25,
  mainlineMin: 3,
}

export const STAGES = ['冰点', '启动', '发酵', '高潮', '分歧', '退潮']

export const STAGE_PLAYBOOK = {
  冰点: '空仓应对为主；留意逆市连板(新周期火种)；首板套利只在最强板块',
  启动: '打低位首板/1进2 为主；情绪低点做龙头，穿越反包空间龙',
  发酵: '五板封住定龙头：上龙头/同梯队强者，次日高开抢；板块弱转强打板龙头、低吸中后排',
  高潮: '接力只做龙头(秒板/放量分歧板)；板块爆炸买跟风但去弱留强；高峰跟风不碰',
  分歧: '抱团龙头与妖股、低吸龙头；尽量避开中位股(核按钮高发)',
  退潮: '空仓纪律优先；只观察最高标(尾盘炸板最强还可捡)；从此高位不接力',
}

// 各阶段仓位上限与核心动作(个人纪律立法版, 配合 STAGE_PLAYBOOK):
// 退潮/分歧断崖/冰点未确认 → ≤2成只卖不买; 高潮 → 兑现为主新仓≤2成; 启动/发酵 → 6-8成做主线
// 各阶段仓位上限与核心动作(个人纪律立法版 × 养家心法口径, 配合 STAGE_PLAYBOOK):
// 养家核心:"基于对市场情绪的揣摩, 判断风险收益比的比较, 指导实际操作"
// 口诀: 买在分歧, 卖在一致 | 高手买入龙头, 超级高手卖出龙头 | 心中无顶底 | 势在股在
export const STAGE_RULES = {
  退潮: {
    cap: '≤2成', hot: '只卖不买',
    act: '势走股走：宁可错过，不可做错。执行清仓价位单，禁止一切新开仓',
    yj: '别人恐慌时我更恐慌——退潮期的每一次反弹都是出货窗口',
  },
  冰点: {
    cap: '≤2成', hot: '只卖不买',
    act: '等新周期火种：逆市连板股出现时，方可用 1 成仓试错首板',
    yj: '行情不好的时候少做——心中无顶底，等的是势，不是价格',
  },
  分歧: {
    cap: '≤2成', hot: '只卖不买',
    act: '断崖分歧只卖不买；高位分歧可抱团龙头，仓位仍受上限约束',
    yj: '分歧是龙头的试金石：分歧转一致可上车，一致转分歧要兑现',
  },
  高潮: {
    cap: '新仓≤2成', hot: '兑现为主',
    act: '卖在一致：全民看多时兑现，杂毛的涨停是出货的伪装',
    yj: '高手买入龙头，超级高手卖出龙头',
  },
  发酵: {
    cap: '6-8成', hot: '做主线',
    act: '强者恒强：上龙头/同梯队强者，弱转强打板龙头、低吸中后排',
    yj: '别人贪婪时我更贪婪——主线内的贪婪才是顺势',
  },
  启动: {
    cap: '6-8成', hot: '做主线',
    act: '买在分歧：新题材首板/1进2，分歧转一致时上龙头',
    yj: '龙头是特定时期市场情绪的产物——新周期只认新面孔',
  },
}

export const STAGE_COLORS = {
  冰点: '#5b8def', 启动: '#4cd964', 发酵: '#2bc4a8',
  高潮: '#ff5a5a', 分歧: '#f5a623', 退潮: '#9b6bde',
}

const mean = arr => {
  const xs = arr.filter(x => x !== null && x !== undefined)
  return xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null
}

// 今日梯队行(level=连板数) / 昨日池行(pid, 封顶5) → 三层划分
// ⚠️ 单一分层口径: leaderBattle.js 矩阵分层闸门与 jiarenmens stage_candidates.py 均按此对齐
export const tierOf = h => (h <= 2 ? 'low' : h <= 5 ? 'mid' : 'high')

function promotionRate(prevRows, curRows, lo, hi) {
  // 昨日 height∈[lo,hi] 的票 → 今日真实高度 = 昨+1 的比例; 昨日 pid=5(封顶) 视作今日需 ≥6
  const prev = prevRows.filter(r => {
    const h = r.pid >= 5 ? 5 : r.pid
    return h >= lo && h <= hi
  })
  if (!prev.length) return null
  const curLv = {}
  for (const r of curRows) curLv[r.code] = r.level
  let hit = 0
  for (const r of prev) {
    const ph = r.pid >= 5 ? 5 : r.pid
    const need = ph + 1
    if ((curLv[r.code] || 0) >= need) hit++
  }
  return hit / prev.length
}

export function boardMainlines(curRows) {
  const agg = {}
  for (const r of curRows) {
    for (const b of r.plates || []) {
      if (!b) continue
      const a = agg[b] || (agg[b] = { board: b, count: 0, maxLevel: 0, cap: 0, names: [] })
      a.count++
      a.maxLevel = Math.max(a.maxLevel, r.level)
      a.cap = Math.max(a.cap, r.cap || 0)
      if (a.names.length < 8) a.names.push(`${r.name}(${r.level}板)`)
    }
  }
  return Object.values(agg)
    .filter(a => a.count >= CYCLE_CFG.mainlineMin)
    .sort((a, b) => b.count - a.count || b.maxLevel - a.maxLevel)
    .slice(0, 6)
}

export function classifyLeaders(curRows, mainlines) {
  if (!curRows.length) return []
  const out = []
  const used = new Set()
  const top = curRows.reduce((a, b) => (b.level > a.level ? b : a))
  used.add(top.code)
  out.push({
    code: top.code, name: top.name, pid: top.level, role: '空间锚(最高连板)',
    note: `${top.level}连板 · ${top.bkName || '无板块归属'}${top.seal ? ` · 封单 ${(top.seal / 1e8).toFixed(2)}亿` : ''}` +
      (top.seal && top.level >= 5 && top.seal < 5e7 ? ' ⚠️封单衰减看分歧' : ''),
  })
  mainlines.slice(0, 2).forEach((m, i) => {
    const members = curRows.filter(r => m.board && (r.plates || []).includes(m.board) && !used.has(r.code))
    if (!members.length) return
    const lead = members.reduce((a, b) => (b.level > a.level ? b : a))
    if (m.board && (top.plates || []).includes(m.board)) return // 主线由空间锚带队, 首行已覆盖
    out.push({
      code: lead.code, name: lead.name, pid: lead.level,
      role: i === 0 ? '总龙头' : `板块龙头(${m.board})`,
      note: `主线[${m.board}] ${m.count}只涨停`,
    })
    used.add(lead.code)
    let rest = members.filter(r => r.code !== lead.code)
    if (rest.length) {
      const mid = rest.reduce((a, b) => ((b.cap || 0) > (a.cap || 0) ? b : a))
      if ((mid.cap || 0) > 5e8) {
        out.push({ code: mid.code, name: mid.name, pid: mid.level, role: `中军(${m.board})`, note: `流通市值 ${(mid.cap / 1e8).toFixed(0)}亿 容量核心` })
        used.add(mid.code)
      }
      rest = rest.filter(r => !used.has(r.code))
      const bu = rest.filter(r => r.level >= 2 && r.level < lead.level)
        .reduce((a, b) => (!a || b.level > a.level ? b : a), null)
      if (bu) {
        out.push({ code: bu.code, name: bu.name, pid: bu.level, role: `补涨/卡位(${m.board})`, note: '龙头被关时的板块内补涨位' })
        used.add(bu.code)
      }
    }
  })
  return out
}

/**
 * 周期判定(与 emotion_cycle.py compute_cycle 同规则)
 * @param {object} p
 *   ladderRows  今日天梯行 [{code,name,level,bkName,cap,seal?}]
 *   prevPool    昨日涨停池行 [{code,pid}]   (His 接口, pid 封顶 5)
 *   riseFall    {today:{zt,brokeRate}, series:[{day,zt,brokeRate}...] 新在前}
 *   moodSeries  [{day,strong,lbgd}...] 新在前 (ChangeStatistics, 情绪值趋势展示)
 * @returns 与 Python 版同构的结果对象
 */
export function computeCycle({ ladderRows = [], prevPool = [], riseFall = null, moodSeries = [], dateStr = '' }) {
  const cfg = CYCLE_CFG
  const curRows = ladderRows.map(r => ({ ...r, height: r.level }))
  const height = curRows.length ? Math.max(...curRows.map(r => r.level)) : 0
  const zt = riseFall?.today?.zt ?? null
  const broke = riseFall?.today?.brokeRate ?? null
  const todayDay = riseFall?.today?.day || dateStr || ''
  const prevSeries = (riseFall?.series || []).filter(r => !todayDay || r.day < todayDay)
  const ztMa5 = mean(prevSeries.slice(0, 5).map(r => r.zt))
  const brokeMa5 = mean(prevSeries.slice(0, 5).map(r => r.brokeRate))
  const zr = zt && ztMa5 ? zt / ztMa5 : null
  const zrTxt = zr !== null ? `${Math.round(zr * 100)}% of ma5` : '无ma5'

  // 高度趋势: 用 mood 的 lbgd 序列(近似展示), 昨日高度取其最新一条
  const moodPrev = moodSeries.filter(r => !todayDay || r.day < todayDay)
  const heightPrev = moodPrev.length ? moodPrev[0].lbgd : null
  const hTrend = moodSeries.slice(0, 4).map(r => r.lbgd).reverse()

  const ladC = { low: 0, mid: 0, high: 0 }
  for (const r of curRows) ladC[tierOf(r.height)]++
  const ladP = { low: 0, mid: 0, high: 0 }
  for (const r of prevPool) ladP[tierOf(r.pid >= 5 ? 5 : r.pid)]++

  const promo = {
    low: promotionRate(prevPool, curRows, 1, 2),
    mid: promotionRate(prevPool, curRows, 3, 5),
    high: promotionRate(prevPool, curRows, 6, 99),
  }
  const tierState = (rate, now, prev) => {
    if (rate !== null) return rate >= cfg.promoteStrong ? '强' : rate < cfg.promoteWeak ? '弱' : '平衡'
    return now > prev ? '强' : now === prev ? '平衡' : '弱'
  }
  const matrix = {
    high: tierState(promo.high, ladC.high, ladP.high),
    mid: tierState(promo.mid, ladC.mid, ladP.mid),
  }

  const reasons = []
  let stage = '分歧'
  const hdrop = heightPrev !== null && height ? heightPrev - height : null
  // 1) 退潮
  if ((hdrop !== null && hdrop >= cfg.tuiHeightDrop) ||
      (zr !== null && zr < cfg.tuiZtRatio && broke > brokeMa5)) {
    stage = '退潮'
    reasons.push(`高度 ${heightPrev ?? '?'}→${height}${hdrop !== null ? `(降${hdrop}级)` : ''}，涨停 ${zt} 只(${zrTxt})，破板率 ${broke}%`)
  } else if (height <= cfg.iceHeight && ((zr !== null && zr < cfg.iceZtRatio) || broke > cfg.iceBroke)) {
    // 2) 冰点
    stage = '冰点'
    reasons.push(`高度仅 ${height}B(≤${cfg.iceHeight}B 直接确认)，涨停 ${zt} 只，破板率 ${broke}%`)
  } else if (height >= cfg.gaochaoHeight || (zr !== null && zr >= cfg.gaochaoZtRatio)) {
    // 3) 高潮
    stage = '高潮'
    reasons.push(`高度 ${height}B(近4日 ${hTrend.join('→')})，涨停 ${zt} 只(${zrTxt})，情绪峰值区`)
  } else if (heightPrev !== null && height >= heightPrev && height >= cfg.fajiaoHeight && (promo.mid || 0) >= 0.4) {
    // 4) 发酵: 五板封住定龙头(允许高度持平) + 中位晋级健康
    const midTxt = promo.mid !== null ? `${Math.round(promo.mid * 100)}%` : '无数据'
    stage = '发酵'
    reasons.push(`高度 ${heightPrev}→${height}B(五板封住定龙头)，中位晋级率 ${midTxt}`)
  } else if (heightPrev !== null && height > heightPrev) {
    // 5) 启动
    stage = '启动'
    reasons.push(`高度回升 ${heightPrev}→${height}B，涨停 ${zt} 只，梯队重建初期`)
  } else {
    // 6) 分歧
    const midTxt = promo.mid !== null ? `${Math.round(promo.mid * 100)}%` : '无数据'
    stage = '分歧'
    reasons.push(`高度 ${height}B 持平/中断，中位晋级率 ${midTxt}，破板率 ${broke}%`)
  }

  const mainlines = boardMainlines(curRows)
  const leaders = classifyLeaders(curRows, mainlines)
  const confidence = Math.min(9, 4 + reasons.length + (promo.mid !== null ? 2 : 0))

  return {
    date: todayDay, stage, confidence, reasons, playbook: STAGE_PLAYBOOK[stage],
    metrics: {
      height, heightPrev, zt, ztMa5, brokeRate: broke, brokeMa5,
      promo, ladder: ladC, hTrend,
    },
    matrix, mainlines, leaders,
  }
}


/**
 * 页面数据装配: 一次拉齐 天梯+昨日池(5板位)+涨跌分析+情绪序列 → computeCycle
 * 返回 {cycle, ladderRows, prevFull}: 后两者供 leaderBattle.js 复用(博弈/出击分析)
 * 昨日全字段池按 prevDay 缓存(盘中不变), 30s 轮询只刷 RT 部分
 */
let _prevPoolCache = { key: '', rows: [] }

export async function loadCycleData(kpl, dateStr) {
  const pids = [1, 2, 3, 4, 5]
  // capRows 放开到 500: 周期引擎需要完整梯队(默认 30 行截断仅供盘面预览)
  const [tianTi, riseFall, mood] = await Promise.all([
    kpl.fetchTianTi(true, 500),
    kpl.fetchRiseFall(true),
    kpl.fetchMarketMood(true),
  ])
  const todayDay = riseFall?.today?.day || dateStr || ''
  const ladderRows = (tianTi || []).flatMap(g => g.rows.map(r => ({
    code: r.code, name: r.name, level: r.level, bkName: r.bkName, cap: +r.cap || 0, seal: 0,
    plates: r.bkName ? [r.bkName] : [],
  })))
  // 昨日池: 从历史序列取今日前一交易日, 拉 5 个板位(按日缓存)
  const prevSeries = (riseFall?.series || []).filter(r => !todayDay || r.day < todayDay)
  const prevDay = prevSeries[0]?.day || ''
  if (!prevDay) return { cycle: computeCycle({ ladderRows, prevPool: [], riseFall, moodSeries: mood || [], dateStr: todayDay }), ladderRows, prevFull: [] }
  if (_prevPoolCache.key !== prevDay) {
    const prevLists = await Promise.all(pids.map(p => kpl.fetchLimitPool(prevDay, p, { rt: false, silent: true })))
    _prevPoolCache = { key: prevDay, rows: prevLists.flat() }
  }
  const prevFull = _prevPoolCache.rows
  const prevPool = prevFull.map(r => ({ code: r.code, pid: r.pid }))
  return { cycle: computeCycle({ ladderRows, prevPool, riseFall, moodSeries: mood || [], dateStr: todayDay }), ladderRows, prevFull }
}
