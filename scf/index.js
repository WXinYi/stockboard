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

// SCF 函数 Timeout=10s、前端 withTimeout=8s。上游若挂起(响应头或 body 都不返回)，
// 旧实现只监听 'error' → 请求永久 pending，直到平台超时；前端只能报「请求超时」。
// 这里在 7s 主动收口：上游连接 destroy + 未发头则回 502，已发头则断开响应。
const DEFAULT_TIMEOUT_MS = 7000

/**
 * 可注入 targets / transport / timeoutMs，便于 node:test 用本地 http server 做上游。
 * 线上 zip 只含本文件 + scf_bootstrap，不能拆出额外模块。
 */
function createServer({ targets = TARGETS, transport = https, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  return http.createServer((req, res) => {
    const pathname = (req.url || '').split('?')[0]
    const target = targets[pathname]
    if (!target) {
      res.writeHead(404, { 'Access-Control-Allow-Origin': '*' })
      res.end('kpl-proxy: unknown path')
      return
    }

    const headers = {
      'User-Agent': 'okhttp/3.12.1',
      'Accept': '*/*',
      'Accept-Language': 'zh-CN,zh;q=0.9',
    }

    let upstreamReq = null
    let timer = null
    let done = false

    const settle = () => {
      if (done) return
      done = true
      if (timer) clearTimeout(timer)
    }

    // 唯一出口：所有失败路径都经过这里，保证只响应一次 + 一定销毁上游连接。
    const fail = (e) => {
      if (done) return
      settle()
      if (upstreamReq && !upstreamReq.destroyed) upstreamReq.destroy()
      const msg = 'upstream error: ' + (e && e.message ? e.message : String(e))
      if (!res.headersSent) {
        res.writeHead(502, {
          'Access-Control-Allow-Origin': '*',
          'Content-Type': 'text/plain; charset=utf-8',
          'Cache-Control': 'no-store',
        })
        res.end(msg)
      } else {
        // 响应头已透传，状态码改不了；直接断开，别让客户端等到平台 10s 超时。
        res.destroy()
      }
    }

    // 一个请求一个总超时，覆盖「POST 收 body + 等响应头 + 读响应体」整条链。
    timer = setTimeout(() => fail(new Error('upstream timeout after ' + timeoutMs + 'ms')), timeoutMs)
    if (typeof timer.unref === 'function') timer.unref()

    const forward = (upstream) => {
      if (done) { upstream.destroy(); return }
      const out = {
        'Access-Control-Allow-Origin': '*',
        'Content-Type': upstream.headers['content-type'] || 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
      }
      if (upstream.headers['content-encoding']) out['Content-Encoding'] = upstream.headers['content-encoding']
      res.writeHead(upstream.statusCode || 502, out)
      res.on('finish', settle)
      upstream.on('error', fail)
      upstream.pipe(res)
    }

    // 客户端提前断开 → 立刻销毁上游，别为无人接收的响应继续占用函数实例。
    res.on('close', () => {
      if (done) return
      settle()
      if (upstreamReq && !upstreamReq.destroyed) upstreamReq.destroy()
    })

    if (req.method === 'GET') {
      const q = req.url.includes('?') ? req.url.slice(pathname.length) : ''
      try {
        upstreamReq = transport.get(target + q, { headers }, forward)
        upstreamReq.on('error', fail)
      } catch (e) { fail(e) }
    } else if (req.method === 'POST') {
      headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
      const chunks = []
      req.on('data', c => chunks.push(c))
      req.on('error', fail)
      req.on('end', () => {
        if (done) return
        try {
          upstreamReq = transport.request(target, { method: 'POST', headers }, forward)
          upstreamReq.on('error', fail)
          upstreamReq.end(Buffer.concat(chunks))
        } catch (e) { fail(e) }
      })
    } else {
      settle()
      res.writeHead(405, { 'Access-Control-Allow-Origin': '*' })
      res.end('method not allowed')
    }
  })
}

function startServer({ port = Number(process.env.PORT) || 9000, ...opts } = {}) {
  const server = createServer(opts)
  server.listen(port, () => console.log('kpl-proxy listening on ' + port))
  return server
}

module.exports = { TARGETS, DEFAULT_TIMEOUT_MS, createServer, startServer }

// scf_bootstrap: `node index.js` → 只有作为主入口时才监听，require 用于本地测试不会占端口。
if (require.main === module) startServer()
