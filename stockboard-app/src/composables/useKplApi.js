// 开盘啦(KPL) 前端数据封装 —— 详情页行情/板块 + 盘面页全市场 + 详情页资讯/F10
// 接口来源: github.com/zensu357/KPL-post 接口清单; 字段映射全部经 curl 实测(2026-08-09)
import { fetchAuction } from '../data/loader.js'

// ---- 公共常量(与 jiarenmens/src/config.py 同款, 仓库公开, 安全) ----
export const KPL_TOKEN = '036ca9cad6e44ee4a585c22cb2c298ed'
export const KPL_USERID = '3807176'
const KPL_DEVICE = '6CC28E90-0785-4B21-8EEF-557159D26CF1'   // 固定设备号
// 浏览器 UA 会被 KPL 风控(返回空 List) → dev 走 vite 代理(覆盖 UA); 生产经腾讯云函数中转(okhttp UA, 同配方)
const DEV_PROXY = import.meta.env.DEV
const PROD_PROXY = 'https://1258166434-kgwxvgeu2h.ap-guangzhou.tencentscf.com'   // 腾讯云 SCF 函数 URL (scf/index.js, 国内直连)
const HOST_HQ = DEV_PROXY ? '/kpl-hq' : PROD_PROXY + '/kpl-hq'    // 实时行情/盘面
const HOST_HIS = DEV_PROXY ? '/kpl-his' : PROD_PROXY + '/kpl-his' // 历史/复盘/股东
const HOST_ART = DEV_PROXY ? '/kpl-art' : PROD_PROXY + '/kpl-art' // 内容/F10
const HOST_SECTION = DEV_PROXY ? '/kpl-sec' : PROD_PROXY + '/kpl-sec' // 板块归属
const HOST_LHB = DEV_PROXY ? '/kpl-lhb' : PROD_PROXY + '/kpl-lhb'     // 龙虎榜
const COMMON = { PhoneOSNew: 1, VerSion: '6.2.20.2', Red: 0, apiv: 'w47' }

function withTimeout(promise, ms = 8000) {
  return Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error('请求超时')), ms))])
}

// POST form-urlencoded; silent=true 失败返回 null 不抛(轮询静默模式)
async function postForm(url, params, silent) {
  try {
    const resp = await withTimeout(fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
      body: new URLSearchParams(params).toString(),
    }))
    return await resp.json()
  } catch (e) {
    if (!silent) console.error('[KPL]', params.a || url, e?.message)
    if (silent) return null
    throw e
  }
}

async function getJson(url, silent) {
  try {
    const resp = await withTimeout(fetch(url))
    return await resp.json()
  } catch (e) {
    if (silent) return null
    throw e
  }
}

// ---- 交易时段判定: 周一至五 09:30-11:30 / 13:00-15:00 (本地时间) ----
export function isTradingTime() {
  const now = new Date()
  const d = now.getDay()
  if (d === 0 || d === 6) return false
  const t = now.getHours() * 60 + now.getMinutes()
  return (t >= 570 && t <= 690) || (t >= 780 && t <= 900)
}

// 最近交易日: core.json 的 date(采集日, 周末/节假日采集会落在非交易日)
// → 非工作日则回退本地最近工作日(GetPlateInfo_w38/GetBKJJBL 对非交易日返回空)
export async function getLatestTradingDay() {
  try {
    const BASE = import.meta.env.BASE_URL
    const j = await withTimeout(fetch(BASE + 'data/latest/core.json').then(r => r.json()))
    if (j && j.date) {
      const s = String(j.date).replace(/-/g, '')
      const d = new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8))
      if (!Number.isNaN(d.getTime())) {
        // 采集日可能落在周末/节假日(爬虫周末也会跑) → 向前回退到最近交易日(GetBKJJBL 需已入库交易日)
        for (let i = 0; i < 7; i++) {
          const t = new Date(d.getTime() - i * 86400000)
          if (t.getDay() !== 0 && t.getDay() !== 6) {
            return t.getFullYear() + String(t.getMonth() + 1).padStart(2, '0') + String(t.getDate()).padStart(2, '0')
          }
        }
      }
    }
  } catch (e) { /* fallthrough */ }
  const d = new Date()
  for (let i = 0; i < 10; i++) {
    const t = new Date(d.getTime() - i * 86400000)
    const w = t.getDay()
    if (w !== 0 && w !== 6) {
      return t.getFullYear() + String(t.getMonth() + 1).padStart(2, '0') + String(t.getDate()).padStart(2, '0')
    }
  }
  return ''
}

// 最近已过的财报报告期 (3-31/6-30/9-30/12-31) — 机构增仓接口用
export function getLatestReportDate() {
  const now = new Date()
  const y = now.getFullYear()
  const m = now.getMonth() + 1
  const candidates = [
    { m: 12, d: 31 }, { m: 9, d: 30 }, { m: 6, d: 30 }, { m: 3, d: 31 },
  ]
  for (const c of candidates) {
    if (m > c.m || (m === c.m && now.getDate() >= c.d)) {
      return `${y}-${String(c.m).padStart(2, '0')}-${c.d}`
    }
  }
  return `${y - 1}-12-31`
}

// HTML → 纯文本(正文接口返回富文本)
// HTML → 纯文本(正文接口返回富文本; 公告为完整 HTML 页, 直接 textContent 会泄漏 <style> CSS 源码)
export function stripHtml(html) {
  if (!html) return ''
  const div = document.createElement('div')
  div.innerHTML = html
  div.querySelectorAll('style, script, head, noscript, iframe, link, meta').forEach(n => n.remove())
  div.querySelectorAll('br').forEach(b => b.replaceWith(document.createTextNode('\n')))
  div.querySelectorAll('p, div, tr, li, h1, h2, h3, h4, h5, h6, table').forEach(el => el.append('\n'))
  return (div.textContent || '').replace(/\u00a0/g, ' ').replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim()
}

// ============ 行情/板块组 (详情页) ============

// 涨跌停价推算(东财/腾讯降级源无 upPx/downPx 时兜底; 按板规则: 北交所30% / 科创创业20% / ST 5% / 主板10%)
export function calcLimitPx(prevClose, code, name) {
  if (!(typeof prevClose === 'number' && prevClose > 0)) return { up: null, down: null }
  let pct = 0.10
  if (/^(4|8|92)/.test(code)) pct = 0.30
  else if (/^(688|689|300|301)/.test(code)) pct = 0.20
  else if (typeof name === 'string' && /ST/.test(name)) pct = 0.05
  return {
    up: Math.round(prevClose * (1 + pct) * 100) / 100,
    down: Math.round(prevClose * (1 - pct) * 100) / 100,
  }
}

// 行情主源 #58 GetStockPanKou_Narrow (POST, 免 Token) — 字段实测
// ⚠️ real 无 preclose_px 字段 → 昨收用 last_px - px_change 推算(实测 97.94-4.87=93.07 与东财一致)
export async function fetchKplQuote(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetStockPanKou_Narrow', c: 'StockL2Data', DeviceID: KPL_DEVICE, StockID: code, State: 1, ...COMMON }, silent)
  if (!j || j.errcode !== '0' || !j.real) {
    if (silent) return null
    throw new Error('KPL行情失败')
  }
  const r = j.real
  const prevClose = typeof r.preclose_px === 'number'
    ? r.preclose_px
    : (typeof r.last_px === 'number' && typeof r.px_change === 'number' ? r.last_px - r.px_change : null)
  return {
    name: j.name || '', code: String(j.code || code),
    price: r.last_px, change: r.px_change, changePct: r.px_change_rate,
    open: r.open_px, prevClose, high: r.high_px, low: r.low_px,
    volume: r.total_amount, amount: r.total_turnover,
    turnover: r.turnover_ratio, volumeRatio: r.vol_ratio, amplitude: r.amplitude,
    pe: r.TTMPeRate, pb: r.dyn_pb_rate,
    totalCap: r.market_value, floatCap: r.circulation_value,
    mainFlowYi: null,   // 由 fetchMainFlow 独立覆盖
    upPx: r.up_px, downPx: r.down_px, avgPx: r.avg_px,
    quoteTime: typeof j.time === 'string' ? j.time : '',   // 数据时效展示(字段缺失则空)
  }
}

// 板块归属 GetFeaturedSection (GET) — info 行 [bk_code,bk_name,strength,leader_code,leader_name,leader_pct]
export async function fetchBoards(code, silent = false) {
  const url = `${HOST_SECTION}?${new URLSearchParams({ ...COMMON, PhoneOSNew: 2, Red: 1, VerSion: '6.2.31.1', a: 'GetFeaturedSection', c: 'StockL2Data', StockID: code, Token: KPL_TOKEN, UserID: KPL_USERID, DeviceID: KPL_DEVICE })}`
  const j = await getJson(url, silent)
  if (!j || j.errcode !== '0' || !Array.isArray(j.info)) {
    if (silent) return null
    throw new Error('KPL板块失败')
  }
  return j.info
    .map(r => ({ code: String(r[0]), name: r[1], strength: r[2], leaderCode: String(r[3]), leaderName: r[4], leaderPct: r[5] }))
    .sort((a, b) => (b.strength || 0) - (a.strength || 0))
}

// 板块完整成分 ZhiShuStockList_W8 (GET) — Date 必须横线格式 YYYY-MM-DD(实测 2026-08-07 磷化铟 16 只全量)
// 行 [code,name,"",0,tags,price,chg%,amount,换手,0,总市值,大单净买,大单净卖,大单净额,...,r22,r23连板标签,...]
// 实测(2026-08-10): Type=-4 涨幅排序, r[23]=连板标签("4连板"/"首板"/""), r[24]="龙一" 等仅在部分排序出现 → 只取连板
export async function fetchBoardConstituents(bkCode, day, silent = false) {
  const date = String(day || '').replace(/(\d{4})(\d{2})(\d{2})/, '$1-$2-$3')   // 8位 → 横线格式
  const url = `${HOST_HIS}?${new URLSearchParams({ a: 'ZhiShuStockList_W8', c: 'ZhiShuRanking', Date: date, PlateID: bkCode, Index: 0, Order: 1, st: 1000, Type: -4, IsKZZType: 0, TSZB: 0, TSZB_Type: 0, filterType: 0, old: 1, RStart: 925, REnd: 1500, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON })}`
  const j = await getJson(url, silent)
  const rows = j && j.list
  if (!Array.isArray(rows)) {
    if (silent) return null
    throw new Error('KPL成分失败')
  }
  return rows
    .map(r => ({
      code: String(r[0]), name: r[1], tags: r[4] || '',
      price: r[5], chgPct: r[6], amount: r[7], turnover: r[8],
      totalMv: r[10], bigBuy: r[11], bigSell: r[12], bigNet: r[13],
      boardLabel: r[23] || '',
    }))
    .sort((a, b) => (b.chgPct || 0) - (a.chgPct || 0))
}

// 个股涨停原因 #66 GetDayZhangTing (POST) — List 空 = 非涨停 → null; 失败 silent 返回 undefined
export async function fetchLimitReason(code, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetDayZhangTing', st: 25, c: 'HisLimitResumption', DeviceID: KPL_DEVICE, StockID: code, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  const list = j && j.List
  if (!Array.isArray(list)) {
    if (silent) return undefined
    throw new Error('涨停原因失败')
  }
  if (!list.length) return null   // 非涨停
  const it = list[0]
  return { zsCodes: (it.ZSCode || []).map(String), reason: it.Reason || '' }
}

// 主力资金 #60 StockDPRealData (POST) — ZLJE 主力净额(元)
export async function fetchMainFlow(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'StockDPRealData', c: 'StockYiDongKanPan', DeviceID: KPL_DEVICE, StockID: code, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || j.errcode !== '0' || typeof j.ZLJE !== 'number') {
    if (silent) return null
    throw new Error('主力资金失败')
  }
  return { zlBuy: j.ZLBuy, zlSell: j.ZLSell, zlJe: j.ZLJE }
}

// ============ 全市场组 (盘面页) ============

// 最强风口 #1 GetFengKListBest (POST) — 个股维度! 行 [股票代码,name,强度值,0,涨幅%,买额,0,0,总买?,净买?,标签串,ts,核心标签]
export async function fetchFengKou(silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetFengKListBest', c: 'StockFengKData', Time: '', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.List)) {
    if (silent) return null
    throw new Error('最强风口失败')
  }
  return j.List.map(r => ({
    code: String(r[0]), name: r[1], strength: r[2], chgPct: parseFloat(r[4]),
    buyAmt: r[8], sellAmt: r[9],
    tags: typeof r[10] === 'string' ? r[10].split('、').filter(Boolean) : [],
    tagSum: r[12] || '',
  }))
}

// 涨停天梯 #32 GetZhangTingTianTi_W47 (POST, 免Token) — StockList 分组; 行 [code,name,?,ts,bkCode,bkName,?,?,?,流通,总市值,板标签,?,连板数,...]
// 实测(2026-08-09): 行内板标签 r[11] 大多为空(如"4天4板"只少数有值), 分组必须用 r[13] 连板数(百花医药/沃格光电/云南锗业 r[13]=4=四连板)
export async function fetchTianTi(silent = false, capRows = 30) {
  const j = await postForm(HOST_HQ, { a: 'GetZhangTingTianTi_W47', c: 'FuPanLa', DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.StockList)) {
    if (silent) return null
    throw new Error('涨停天梯失败')
  }
  const seen = new Set()
  const rows = j.StockList.flat().filter(r => {
    const code = String(r[0])
    if (seen.has(code)) return false // 接口分组有重复(宝鼎科技同时出现在多组)
    seen.add(code)
    return true
  }).map(r => ({
    code: String(r[0]), name: r[1], bkCode: String(r[4] || ''), bkName: r[5] || '',
    label: r[11] || '', level: +r[13] || 0, cap: r[10],
  }))
  const groups = new Map()
  for (const row of rows) {
    const lv = row.level
    const key = lv > 0 ? 'b' + lv : 'b0'
    if (!groups.has(key)) {
      groups.set(key, {
        title: row.label || (lv === 1 ? '首板' : lv + '板'),
        level: lv, rows: [],
      })
    }
    groups.get(key).rows.push(row)
  }
  return [...groups.values()]
    .sort((a, b) => b.level - a.level)
    .map(g => ({ ...g, rows: g.rows.slice(0, capRows) }))
}

// 全市场涨停原因 #34 GetPlateInfo_w38 (POST) — nums.ZT 涨停家数; list 按板块分组, 行 [code,name,0,"",0,0,ts,0,amt,"首板",1,"板块tags",a,b,涨幅,amt2,"核心tag","原因全文",...]
export async function fetchMarketLimitReasons(date, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetPlateInfo_w38', c: 'HisLimitResumption', st: 100, Index: 0, Date: date, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.list)) {
    if (silent) return null
    throw new Error('涨停原因失败')
  }
  const groups = j.list.map(g => ({
    bkCode: String(g.ZSCode || ''), bkName: g.ZSName || '',
    stocks: (g.StockList || []).map(r => ({
      code: String(r[0]), name: r[1], chgPct: r[14], level: r[9] || '',
      tags: r[12] || '', reason: r[17] || '',
    })),
  }))
  return { nums: j.nums || {}, groups }
}

// 百日新高按板块 #44 GroupStock_W28 — GroupList 项 {GroupName:板块名, GroupID:板块代码, List:个股}
// 实测(2026-08-09): 板块视图 = GroupName + 新高家数(List.length); stocks 供板块详情按新高口径渲染
// (GetBKJJBL 是竞价异动口径, 与新高家数不一致 → src=nh 时用这里的 List)
export async function fetchNewHighBoards(silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GroupStock_W28', c: 'StockNewHigh', st: 15, Index: 0, Type: '0_0_0_0_0', DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.GroupList)) {
    if (silent) return null
    throw new Error('百日新高失败')
  }
  // List 行 [code,name,price,chg%,tags,资金×6,...,bkCode,bkName,涨幅%]
  return j.GroupList.map(g => ({
    bkCode: String(g.GroupID || ''), bkName: g.GroupName || '',
    count: (g.List || []).length,
    stocks: (g.List || []).map(r => ({
      code: String(r[0]), name: r[1], price: parseFloat(r[2]), chgPct: parseFloat(r[3]),
      tags: typeof r[4] === 'string' ? r[4].split('、').filter(Boolean) : [],
    })),
  }))
}

// 百日新高按个股 #45 GroupStock_W28 IsAll=1 — List 行 [code,name,price,chg,tags,...,1,bkCode,bkName,?]
export async function fetchNewHighStocks(silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GroupStock_W28', c: 'StockNewHigh', Order: 1, st: 50, Index: 0, IsAll: 1, Type: '0_0_0_0_0', OrderType: 2, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.List)) {
    if (silent) return null
    throw new Error('百日新高失败')
  }
  return j.List.map(r => ({
    code: String(r[0]), name: r[1], price: r[2], chgPct: r[3],
    tags: typeof r[4] === 'string' ? r[4].split('、').filter(Boolean) : [],
    bkCode: String(r[12] || ''), bkName: r[13] || '',
  }))
}

// 百日新高趋势 #46 GetDayNewHigh_W28 (POST) — x 项 "YYYYMMDD_新高数_次高数_0"
export async function fetchNewHighTrend(silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetDayNewHigh_W28', c: 'StockNewHigh', st: 360, Index: 0, GroupID: 'ALL', Type: '0_0_0_0_0', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.x)) {
    if (silent) return null
    throw new Error('新高趋势失败')
  }
  return j.x.map(s => {
    const [date, count] = String(s).split('_')
    return { date: date || '', count: +count || 0 }
  })
}

// 隔夜外围 #56 GlobalCommon (POST) — {CYWWZS:主要指数, RMGZ:股指期货, WWYD:异动}
export async function fetchGlobalIndexes(silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GlobalCommon', c: 'GlobalIndex', View: '1,2,3,4,5,6', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j) {
    if (silent) return null
    throw new Error('外围行情失败')
  }
  // 实测(2026-08-09): increase_rate 为 "0.28%" 字符串 → strip % 转数字; last/increase_amount 字符串数值 → 转数字
  const num = s => { const n = parseFloat(String(s).replace('%', '')); return Number.isFinite(n) ? n : null }
  const map = row => ({ code: row.code, name: row.prod_name, last: num(row.last_px), chgPct: num(row.increase_rate), chgAmt: num(row.increase_amount) })
  return {
    indexes: (j.CYWWZS || []).map(map),
    futures: (j.RMGZ || []).map(map),
    movers: (j.WWYD || []).map(map),
  }
}

// 机构增仓 #41/42 GGList_JGCC (POST) — List 行 [bkCode,bkName,增仓额,家数,总额,占比%,均增%,总市值,0]; IsBX=1含北向/0过滤
// 实测(2026-08-09): 占比% 可为负(如风电 -4.36% 而增仓额为正) → 不是"增仓额/总额"的简单比值,
//   更接近"较上期占比变动"类口径(接口未公开); UI 表头 tooltip 已注明, 不当作净增仓比例解读
export async function fetchInstitutionIncrease(reportDate, isBX = false, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GGList_JGCC', c: 'ZhuLiChiCang', Type: 1, Order: 1, Index: 0, st: 30, IsBX: isBX ? 1 : 0, Date: reportDate, DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.List)) {
    if (silent) return null
    throw new Error('机构增仓失败')
  }
  // 实测(2026-08-09): 行 [bkCode,bkName,增仓额(元),家数,总额(元),占比%,均增%,总市值(元),0] — 金额类 /1e8 转亿
  const yi = s => { const n = parseFloat(s); return Number.isFinite(n) ? +(n / 1e8).toFixed(2) : null }
  return j.List.map(r => ({
    bkCode: String(r[0]), bkName: r[1], addAmt: yi(r[2]), count: parseFloat(r[3]),
    totalAmt: yi(r[4]), ratio: parseFloat(r[5]), avgRatio: parseFloat(r[6]), totalMv: yi(r[7]),
  }))
}

// 市场情绪 #11 ChangeStatistics (POST) — info[] 按 Day 倒序(最新在前), 字段: strong强度 ztjs涨停 lbgd连板高度 df_num跌停 Day
export async function fetchMarketMood(silent = false) {
  const j = await postForm(HOST_HIS, { a: 'ChangeStatistics', st: 100, c: 'HisHomeDingPan', Index: 0, DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.info)) {
    if (silent) return null
    throw new Error('市场情绪失败')
  }
  return j.info.map(r => ({
    day: String(r.Day || ''), strong: +r.strong || 0, zt: +r.ztjs || 0,
    lbgd: +r.lbgd || 0, df: +r.df_num || 0,
  }))
}

// 涨停池 DailyLimitPerformance — 周期引擎数据源(与 auction_spider.zt_pool 同款字段序)
// rt=true → 当日实时(c=HomeDingPan, 无 Day); 否则历史(c=HisHomeDingPan, Day=date, 只服务已完成交易日)
export async function fetchLimitPool(date, pidType = 1, { rt = false, silent = false } = {}) {
  const base = { a: 'DailyLimitPerformance', PidType: pidType, Type: 4, Index: 0, Order: 0, st: 500, apiv: 'w39', DeviceID: KPL_DEVICE }
  const j = rt
    ? await postForm(HOST_HQ, { ...base, c: 'HomeDingPan' }, silent)
    : await postForm(HOST_HIS, { ...base, c: 'HisHomeDingPan', Day: date }, silent)
  if (!j || !Array.isArray(j.info)) {
    if (silent) return []
    throw new Error('涨停池失败')
  }
  const seen = new Set()
  return j.info.flat().filter(r => {
    if (!Array.isArray(r) || r.length < 14) return false
    const code = String(r[0])
    if (seen.has(code)) return false // RT 接口按板位组返回有跨组重复
    seen.add(code)
    return true
  }).map(r => ({
    code: String(r[0]), name: String(r[1]), pid: pidType,
    ztTime: typeof r[4] === 'number' ? r[4] : null,
    reason: String(r[5] || ''), seal: +r[6] || 0, maxSeal: +r[7] || 0,
    mainNet: +r[8] || 0, amount: +r[11] || 0,
    plates: String(r[12] || '').split('、').filter(Boolean), circMv: +r[13] || 0,
    turnover: +r[14] || 0,
  }))
}

// 未涨停池 DailyLimitPerformance2 (RT) — 昨N板今未封(炸板/未封): 1进2半路候选 + 高标开板分歧信号
// 行: [code,name,?,?,现价,涨幅%,板块,主力净额,主力买入,主力卖出,成交额,...] (kpl-api.md #未涨停)
export async function fetchUnsealedPool(pidType = 1, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'DailyLimitPerformance2', PidType: pidType, Type: 5, Order: 1, Index: 0, st: 100, apiv: 'w40', c: 'HomeDingPan', DeviceID: KPL_DEVICE }, silent)
  if (!j || !Array.isArray(j.info)) {
    if (silent) return []
    throw new Error('未涨停池失败')
  }
  return j.info.flat().filter(r => Array.isArray(r) && r.length >= 11).map(r => ({
    code: String(r[0]), name: String(r[1]), pid: pidType,
    price: +r[4] || 0, pct: +r[5] || 0,
    plates: String(r[6] || '').split('、').filter(Boolean),
    mainNet: +r[7] || 0, amount: +r[10] || 0,
  }))
}

// 涨跌/炸板分析 RiseFallAnalysis — 实时当日 + 历史 250 天序列
// 行: [涨停, 跌停, 自然涨停, 曾跌停, 破板率, 炸板数, 日期]
export async function fetchRiseFall(silent = false) {
  const rt = await postForm(HOST_HQ, { a: 'RiseFallAnalysis', apiv: 'w43', c: 'HomeDingPan', PhoneOSNew: 1, DeviceID: KPL_DEVICE }, silent)
  const his = await postForm(HOST_HIS, { a: 'RiseFallAnalysis', st: 250, apiv: 'w43', c: 'HisHomeDingPan', PhoneOSNew: 1, Index: 0, DeviceID: KPL_DEVICE }, silent)
  const map = r => ({ zt: +r[0] || 0, dt: +r[1] || 0, brokeRate: +r[4] || 0, zhaban: +r[5] || 0, day: String(r[6] || '') })
  const todayRow = (Array.isArray(rt?.info) && rt.info[0]) || null
  const series = (Array.isArray(his?.info) ? his.info : []).filter(r => Array.isArray(r) && r.length >= 7).map(map)
  const today = todayRow ? map(todayRow) : (series[0] || null)
  if (!today) {
    if (silent) return null
    throw new Error('涨跌分析失败')
  }
  return { today, series }
}

// 赚钱效应展开 #29 GetMoneyDetail (POST) — Detail 键 -5..-1(亏) 1..5(赚) 各档家数 + num 总数; ttag 赚钱效应值
export async function fetchMoneyEffect(day, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetMoneyDetail', c: 'Emotion', Day: day || '', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !j.Detail) {
    if (silent) return null
    throw new Error('赚钱效应失败')
  }
  const d = j.Detail
  const tier = (k) => +d[k] || 0
  return {
    day: j.Day || day || '', num: +d.num || 0, ttag: parseFloat(j.ttag),
    // 涨/跌两端分开: 大赚5档 大亏5档
    gain: [tier('5'), tier('4'), tier('3'), tier('2'), tier('1')],
    loss: [tier('-5'), tier('-4'), tier('-3'), tier('-2'), tier('-1')],
  }
}

// 盘面亮点 #30 GetPMSL_PMLD (POST) — List[] {TimeMin(Unix), TagID, ZSCode板块代码, Detail播报文本, TagName标签, ZSName板块名, StockList[[code,name]]}
export async function fetchMarketHighlights(silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetPMSL_PMLD', st: 30, c: 'FuPanLa', Index: 0, DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.List)) {
    if (silent) return null
    throw new Error('盘面亮点失败')
  }
  return j.List.map(r => ({
    time: r.TimeMin, tagName: r.TagName || '', tagId: r.TagID,
    bkCode: String(r.ZSCode || ''), bkName: r.ZSName || '',
    detail: r.Detail || '',
    stocks: (r.StockList || []).map(s => ({ code: String(s[0]), name: s[1] })),
  }))
}

// 板块标注 #9 GetPoint (POST) — list[] {Time(Unix), Plate标注文本, PlateCode, PlateName, PlateJE成交额, PlateZDF涨跌幅, Color}
export async function fetchBoardAnnotations(silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetPoint', c: 'ConceptionPoint', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.list)) {
    if (silent) return null
    throw new Error('板块标注失败')
  }
  return j.list.map(r => {
    const zdf = parseFloat(r.PlateZDF)
    return {
      time: r.Time, text: r.Plate || '',
      bkCode: String(r.PlateCode || ''), bkName: r.PlateName || '',
      je: parseFloat(r.PlateJE) || 0, zdf: Number.isFinite(zdf) ? zdf : null,   // 非板块标注 PlateZDF 为空串 → null, 避免 NaN%
      color: String(r.Color || '0'),
    }
  }).filter(x => x.text)   // 过滤空文本行(个别行只有涨跌提示)
}

// 龙虎榜 #49 GetStockList (POST) — list[] {ID, Name, IncreaseAmount涨幅%, BuyIn净买入(元,可负), JoinNum机构家数, Turnover成交额, CircPrice流通市值, Amplitude振幅, TurnoverRatio换手率, Capitalization总市值}
export async function fetchLhbList(silent = false) {
  const j = await postForm(HOST_LHB, { a: 'GetStockList', st: 500, c: 'LongHuBang', Index: 0, Type: 1, Time: '', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.list)) {
    if (silent) return null
    throw new Error('龙虎榜失败')
  }
  return {
    time: j.Time || '',
    list: j.list.map(r => ({
      code: String(r.ID), name: r.Name,
      chgPct: parseFloat(String(r.IncreaseAmount).replace('%', '')),
      buyIn: +r.BuyIn || 0, joinNum: +r.JoinNum || 0,
      turnover: +r.Turnover || 0, circMv: +r.CircPrice || 0, totalMv: +r.Capitalization || 0,
      amplitude: parseFloat(r.Amplitude), turnoverRatio: parseFloat(r.TurnoverRatio),
    })).sort((a, b) => b.buyIn - a.buyIn),   // 净买入降序, 榜首更聚焦
  }
}

// ============ 个股组 (详情页 资讯|基本面 tab) ============

// 新闻/公告/研报列表 #51/52/55 GetList (POST) — NoticeList [{iid,code,date,title,name}]; type: 1新闻/2研报/3公告
export async function fetchInfoList(code, type = 1, index = 0, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetList', c: 'CompanyNotice', StockID: code, Index: index, st: 34, Type: type, DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.NoticeList)) {
    if (silent) return null
    throw new Error('资讯列表失败')
  }
  return j.NoticeList.map(n => ({ iid: String(n.iid), code: n.code, date: n.date, title: n.title }))
}

// 新闻/公告正文 #53 GetContentNew (POST) — {content: HTML}
// 实测(2026-08-09): Type=2 研报 content 恒为空, 只有 SourceUrl → 返回原文链接兜底
export async function fetchInfoContent(iid, type = 1, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetContentNew', c: 'CompanyNotice', iid: iid, Type: type, isZhiShu: 0, DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j) {
    if (silent) return null
    throw new Error('正文失败')
  }
  if (!j.content && !j.SourceUrl) {
    if (silent) return null
    throw new Error('正文失败')
  }
  return { content: stripHtml(j.content || ''), sourceUrl: j.SourceUrl || '', title: j.title || '' }
}

// 公告正文 #54 GGDetail (POST) — GGDetail[0] {title, SourceUrl, content: HTML}
export async function fetchAnnounceContent(iid, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GGDetail', c: 'AnnouncementList', iid: iid, DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  const g = j && j.GGDetail && j.GGDetail[0]
  if (!g) {
    if (silent) return null
    throw new Error('公告正文失败')
  }
  return { title: g.title, content: stripHtml(g.content), sourceUrl: g.SourceUrl || '' }
}

// 公司资料 #62 GetCompanyInfo (POST) — XXList[0] 基本信息 + ZYGCList 主营构成
export async function fetchF10Company(code, silent = false) {
  const j = await postForm(HOST_ART, { a: 'GetCompanyInfo', c: 'StockF10Basic', StockID: code, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  const L = j && j.List
  if (!L || !Array.isArray(L.XXList)) {
    if (silent) return null
    throw new Error('公司资料失败')
  }
  const info = L.XXList[0] || {}
  const zg = L.ZYGCList || {}
  return {
    info: {
      name: info.CName, address: info.Address, office: info.OfficeAddress,
      chairman: info.Chairman, secretary: info.Secretary,
      mainSale: info.MainSale, troHold: info.TroHold, actHold: info.ActHold,
    },
    biz: { hy: zg.HY, hyDate: zg.HYDate, cp: zg.CP, cpDate: zg.CPDate, dq: zg.DQ, dqDate: zg.DQDate },
  }
}

// 财务指标 #65 GetMainIndicators (POST) — 键: GMJLR归母净利 KFJLR扣非 YYSR营收 JZCSYL净资产收益率 XSMLL毛利率 JLL净利率 ZCFZL负债率 JYHDXJL经营现金流
export async function fetchF10Finance(code, silent = false) {
  const j = await postForm(HOST_ART, { a: 'GetMainIndicators', c: 'StockF10Basic', Type: 0, StockID: code, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.YYSR)) {
    if (silent) return null
    throw new Error('财务指标失败')
  }
  const label = {
    YYSR: '营业收入', GMJLR: '归母净利润', KFJLR: '扣非净利润', JZCSYL: '净资产收益率',
    XSMLL: '销售毛利率', JLL: '净利率', ZCFZL: '资产负债率', JYHDXJL: '经营现金流',
  }
  const series = {}
  for (const k of Object.keys(label)) {
    if (Array.isArray(j[k])) {
      series[label[k]] = j[k].map(y => ({ year: y.name, value: y.value, tb: y.tb, quarter: (y.quarter || []).map(q => ({ name: q.name, value: q.value, tb: q.tb })) }))
    }
  }
  return series
}

// 十大股东/股东户数 #63 GetGuDong (POST) — GuDong: {GDRS 户数, SDGDData 十大股东, SDGDDate}
export async function fetchF10Shareholders(code, silent = false) {
  const j = await postForm(HOST_HIS, { a: 'GetGuDong', c: 'YiDianCangWei', Type: 2, StockID: code, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  const g = j && j.GuDong
  if (!g) {
    if (silent) return null
    throw new Error('股东信息失败')
  }
  return {
    counts: (g.GDRS || []).map(r => ({ day: r.Day, count: r.GDRS, change: r.JSQBH, price: r.Price })),
    top10: (g.SDGDData || []).map(r => ({ name: r.JG, ratio: r.ZLTBL, shares: r.CYSL, change: r.SJJZC })),
    date: (g.SDGDDate || [])[0] || '',
    countChange: g.JSQBH,
  }
}

// 估值 #64 GetValuation (POST) — list 行 [日期, 价格, PE]
export async function fetchF10Valuation(code, silent = false) {
  const j = await postForm(HOST_ART, { a: 'GetValuation', c: 'StockF10Basic', year: 1, key: 'PE', StockID: code, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.list)) {
    if (silent) return null
    throw new Error('估值数据失败')
  }
  return j.list.map(r => ({ date: r[0], price: parseFloat(r[1]), pe: parseFloat(r[2]) }))
}

// 竞价快照 (盘面页竞价卡) — 复用 loader.js
export function fetchAuctionSummary() {
  return fetchAuction()
}

// ============ 详情页短线功能(2026-08-10 新增, 参数来自 jiarenmens/src/spiders/auction_spider.py 实测) ============

// 完整盘口 GetStockPanKou (POST) — 含 10 级 weituo/内外盘; 字段联调验证
// ⚠️ 盘口主源用腾讯五档(loadTencentPankou, 实测可靠), 本函数为 KPL 增强可选
export async function fetchStockPankou(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetStockPanKou', c: 'StockL2Data', DeviceID: KPL_DEVICE, StockID: code, State: 1, ...COMMON }, silent)
  if (!j || j.errcode !== '0') return null
  return j
}

// 逐笔大单 GetMainMonitor_w30 (POST) — 行 [时间,价格,方向0买1卖,手数,类型?,金额]
export async function fetchMainMonitor(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetMainMonitor_w30', c: 'StockYiDongKanPan', Order: 0, st: 20, Index: 0, Money: 2, StockID: code, IsBS: 0, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.List)) return null
  return j.List.map(r => ({
    time: String(r[0] || ''), price: parseFloat(r[1]),
    side: r[2] === '0' ? '买' : '卖', vol: parseFloat(r[3]),
    amount: parseFloat(r[5]),
    type: parseFloat(r[3]) >= 100 ? '超大' : parseFloat(r[3]) >= 50 ? '大单' : '中单',
  }))
}

// 涨停基因 GetZhangTingGene (GET, 免Token) — List[涨停次数,5%溢价次,次日红盘%,首板封板率%,破板率%,连板率%]
export async function fetchZhangTingGene(code, silent = false) {
  const url = `${HOST_HQ}?${new URLSearchParams({ a: 'GetZhangTingGene', apiv: 'w42', c: 'StockL2Data', StockID: code, PhoneOSNew: 1, DeviceID: KPL_DEVICE, VerSion: '5.21.0.0' })}`
  const j = await getJson(url, silent)
  if (!j || !Array.isArray(j.List) || !j.List.length) return null
  const g = j.List[0]
  return { ztCount: g[0], premium5: g[1], nextRedPct: g[2], firstSealPct: g[3], breakPct: g[4], lianbanPct: g[5] }
}

// 竞价分时 GetStockBid (POST) — bid[[时间,价格,买卖方向,累计量],...]
export async function fetchStockBid(code, silent = false) {
  const j = await postForm(HOST_HQ, { a: 'GetStockBid', c: 'StockL2Data', apiv: 'w41', StockID: code, DeviceID: KPL_DEVICE, ...COMMON }, silent)
  if (!j || !Array.isArray(j.bid)) return null
  return j.bid.map(r => ({ time: String(r[0]), price: parseFloat(r[1]), side: r[2], cumVol: parseFloat(r[3]) }))
}

// 龙虎榜个股历史 GetStockList (POST) — 全市场榜单按 code 过滤(联调确认是否支持 StockID 参数)
// ⚠️ dealer 字段: 该股当日买卖营业部名数组(游资标签用)。真实字段名以联调返回为准 ——
//   KPL 龙虎榜返回通常含买卖营业部明细(联调时找到营业部名所在字段映射到 dealer);
//   若接口无逐日营业部明细, dealer 置空数组, LhbStockCard 只显示净买入/机构家数(游资标签随之隐藏)。
export async function fetchStockLhbHistory(code, silent = false) {
  const j = await postForm(HOST_LHB, { a: 'GetStockList', st: 500, c: 'LongHuBang', Index: 0, Type: 1, Time: '', DeviceID: KPL_DEVICE, Token: KPL_TOKEN, UserID: KPL_USERID, ...COMMON }, silent)
  if (!j || !Array.isArray(j.list)) return null
  return j.list
    .filter(r => String(r.ID) === String(code))
    .map(r => ({
      date: j.Time || '', code: String(r.ID), name: r.Name,
      chgPct: parseFloat(String(r.IncreaseAmount).replace('%', '')),
      buyIn: +r.BuyIn || 0, joinNum: +r.JoinNum || 0,
      dealer: [],   // ← 联调填: 当日营业部名数组(如 ["华鑫证券上海分公司", "国泰君安南京太平南路"]); 无则空数组
    }))
}
