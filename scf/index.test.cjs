const test = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const { createServer, startServer, TARGETS, DEFAULT_TIMEOUT_MS } = require('./index.js')

function listen(server) {
  return new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve(server.address().port)))
}

function close(server) {
  return new Promise(resolve => {
    server.closeAllConnections()
    server.close(() => resolve())
  })
}

function request(port, path, opts = {}) {
  return new Promise(resolve => {
    const req = http.request({
      host: '127.0.0.1', port, path,
      method: opts.method || 'GET',
      headers: opts.headers || {},
    }, res => {
      const chunks = []
      const done = extra => resolve({
        status: res.statusCode, headers: res.headers,
        body: Buffer.concat(chunks).toString(), ...extra,
      })
      res.on('data', c => chunks.push(c))
      res.on('end', () => done({ complete: res.complete }))
      res.on('aborted', () => done({ complete: false }))
      res.on('error', e => done({ socketError: e.code || e.message }))
    })
    req.on('error', e => resolve({ socketError: e.code || e.message }))
    if (opts.body) req.write(opts.body)
    req.end()
  })
}

// 本地 http server 当上游：transport 注入 http，targets 指向 127.0.0.1，不碰真实开盘啦域名
async function setup(t, upstreamHandler, timeoutMs = 150) {
  const upstream = http.createServer(upstreamHandler)
  const upPort = await listen(upstream)
  const proxy = createServer({
    targets: { '/kpl-hq': `http://127.0.0.1:${upPort}/w1/api/index.php` },
    transport: http,
    timeoutMs,
  })
  const proxyPort = await listen(proxy)
  t.after(async () => { await close(proxy); await close(upstream) })
  return { proxyPort, upPort }
}

const wait = ms => new Promise(r => setTimeout(r, ms))

test('require 不自动监听端口，且导出可注入入口', () => {
  assert.equal(typeof createServer, 'function')
  assert.equal(typeof startServer, 'function')
  assert.equal(createServer().listening, false)
  assert.equal(Object.keys(TARGETS).length, 5)
  assert.ok(DEFAULT_TIMEOUT_MS < 10000, '上游超时必须早于 SCF 函数 10s Timeout')
})

test('未知路径 404 + CORS；不支持的方法 405', async (t) => {
  const { proxyPort } = await setup(t, () => {})
  const notFound = await request(proxyPort, '/nope')
  assert.equal(notFound.status, 404)
  assert.equal(notFound.headers['access-control-allow-origin'], '*')
  const badMethod = await request(proxyPort, '/kpl-hq', { method: 'PUT' })
  assert.equal(badMethod.status, 405)
})

test('GET 透传 query / okhttp UA / CORS / no-store / 响应体', async (t) => {
  let seen = null
  const { proxyPort } = await setup(t, (req, res) => {
    seen = { url: req.url, ua: req.headers['user-agent'] }
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end('{"ok":1}')
  })
  const r = await request(proxyPort, '/kpl-hq?a=GetStockPanKou_Narrow&StockID=600000')
  assert.equal(r.status, 200)
  assert.equal(r.body, '{"ok":1}')
  assert.equal(r.complete, true)
  assert.equal(r.headers['access-control-allow-origin'], '*')
  assert.equal(r.headers['cache-control'], 'no-store')
  assert.equal(seen.ua, 'okhttp/3.12.1')
  assert.match(seen.url, /a=GetStockPanKou_Narrow&StockID=600000/)
})

test('POST 原样转发 body 与 Content-Type', async (t) => {
  let seen = null
  const { proxyPort } = await setup(t, (req, res) => {
    const chunks = []
    req.on('data', c => chunks.push(c))
    req.on('end', () => {
      seen = { body: Buffer.concat(chunks).toString(), ct: req.headers['content-type'] }
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end('{"posted":1}')
    })
  })
  const r = await request(proxyPort, '/kpl-hq', {
    method: 'POST', body: 'a=GetHome&c=NewStock',
  })
  assert.equal(r.status, 200)
  assert.equal(r.body, '{"posted":1}')
  assert.equal(seen.body, 'a=GetHome&c=NewStock')
  assert.match(seen.ct, /application\/x-www-form-urlencoded/)
})

test('上游不返回响应头 → 7s 级总超时回 502 并销毁上游连接', async (t) => {
  let socketClosed = false
  const { proxyPort } = await setup(t, (req, res) => {
    req.socket.on('close', () => { socketClosed = true })
    // 故意不 writeHead / 不 end
  }, 60)
  const started = Date.now()
  const r = await request(proxyPort, '/kpl-hq')
  assert.equal(r.status, 502)
  assert.match(r.body, /upstream timeout after 60ms/)
  assert.ok(Date.now() - started < 1000, '不能挂到 SCF 平台超时')
  assert.equal(r.headers['access-control-allow-origin'], '*')
  await wait(30)
  assert.equal(socketClosed, true, '超时必须 destroy 上游请求')
})

test('上游发了响应头但 body 挂起 → 主动截断，不永久 pending', async (t) => {
  let socketClosed = false
  const { proxyPort } = await setup(t, (req, res) => {
    req.socket.on('close', () => { socketClosed = true })
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.write('{"partial":')   // 不 end
  }, 60)
  const started = Date.now()
  const r = await request(proxyPort, '/kpl-hq')
  assert.equal(r.status, 200, '响应头已透传，状态码改不了')
  assert.ok(r.socketError || r.complete === false, '客户端必须在超时时被断开')
  assert.notEqual(r.body, '{"partial":true}')
  assert.ok(Date.now() - started < 1000)
  await wait(30)
  assert.equal(socketClosed, true)
})

test('上游连接失败(ECONNREFUSED) → 502，不抛未捕获异常', async (t) => {
  const proxy = createServer({
    targets: { '/kpl-hq': 'http://127.0.0.1:1/w1/api/index.php' },
    transport: http, timeoutMs: 200,
  })
  const proxyPort = await listen(proxy)
  t.after(() => close(proxy))
  const r = await request(proxyPort, '/kpl-hq')
  assert.equal(r.status, 502)
  assert.match(r.body, /upstream error: /)
})

test('客户端提前断开 → 上游连接被销毁', async (t) => {
  let socketClosed = false
  const upstream = http.createServer((req, res) => {
    req.socket.on('close', () => { socketClosed = true })
  })
  const upPort = await listen(upstream)
  const proxy = createServer({
    targets: { '/kpl-hq': `http://127.0.0.1:${upPort}/w1/api/index.php` },
    transport: http, timeoutMs: 5000,
  })
  const proxyPort = await listen(proxy)
  t.after(async () => { await close(proxy); await close(upstream) })

  await new Promise(resolve => {
    const req = http.request({ host: '127.0.0.1', port: proxyPort, path: '/kpl-hq' }, () => resolve())
    req.on('error', () => resolve())
    req.end()
    setTimeout(() => req.destroy(), 20)
  })
  await wait(50)
  assert.equal(socketClosed, true)
})
