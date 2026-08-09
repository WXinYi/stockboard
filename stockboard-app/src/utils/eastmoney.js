// 东方财富 secid 市场标识: 0=深市 1=沪市 2=北交所 116=港股
// 覆盖: A股(6/0/3) + 北交所(4/8/92) + 港股(5位) + 转债(11x/10x沪, 12x深) + 基金(5沪, 15/16/18深) + B股(9沪, 2深)
export function emMarket(code) {
  if (!code) return '0'
  if (/^\d{5}$/.test(code)) return '116'      // 港股 5 位
  if (/^(4|8|92)/.test(code)) return '2'      // 北交所
  if (/^(6|5|9|11|10)/.test(code)) return '1' // 沪市: 6主板/688科创 5基金 900B股 110/113/118转债 100+国债
  return '0'                                   // 深市: 0/3 A股 200B股 12x转债 15/16/18基金 及默认
}
export function secid(code) {
  return `${emMarket(code)}.${code}`
}

// 东财 H5 个股页(移动版自动适配): https://quote.eastmoney.com/sz000938.html
export function emQuoteUrl(code) {
  const m = emMarket(code)
  const pfx = m === '1' ? 'sh' : m === '2' ? 'bj' : m === '116' ? 'hk' : 'sz'
  return `https://quote.eastmoney.com/${pfx}${code}.html`
}

// 同花顺 H5 个股页(移动版自动适配): https://stockpage.10jqka.com.cn/000938/
export function thsUrl(code) {
  return `https://stockpage.10jqka.com.cn/${code}/`
}

// JSONP 助手: script 注入绕 CORS, 8s 超时
export function jsonp(url, cbParam = 'cb', timeout = 8000) {
  return new Promise((resolve, reject) => {
    const cbName = '_em' + Date.now() + '_' + Math.random().toString(36).slice(2, 8)
    const script = document.createElement('script')
    const cleanup = () => { delete window[cbName]; script.remove() }
    const timer = setTimeout(() => { cleanup(); reject(new Error('数据请求超时')) }, timeout)
    window[cbName] = (data) => { clearTimeout(timer); cleanup(); resolve(data) }
    script.src = url + (url.includes('?') ? '&' : '?') + cbParam + '=' + cbName
    script.onerror = () => { clearTimeout(timer); cleanup(); reject(new Error('数据请求失败')) }
    document.head.appendChild(script)
  })
}

// ── 自建详情页数据源(实测可用) ──
// 行情走 push2delay(东财延时host, 浏览器JSONP可用); K线/分时走腾讯host(fetch直连无CORS)

// 腾讯代码前缀: 6/5/9/11/10→sh, 0/3/12/15/16/18→sz, 4/8/92→bj, 港股5位→hk
export function qqPrefix(code) {
  if (!code) return 'sz'
  if (/^\d{5}$/.test(code)) return 'hk'
  if (/^(4|8|92)/.test(code)) return 'bj'
  if (/^(6|5|9|11|10)/.test(code)) return 'sh'
  return 'sz'
}

// 东财单股行情 push2delay(fltt=2 返回小数价; f62=主力净流入亿, 单位存疑则兜底隐藏)
export function emQuoteApi(code, fields) {
  const f = fields || 'f43,f44,f45,f46,f47,f48,f57,f58,f60,f62,f116,f117,f162,f167,f168,f169,f170'
  return `https://push2delay.eastmoney.com/api/qt/stock/get?secid=${secid(code)}&fltt=2&invt=2&fields=${f}`
}

// 腾讯 K线: adjust 为 qfq/hfq 走 fqkline, 不复权走 kline/kline(count 下限 320)
export function qqKlineUrl(code, period = 'day', count = 320, adjust = 'qfq') {
  const pfx = qqPrefix(code)
  const endpoint = adjust ? 'fqkline/get' : 'kline/kline'
  const suffix = adjust ? ',' + adjust : ''
  return `https://web.ifzq.gtimg.cn/appstock/app/${endpoint}?param=${pfx}${code},${period},,,${count}${suffix}`
}

// 腾讯当日分时
export function qqMinuteUrl(code) {
  return `https://ifzq.gtimg.cn/appstock/app/minute/query?code=${qqPrefix(code)}${code}`
}

// 腾讯分钟K线(m5/m15/m30/m60), 不复权; 行: [YYYYMMDDHHMM, open, close, high, low, volume, ...]
export function qqMklineUrl(code, period = 'm60', count = 320) {
  return `https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=${qqPrefix(code)}${code},${period},,${count}`
}

// 腾讯实时行情(备源, 脚本变量 v_<pfx><code>)
export function qqQuoteUrl(code) {
  return `https://qt.gtimg.cn/q=${qqPrefix(code)}${code}`
}
