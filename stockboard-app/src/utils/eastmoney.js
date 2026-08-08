// 东方财富 secid 市场标识: 0=深市 1=沪市 2=北交所 116=港股
export function emMarket(code) {
  if (!code) return '0'
  if (/^\d{5}$/.test(code)) return '116'
  if (/^(4|8|92)/.test(code)) return '2'
  if (/^[679]/.test(code)) return '1'
  return '0'
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
