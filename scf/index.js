// KPL 中转代理 — 腾讯云 SCF Web 函数（监听 9000 端口）
// 逻辑同 worker/index.js: UA=okhttp/3.12.1 + 4 路径前缀转发 + CORS
// 路径前缀与 vite dev proxy 一致: /kpl-hq /kpl-his /kpl-art /kpl-sec /kpl-lhb → 对应开盘啦子域
const http = require('http')
const https = require('https')

const TARGETS = {
  '/kpl-hq': 'https://apphwhq.longhuvip.com/w1/api/index.php',
  '/kpl-his': 'https://apphis.longhuvip.com/w1/api/index.php',
  '/kpl-art': 'https://apparticle.longhuvip.com/w1/api/index.php',
  '/kpl-sec': 'https://apphwshhq.kaipanhong.com/w1/api/index.php',
  '/kpl-lhb': 'https://applhb.longhuvip.com/w1/api/index.php',
}

const server = http.createServer((req, res) => {
  const pathname = req.url.split('?')[0]
  const target = TARGETS[pathname]
  if (!target) { res.writeHead(404, { 'Access-Control-Allow-Origin': '*' }); res.end('kpl-proxy: unknown path'); return }

  const headers = {
    'User-Agent': 'okhttp/3.12.1',
    'Accept': '*/*',
    'Accept-Language': 'zh-CN,zh;q=0.9',
  }
  // 转发响应: CORS + 必需头, body 原样透传
  const forward = (upstream) => {
    const out = {
      'Access-Control-Allow-Origin': '*',
      'Content-Type': upstream.headers['content-type'] || 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    }
    if (upstream.headers['content-encoding']) out['Content-Encoding'] = upstream.headers['content-encoding']
    res.writeHead(upstream.statusCode, out)
    upstream.pipe(res)
  }
  const fail = (e) => { res.writeHead(502, { 'Access-Control-Allow-Origin': '*' }); res.end('upstream error: ' + e.message) }

  if (req.method === 'GET') {
    const q = req.url.includes('?') ? req.url.slice(pathname.length) : ''
    https.get(target + q, { headers }, forward).on('error', fail)
  } else if (req.method === 'POST') {
    headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
    const chunks = []
    req.on('data', c => chunks.push(c))
    req.on('end', () => {
      const r = https.request(target, { method: 'POST', headers }, forward)
      r.on('error', fail)
      r.end(Buffer.concat(chunks))
    })
  } else {
    res.writeHead(405, { 'Access-Control-Allow-Origin': '*' }); res.end('method not allowed')
  }
})

server.listen(9000, () => console.log('kpl-proxy listening on 9000'))
