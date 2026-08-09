// KPL 中转代理 — 浏览器 UA 会被开盘啦风控(返回空 List), 此 Worker 用 okhttp UA 转发
// 与 vite dev proxy 同配方(vite.config.js): UA=okhttp/3.12.1 + 不转发 Origin/Referer
// 路径前缀与 dev 一致: /kpl-hq /kpl-his /kpl-art /kpl-sec → 对应开盘啦子域
const TARGETS = {
  '/kpl-hq': 'https://apphwhq.longhuvip.com/w1/api/index.php',
  '/kpl-his': 'https://apphis.longhuvip.com/w1/api/index.php',
  '/kpl-art': 'https://apparticle.longhuvip.com/w1/api/index.php',
  '/kpl-sec': 'https://apphwshhq.kaipanhong.com/w1/api/index.php',
}

export default {
  async fetch(request) {
    const url = new URL(request.url)
    const target = TARGETS[url.pathname]
    if (!target) return new Response('kpl-proxy: unknown path', { status: 404 })

    const headers = {
      'User-Agent': 'okhttp/3.12.1',
      'Accept': '*/*',
      'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    let upstream
    if (request.method === 'GET') {
      const q = url.searchParams.toString()
      upstream = await fetch(q ? target + '?' + q : target, { headers, redirect: 'follow' })
    } else if (request.method === 'POST') {
      // 直接转发 form body, 用 form Content-Type(与 dev proxy 行为一致)
      headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
      upstream = await fetch(target, { method: 'POST', headers, body: await request.text(), redirect: 'follow' })
    } else {
      return new Response('method not allowed', { status: 405 })
    }

    const resp = new Response(upstream.body, { status: upstream.status })
    resp.headers.set('Access-Control-Allow-Origin', '*')
    resp.headers.set('Content-Type', upstream.headers.get('Content-Type') || 'application/json; charset=utf-8')
    resp.headers.set('Cache-Control', 'no-store')
    return resp
  },
}
