// 选股页(原盘面)派生逻辑 —— 全部纯函数, 输出即页面文案/判定, 单测见 __tests__/stockPicks.test.js
// 状态词口径全站唯一: 可买 / 待确认 / 只看不买
//   后端/Python 旧词(可做/出击/备选/矩阵谨慎/观察…) 只在本文件归一, 页面不得再自造词
import { STAGE_RULES } from './emotionCycle.js'

const RETREAT_STAGES = ['退潮', '冰点', '分歧']
const V_BAN = { verdict: '禁买', pool: '关闭', cls: 'ban' }
const V_FULL = { verdict: '可买', pool: '全开', cls: 'go' }

/** 结论头三层里的「池」判定: gateCap 0=关 / >=100=全开 / 其余限N分; 缺失返回占位 */
export function gateTier(gateCap) {
  if (gateCap === null || gateCap === undefined || gateCap === '') return { verdict: '—', pool: '—', cls: 'plain' }
  const cap = Number(gateCap)
  if (!Number.isFinite(cap)) return { verdict: '—', pool: '—', cls: 'plain' }
  if (cap === 0) return { ...V_BAN }
  if (cap >= 100) return { ...V_FULL }
  return { verdict: '谨慎可买', pool: `限${cap}分`, cls: 'warn' }
}

/** 结论头里的「纪律上限」: 按阶段, 单位「成」 */
export function capWord(stage) {
  return STAGE_RULES[stage]?.cap || '—'
}

/** 结论头副行: 阶段 + 一句话偏向 */
export function basisLine(stage, note = '') {
  const s = stage || '—'
  return note ? `${s} · ${note}` : s
}

/** 候选状态词归一(处理 Python 各旧词与括号注记; 矩阵谨慎=待确认 优先于可做前缀) */
export function statusWord(raw = '') {
  const s = String(raw || '')
  if (s.includes('矩阵谨慎')) return { txt: '待确认', cls: 'alt' }
  const t = s.replace(/[（(][^）)]*[)）]/g, '').trim()
  if (/^(出击|可做|可买)/.test(t)) return { txt: '可买', cls: 'go' }
  if (/^备选/.test(t)) return { txt: '待确认', cls: 'alt' }
  return { txt: '只看不买', cls: 'watch' }
}

/** 与持仓重叠标记(去重+防重复买入) */
export function holdInfo(candidate, positions = []) {
  const p = (positions || []).find(x => x && x.code === (candidate && candidate.code))
  if (!p) return null
  const weight = p.weight ? String(p.weight).trim() : ''
  return { held: true, txt: weight ? `已持仓 ${weight}` : '已持仓' }
}

/** 今日出击候选一次归一: 状态词 + 持仓标记; 列表同源展示, 页面不得另存副本 */
export function mergeCandidates(candidates = [], positions = []) {
  return (candidates || []).map(c => ({ ...c, word: statusWord(c.status), hold: holdInfo(c, positions) }))
}

/** 时段顺序: <10:00 早盘盘前候选优先, ≥10:00 盘中今日出击优先 */
export function isMorning(now = new Date()) {
  const h = now instanceof Date && !Number.isNaN(now.getTime()) ? now.getHours() : new Date().getHours()
  return h < 10
}

export function sectionOrder(morning) {
  return morning ? ['pre', 'strike'] : ['strike', 'pre']
}

/** 结论与达标候选联动: 池开但无可买/待确认 → 如实显示「仅观察」, 防止「可买但无票」误导 */
export function resolveVerdict(tier, actCount) {
  if (!tier || tier.verdict === '禁买' || (Number(actCount) || 0) > 0) return tier
  return { verdict: '仅观察', pool: tier.pool, cls: 'warn' }
}

/** 六情绪等日终指标的展示守卫: 其数据日必须与实时周期/结论日一致, 否则视为过期不展示 */
export function sameDataDay(reviewDate, liveDate) {
  return !!(reviewDate && liveDate && String(reviewDate) === String(liveDate))
}

/** 双引擎背离提示: 六情绪说"极强"(主攻龙头/核心), 但池未开或无达标候选 → 取严执行 + 人工豁免出路 */
const STRONG_SIX = ['板块情绪极强', '投机情绪极强']
export function divergenceNote(dominant, tier, actCount) {
  if (!STRONG_SIX.includes(dominant)) return ''
  if (!tier || tier.verdict === '可买' || (Number(actCount) || 0) > 0) return ''
  return '情绪(' + dominant + ')与梯队禁买背离 → 系统按更严执行(仅观察); 空间锚/总龙头可人工豁免(参考≤1成)'
}

/** 涨停幅度: 创业/科创 20%, 北交 30%, 其余 10%(与页面历史口径 limOf 一致) */
export function limitPctOf(code) {
  const s = String(code || '')
  if (/^(4|8|92)/.test(s)) return 30
  if (/^(688|689|300|301)/.test(s)) return 20
  return 10
}

const isNum = v => typeof v === 'number' && isFinite(v)
/** 由现价与涨跌幅反推昨收 */
export function prevCloseOf(price, pct) {
  if (!isNum(price) || !isNum(pct)) return null
  const d = 1 + pct / 100
  return d > 0 ? price / d : null
}
/** 现价距涨停的百分比(打板可执行度) */
export function distToLimit(price, pct, code) {
  const pc = prevCloseOf(price, pct)
  if (!pc || !isNum(price)) return null
  return Math.round((pc * (1 + limitPctOf(code) / 100) / price - 1) * 1000) / 10
}

/** 打板类模式: 买点=涨停价 */
export const BOARD_BUY_MODES = ['排板', '排板接力', '龙头接力', '打板']
/** 实时可执行行: 按模式给「距买点」—— 打板类对照涨停价, 低吸类对照当日分时均价(东财 f5/f6 推算), 其余只给现价+止损 */
export function pxLine({ mode = '', price, pct = 0, code = '', avg = null }) {
  if (!isNum(price)) return ''
  const pctTxt = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`
  const stop = (price * 0.97).toFixed(2)
  if (BOARD_BUY_MODES.some(m => String(mode || '').includes(m))) {
    const d = distToLimit(price, pct, code)
    return d == null ? '' : `⏱ 现价 ${price}(${pctTxt}) · 距买点(涨停) ${d}% · 止损参考 ${stop}`
  }
  if (isNum(avg) && avg > 0) {
    const d = Math.round((avg / price - 1) * 1000) / 10
    return `⏱ 现价 ${price}(${pctTxt}) · 距买点(分时均价) ${d}% · 止损参考 ${stop}`
  }
  return `⏱ 现价 ${price}(${pctTxt}) · 止损参考 ${stop}`
}

/** 列表排序: 最适合买入在前(可买>待确认>只看), 次级按竞价涨幅降序(强者优先) */
export function sortPicksFirst(rows) {
  const w = r => (r?.w?.txt === '可买' ? 0 : r?.w?.txt === '待确认' ? 1 : 2)
  return [...(rows || [])].sort((a, b) =>
    w(a) - w(b) || ((b?.bid_pct ?? -99) - (a?.bid_pct ?? -99)))
}

/** 复核排序: 执行优先级(持有>兑现>减半>开盘走>不出手), 不出手垫底 */
const REVIEW_ORDER = ['持有到尾盘', '冲高兑现', '减半(弱于预期)', '开盘走', '不出手']
export function sortReviewRows(rows) {
  const idx = r => { const i = REVIEW_ORDER.indexOf(r?.tag); return i === -1 ? 98 : i }
  return [...(rows || [])].sort((a, b) => idx(a) - idx(b))
}

export { RETREAT_STAGES }
