// 股票搜索建议: 东财 searchapi type=14(行情建议), JSONP 免 CORS
// 返回 [{ code, name, market }], 仅 A股(沪A/深A/京A) — 代码可直接用于 /stock/:code 路由
import { jsonp } from './eastmoney.js'

const SUGGEST_TOKEN = 'D43BF722C8E33BDC906FB84D85E326E8'   // 东财网页版公开 token

export async function searchStock(input, count = 8) {
  const kw = String(input || '').trim()
  if (!kw) return []
  const url = `https://searchapi.eastmoney.com/api/suggest/get?input=${encodeURIComponent(kw)}&type=14&token=${SUGGEST_TOKEN}&count=${count}`
  const r = await jsonp(url)
  const rows = r?.QuotationCodeTable?.Data || []
  return rows
    .filter(d => /A$/.test(d.SecurityTypeName || ''))
    .map(d => ({ code: d.Code, name: d.Name, market: d.SecurityTypeName }))
}
