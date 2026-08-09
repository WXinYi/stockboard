// 自建股票详情数据层: 行情(push2delay 主 + 腾讯备) + K线/分时(腾讯 host)
import { ref } from 'vue'
import { emQuoteApi, jsonp, qqKlineUrl, qqMinuteUrl, qqMklineUrl, qqPrefix, qqQuoteUrl } from '../utils/eastmoney.js'

function withTimeout(promise, ms = 8000) {
  return Promise.race([promise, new Promise((_, rej) => setTimeout(() => rej(new Error('请求超时')), ms))])
}

// 加载脚本并读全局变量(腾讯行情备源: qt.gtimg.cn 返回 v_sz000938="..." 赋值语句)
function loadScriptVar(url, varName, timeout = 8000) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    const timer = setTimeout(() => { script.remove(); reject(new Error('超时')) }, timeout)
    script.onload = () => {
      clearTimeout(timer)
      const v = window[varName]
      script.remove()
      if (v !== undefined) resolve(v)
      else reject(new Error('变量缺失'))
    }
    script.onerror = () => { clearTimeout(timer); script.remove(); reject(new Error('加载失败')) }
    script.src = url
    document.head.appendChild(script)
  })
}

// 腾讯 v_ 变量 → 行情对象(标准字段布局)
function parseTencentQuote(raw) {
  const p = String(raw).split('~')
  if (p.length < 50) return null
  return {
    name: p[1], code: p[2],
    price: parseFloat(p[3]), prevClose: parseFloat(p[4]),
    open: parseFloat(p[5]), high: parseFloat(p[33]), low: parseFloat(p[34]),
    change: parseFloat(p[31]), changePct: parseFloat(p[32]),
    volume: parseFloat(p[36]), amountWan: parseFloat(p[37]),
    turnover: parseFloat(p[38]), peTtm: parseFloat(p[39]),
    floatCapYi: parseFloat(p[44]), totalCapYi: parseFloat(p[45]),
    pb: parseFloat(p[46]), volumeRatio: parseFloat(p[49]),
    mainFlowYi: null,
  }
}

export function useStockDetail(code) {
  // code 可能是字符串 / 函数 / ref(computed) —— 统一在调用时取值
  const getCode = () => {
    if (typeof code === 'function') return code()
    if (code && typeof code === 'object' && 'value' in code) return code.value
    return code
  }

  const quote = ref(null)
  const kline = ref([])
  const trend = ref([])
  const loading = ref({ quote: false, chart: false })
  const error = ref('')

  async function loadQuote() {
    loading.value.quote = true
    error.value = ''
    try {
      const r = await withTimeout(jsonp(emQuoteApi(getCode())))
      const d = r && r.data
      if (!d) throw new Error('行情数据为空')
      quote.value = {
        name: d.f58, code: d.f57, price: d.f43, high: d.f44, low: d.f45,
        open: d.f46, prevClose: d.f60, volume: d.f47, amount: d.f48,
        pe: d.f162, pb: d.f167, totalCap: d.f116, floatCap: d.f117,
        turnover: d.f168, change: d.f169, changePct: d.f170,
        // f62 主力净流入(亿); 数值异常(如 -)则置 null 隐藏
        mainFlowYi: (typeof d.f62 === 'number' && isFinite(d.f62) && Math.abs(d.f62) < 10000) ? d.f62 : null,
      }
    } catch (e) {
      // 降级: 腾讯脚本变量
      try {
        const c = getCode()
        const raw = await loadScriptVar(qqQuoteUrl(c), 'v_' + qqPrefix(c) + c)
        const q = parseTencentQuote(raw)
        if (!q) throw new Error('腾讯行情解析失败')
        quote.value = q
      } catch (e2) {
        error.value = '行情加载失败'
      }
    } finally {
      loading.value.quote = false
    }
  }

  async function loadKline(period = 'day', adjust = 'qfq') {
    loading.value.chart = true
    error.value = ''
    try {
      const c = getCode()
      let url, key, intraday = false
      if (period === 'm60') {
        // 分钟K线: 不复权, 时间 "YYYYMMDDHHMM" → UTC 秒
        url = qqMklineUrl(c, period, 320)
        key = 'm60'
        intraday = true
      } else {
        url = qqKlineUrl(c, period, 320, adjust)
        key = (adjust ? 'qfq' : '') + period   // 复权→qfqday/qfqweek, 不复权→day/week
      }
      const resp = await withTimeout(fetch(url))
      const j = await resp.json()
      const box = j.data && j.data[qqPrefix(c) + c]
      const rows = box ? (box[key] || box[period]) : null
      // 腾讯列序: [date, open, close, high, low, volume]
      kline.value = (rows || []).map(r => {
        let time = r[0]
        if (intraday && typeof time === 'string' && time.length === 12) {
          time = Date.UTC(+time.slice(0, 4), +time.slice(4, 6) - 1, +time.slice(6, 8), +time.slice(8, 10), +time.slice(10, 12)) / 1000
        }
        return { time, open: +r[1], close: +r[2], high: +r[3], low: +r[4], volume: +r[5] }
      })
    } catch (e) {
      error.value = 'K线加载失败'
    } finally {
      loading.value.chart = false
    }
  }

  async function loadTrend() {
    loading.value.chart = true
    error.value = ''
    try {
      const c = getCode()
      const resp = await withTimeout(fetch(qqMinuteUrl(c)))
      const j = await resp.json()
      const box = j.data && j.data[qqPrefix(c) + c]
      const rows = (box && box.data && box.data.data) || []
      const dateStr = (box && box.data && box.data.date) || ''
      // 行格式 "HHMM price vol amount" → 组装 {time, price}; 时间用 UTC-naive 秒(中国时区渲染)
      trend.value = rows.map(r => {
        const [hhmm, price] = String(r).split(' ')
        let time = 0
        if (dateStr && hhmm && hhmm.length === 4) {
          const y = +dateStr.slice(0, 4), mo = +dateStr.slice(4, 6) - 1, d = +dateStr.slice(6, 8)
          time = Date.UTC(y, mo, d, +hhmm.slice(0, 2), +hhmm.slice(2, 4)) / 1000
        }
        return { time, price: parseFloat(price) }
      })
    } catch (e) {
      error.value = '分时加载失败'
    } finally {
      loading.value.chart = false
    }
  }

  return { quote, kline, trend, loading, error, loadQuote, loadKline, loadTrend }
}
