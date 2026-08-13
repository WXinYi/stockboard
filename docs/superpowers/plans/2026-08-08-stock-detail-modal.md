# 股票详情弹窗 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让看板里所有可点股票名(重仓共识表 + PlayerDetail 3 张表)通过 📈 图标打开一个股票详情弹窗,展示走势(K线/分时)、基本信息、主力资金流向与 F10 资料,数据来自东财公开 push2 接口(JSONP)。

**Architecture:** 静态站无后端,前端运行时用 `<script>` 注入方式(JSONP)直连东财公开接口绕 CORS。新增 `src/utils/eastmoney.js` 统一封装接口,新增 `src/components/StockDetailModal.vue` 弹窗;`lightweight-charts` 动态 import 懒加载。单击复制/长按开 App 的现有交互不变。

**Tech Stack:** Vue 3 `<script setup>`、Vite、lightweight-charts v4、playwright-core(验证)、Node 内置 assert(纯函数测试)。

## Global Constraints

- **静态站无后端**:所有运行时数据只能来自浏览器直连东财公开 push2 接口(JSONP),禁止引入任何需鉴权的接口。
- **价格颜色约定**:红涨 `#e74c3c`,绿跌 `#27ae60`(与 PlayerDetail 现有 `pct` 用法一致)。
- **lightweight-charts 必须动态 import**(`await import('lightweight-charts')`),不得进首屏 bundle。
- **JSONP 每请求 8s 超时 + onerror → 失败态 + 重试**,不抛未捕获异常。
- **不破坏现有交互**:StockTab 单击股票名=复制代码;PlayerDetail 单击=复制、长按=打开东财 App。
- **验证环境**(本机):`playwright-core` 在 `/Users/xywang/ionic-app/node_modules/playwright-core`;浏览器用系统 Chrome(`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`);dev server 用端口 **5199**(避开常用端口)。所有 verify 脚本放 `/tmp/pw/`,不入库。
- **工作流**:先本地验证(Playwright 断言 + `npm run build`),不直接 push。
- **禁止未经允许调用视觉模型**:验证用 console 错误断言 + DOM 断言,不用截图视觉分析。

---

### Task 1: 纯函数 `emMarket` / `secid`

**Files:**
- Create: `stockboard-app/src/utils/eastmoney.js`
- Test: `/tmp/pw/test-emmarket.mjs`

**Interfaces:**
- Produces: `emMarket(code) -> '0'|'1'|'2'|'116'`、`secid(code) -> '${market}.${code}'`。后续所有 fetch 函数与弹窗都依赖这两个签名。

- [ ] **Step 1: 写失败测试**

```js
// /tmp/pw/test-emmarket.mjs
import { strict as assert } from 'node:assert'
import { emMarket, secid } from '/Users/xywang/stockboard/stockboard-app/src/utils/eastmoney.js'
assert.equal(emMarket('000938'), '0')        // 深市主板/创业板
assert.equal(emMarket('300059'), '0')        // 创业板(深)
assert.equal(emMarket('600000'), '1')        // 沪市主板
assert.equal(emMarket('688001'), '1')        // 科创板(沪)
assert.equal(emMarket('832000'), '2')        // 北交所(8 开头)
assert.equal(emMarket('920001'), '2')        // 北交所(92 开头)
assert.equal(emMarket('01810'), '116')       // 港股(5 位)
assert.equal(emMarket(''), '0')              // 兜底
assert.equal(secid('000938'), '0.000938')
assert.equal(secid('01810'), '116.01810')
console.log('✅ emMarket/secid 测试通过')
```

- [ ] **Step 2: 运行验证失败**

Run: `node /tmp/pw/test-emmarket.mjs`
Expected: `Cannot find module .../utils/eastmoney.js`

- [ ] **Step 3: 实现**

```js
// stockboard-app/src/utils/eastmoney.js
// 东财 secid 市场标识: 0=深市 1=沪市 2=北交所 116=港股
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
```

- [ ] **Step 4: 运行验证通过**

Run: `node /tmp/pw/test-emmarket.mjs`
Expected: `✅ emMarket/secid 测试通过`

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/utils/eastmoney.js && git commit -m "feat: 新增 eastmoney 工具(emMarket/secid 市场判定)"
```

---

### Task 2: `jsonp` 助手 + 全接口探针(确认字段口径)

**Files:**
- Modify: `stockboard-app/src/utils/eastmoney.js`(追加 `jsonp`)
- Create: `/tmp/pw/probe-eastmoney.mjs`、`/tmp/pw/harness.mjs`
- Test: `/tmp/pw/probe-eastmoney.mjs`

**Interfaces:**
- Produces: `jsonp(url, cbParam='cb', timeout=8000) -> Promise<any>`,成功 resolve JSONP 数据,失败 reject Error。Task 3-4 的 fetch 函数与 Task 6 弹窗都依赖它。
- 探针把 5 个接口的**原始返回结构**写到 `/tmp/pw/probe-output.json`,是 Task 3/4 实现 fetch 函数时字段映射的唯一事实来源。

- [ ] **Step 1: 实现 `jsonp`**

```js
// 追加到 eastmoney.js
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
```

- [ ] **Step 2: 写共享测试基座**

```js
// /tmp/pw/harness.mjs
import { spawn } from 'node:child_process'
import { chromium } from '/Users/xywang/ionic-app/node_modules/playwright-core/index.mjs'
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const APP = '/Users/xywang/stockboard/stockboard-app'
const PORT = 5199
export async function withDevServer(run) {
  const server = spawn('npm', ['run', 'dev', '--', '--port', String(PORT)], { cwd: APP, stdio: 'ignore' })
  const url = `http://localhost:${PORT}/`
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(url); if (r.ok) break } catch {}
    await new Promise(r => setTimeout(r, 500))
  }
  const browser = await chromium.launch({ executablePath: CHROME })
  const page = await browser.newPage()
  const consoleErrs = []
  page.on('console', m => { if (m.type() === 'error') consoleErrs.push(m.text()) })
  try { await run(page, url) } finally { await browser.close(); server.kill() }
  if (consoleErrs.length) { console.log('⚠️ console errors:', consoleErrs); process.exitCode = 1 }
}
```

- [ ] **Step 3: 写探针脚本(浏览器内直连 5 个接口)**

```js
// /tmp/pw/probe-eastmoney.mjs
import { writeFileSync } from 'node:fs'
import { withDevServer } from './harness.mjs'
withDevServer(async (page) => {
  const out = await page.evaluate(async () => {
    const cb = (url) => new Promise((res, rej) => {
      const n = '_p' + Date.now() + Math.random().toString(36).slice(2, 6)
      const s = document.createElement('script')
      window[n] = (d) => { delete window[n]; s.remove(); res(d) }
      s.onerror = () => rej(new Error('load failed: ' + url))
      s.src = url + '&cb=' + n
      document.head.appendChild(s)
    })
    const S = '0.000938'  // 紫光股份(深市)
    const q = await cb(`https://push2.eastmoney.com/api/qt/stock/get?secid=${S}&fields=f57,f58,f43,f44,f45,f46,f47,f48,f60,f168,f116,f117,f162,f167,f169,f170`)
    const k = await cb(`https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=${S}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&lmt=3&end=20500101`)
    const t = await cb(`https://push2his.eastmoney.com/api/qt/stock/trends2/get?secid=${S}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1`)
    const f = await cb(`https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid=${S}&lmt=5&klt=1&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63`)
    const f10 = await cb(`https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO&columns=ALL&filter=(SECUCODE%3D%22${S.split('.')[0]}.SZ%22)`)
    return { quote: q.data, kline: k.data?.klines, trend: t.data?.trends?.slice(0, 2), flow: f.data?.klines, f10: f10.result?.[0] }
  })
  writeFileSync('/tmp/pw/probe-output.json', JSON.stringify(out, null, 2))
  console.log('探针已写入 /tmp/pw/probe-output.json')
  console.log('quote:', JSON.stringify(out.quote))
  console.log('kline[0]:', out.kline?.[0])
  console.log('trend[0]:', out.trend?.[0])
  console.log('flow[0]:', out.flow?.[0])
  console.log('f10 keys:', out.f10 ? Object.keys(out.f10).slice(0, 30) : '❌ F10 无结果')
})
```

- [ ] **Step 4: 运行探针,记录事实**

Run: `node /tmp/pw/probe-eastmoney.mjs`
Expected: 5 个接口都返回数据;`/tmp/pw/probe-output.json` 生成。**核对以下事实并记下**(后续实现依据):
1. `quote.data` 中 `f43`(现价)相对真实价(紫光股份约 3.2 元)是否 ÷100 得到(若 f43≈320 则需 ÷100)。
2. `kline[0]` 形如 `"2026-08-07,3.20,3.21,3.23,3.18,123456,987654321"`(date,open,close,high,low,vol,amount)。
3. `trend[0]` 各字段顺序(f51=时间,f53=价,f58=均价)。
4. `flow[0]` 各字段顺序(核心: 哪个位置是主力净流入=超大+大单)。
5. `f10` 是否返回(result 数组),JSONP 参数 `cb` 是否生效;返回字段里公司简介/主营/行业各是哪个键。
6. 浏览器 console 无 CORS 报错。

若某项不符预期(如 F10 需要 `callback=` 参数、字段顺序不同),**修改探针重跑**,以真实响应为准。

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/utils/eastmoney.js && git commit -m "feat: eastmoney jsonp 助手"
```

---

### Task 3: `fetchQuote` + `fetchKline`

**Files:**
- Modify: `stockboard-app/src/utils/eastmoney.js`
- Test: `/tmp/pw/verify-quote.mjs`

**Interfaces:**
- Consumes: `jsonp`、`secid`(Task 1/2)。
- Produces:
  - `fetchQuote(code) -> Quote|null`;`Quote = { code, name, price, high, low, open, prevClose, change, changePct, volume, amount, turnoverRate, marketCap, floatCap, pe, pb }`(价格已按 Task 2 探针确认的缩放系数换算,单位: 元/手/元/%)。
  - `fetchKline(code, klt='101', lmt=120) -> Array<{ date, open, close, high, low, volume, amount }>|null`,klt: `'101'`日/`'102'`周/`'103'`月。
  - Task 6 弹窗首屏 = `fetchQuote` + `fetchKline('101')`。

- [ ] **Step 1: 写失败验证(先确认缩放正确)**

```js
// /tmp/pw/verify-quote.mjs
import { withDevServer } from './harness.mjs'
withDevServer(async (page, url) => {
  await page.goto(url)
  const out = await page.evaluate(async () => {
    const m = await import('/src/utils/eastmoney.js')
    const q = await m.fetchQuote('000938')
    const k = await m.fetchKline('000938', '101', 3)
    return { q, k }
  })
  console.log('quote:', JSON.stringify(out.q))
  console.log('kline:', JSON.stringify(out.k))
  const lastClose = out.k?.at(-1)?.close
  const price = out.q?.price
  if (!out.q?.name || !lastClose || !price) { console.error('❌ 数据为空'); process.exit(1) }
  const ratio = price / lastClose
  console.log('price/收盘 ratio:', ratio?.toFixed(3))
  if (!(ratio > 0.9 && ratio < 1.1)) { console.error('❌ 缩放不符,需调整 scale'); process.exit(1) }
  console.log('✅ quote 与 kline 收盘自洽,缩放正确')
})
```

- [ ] **Step 2: 运行确认失败**

Run: `node /tmp/pw/verify-quote.mjs`
Expected: `Error: Cannot find module .../eastmoney.js`(fetch 函数未定义)或进程报错。

- [ ] **Step 3: 实现(缩放系数按 Task 2 探针事实定)**

```js
// 追加到 eastmoney.js
const PUSH2 = 'https://push2.eastmoney.com/api/qt/stock/get'
const PUSH2HIS_K = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'

export async function fetchQuote(code) {
  const url = `${PUSH2}?secid=${secid(code)}&fields=f57,f58,f43,f44,f45,f46,f47,f48,f60,f168,f116,f117,f162,f167,f169,f170`
  const d = await jsonp(url)
  const q = d?.data
  if (!q) return null
  // 缩放: A 股价格类字段 ×100(以 Task 2 探针确认值为准,若港股不同则按市场分支处理)
  const s2 = (v) => (v == null ? null : v / 100)
  return {
    code: String(q.f57 ?? code),
    name: q.f58,
    price: s2(q.f43), high: s2(q.f44), low: s2(q.f45), open: s2(q.f46), prevClose: s2(q.f60),
    change: s2(q.f169), changePct: q.f170 ?? null,
    volume: q.f47, amount: q.f48, turnoverRate: q.f168,
    marketCap: q.f116, floatCap: q.f117, pe: s2(q.f162), pb: s2(q.f167),
  }
}

export async function fetchKline(code, klt = '101', lmt = 120) {
  const url = `${PUSH2HIS_K}?secid=${secid(code)}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&klt=${klt}&fqt=1&end=20500101&lmt=${lmt}`
  const d = await jsonp(url)
  const klines = d?.data?.klines
  if (!klines) return null
  return klines.map(k => {
    const [date, open, close, high, low, volume, amount] = k.split(',')
    return { date, open: +open, close: +close, high: +high, low: +low, volume: +volume, amount: +amount }
  })
}
```

- [ ] **Step 4: 运行确认通过**

Run: `node /tmp/pw/verify-quote.mjs`
Expected: `✅ quote 与 kline 收盘自洽,缩放正确`,且无 console error。

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/utils/eastmoney.js && git commit -m "feat: fetchQuote/fetchKline 实时行情与K线"
```

---

### Task 4: `fetchTrend` + `fetchFundFlow` + `fetchF10`

**Files:**
- Modify: `stockboard-app/src/utils/eastmoney.js`
- Test: `/tmp/pw/verify-more.mjs`

**Interfaces:**
- Consumes: `jsonp`、`secid`、`emMarket`、Task 2 探针确认的字段顺序。
- Produces:
  - `fetchTrend(code) -> Array<{ time, open, price, high, low, volume, amount, avg }>|null`(当日分时,price 为现价,avg 为均价)。
  - `fetchFundFlow(code, lmt=5) -> Array<{ date, main, small, mid, large, super, mainPct, close, chgPct }>|null`(近5日;main 主力净流入=超大+大单,验证用)。
  - `secuCode(code) -> '000938.SZ'|...`(市场后缀)。
  - `fetchF10(code) -> { intro, business, industry, concepts }|null`(简介/主营/行业/概念)。
  - Task 6 弹窗"分时/资金/资料"Tab 依赖。

- [ ] **Step 1: 写失败验证**

```js
// /tmp/pw/verify-more.mjs
import { withDevServer } from './harness.mjs'
withDevServer(async (page, url) => {
  await page.goto(url)
  const out = await page.evaluate(async () => {
    const m = await import('/src/utils/eastmoney.js')
    const t = await m.fetchTrend('000938')
    const f = await m.fetchFundFlow('000938')
    const f10 = await m.fetchF10('000938')
    return { t: t?.slice(-2), f, f10 }
  })
  console.log('trend[-2:]:', JSON.stringify(out.t))
  console.log('flow:', JSON.stringify(out.f))
  console.log('f10:', JSON.stringify(out.f10))
  let ok = true
  if (!out.t?.length || out.t[0].price == null) { console.error('❌ trend 空/缺价'); ok = false }
  if (!out.f?.length) { console.error('❌ flow 空'); ok = false }
  else {
    const row = out.f[0]
    const diff = Math.abs(row.main - (row.super + row.large)) / Math.max(1, Math.abs(row.main))
    if (diff > 0.05) { console.error(`❌ 主力≠超大+大单 (main=${row.main}, super+large=${row.super + row.large})`); ok = false }
  }
  if (!out.f10?.intro && !out.f10?.business && !out.f10?.industry) { console.warn('⚠️ F10 数据空,见 Task 4 Step 4 回退') }
  console.log(ok ? '✅ trend/flow 验证通过' : '❌ 验证失败')
  if (!ok) process.exit(1)
})
```

- [ ] **Step 2: 运行确认失败**

Run: `node /tmp/pw/verify-more.mjs`
Expected: `Cannot find module` 或报错(fetch 函数未定义)。

- [ ] **Step 3: 实现(字段顺序以 Task 2 探针输出为准,下面注释标注已知映射)**

```js
// 追加到 eastmoney.js
const TRENDS = 'https://push2his.eastmoney.com/api/qt/stock/trends2/get'
const FFLOW = 'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get'
const DATACENTER = 'https://datacenter-web.eastmoney.com/api/data/v1/get'

export async function fetchTrend(code) {
  const url = `${TRENDS}?secid=${secid(code)}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1`
  const d = await jsonp(url)
  const trends = d?.data?.trends
  if (!trends) return null
  // f51时间 f52开 f53价 f54高 f55低 f56量 f57额 f58均价
  return trends.map(t => {
    const [time, open, price, high, low, volume, amount, avg] = t.split(',')
    return { time, open: +open, price: +price, high: +high, low: +low, volume: +volume, amount: +amount, avg: +avg }
  })
}

export async function fetchFundFlow(code, lmt = 5) {
  const url = `${FFLOW}?secid=${secid(code)}&lmt=${lmt}&klt=1&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63`
  const d = await jsonp(url)
  const klines = d?.data?.klines
  if (!klines) return null
  // f51日期 f52主力 f53小单 f54中单 f55大单 f56超大单 f57主力占比 f62收盘 f63涨跌幅(以探针核对)
  return klines.map(k => {
    const [date, main, small, mid, large, sup, mainPct, , , , , close, chgPct] = k.split(',')
    return { date, main: +main, small: +small, mid: +mid, large: +large, super: +sup, mainPct: +mainPct, close: +close, chgPct: +chgPct }
  })
}

export function secuCode(code) {
  const m = emMarket(code)
  const sfx = m === '0' ? 'SZ' : m === '1' ? 'SH' : m === '2' ? 'BJ' : 'HK'
  return `${code}.${sfx}`
}

export async function fetchF10(code) {
  const sc = secuCode(code)
  const url = `${DATACENTER}?reportName=RPT_F10_BASIC_ORGINFO&columns=ALL&filter=(SECUCODE%3D%22${sc}%22)`
  const d = await jsonp(url, 'cb')   // 若探针显示 F10 需 callback= 参数,则第二参改 'callback'
  const r = d?.result?.[0]
  if (!r) return null
  // 字段键名以 Task 2 探针的 f10 keys 输出为准(常见: BUSINESS_SCOPE 主营 / 简介 / 行业)
  return { intro: r.PROFILE ?? r.INTRO, business: r.BUSINESS_SCOPE, industry: r.INDUSTRY, concepts: [] }
}
```

- [ ] **Step 4: 运行;F10 失败则回退**

Run: `node /tmp/pw/verify-more.mjs`
- Expected: trend/flow 通过(F10 可空)。
- **若 F10 接口 JSONP 不生效**(datacenter-web 不支持 `cb=` 或 CORS 拦截):按设计回退——`fetchF10` 改从另一个公开口取(如 `https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/CoreConceptionAjax?code=SZ000938` 加 `cb=`),或在 verify 里临时用页面 fetch 探测可用端点;**仍不通则保持 `fetchF10` 返回 null**,弹窗"资料"Tab 显示"暂不支持",不阻塞其余功能。

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/utils/eastmoney.js && git commit -m "feat: fetchTrend/fetchFundFlow/fetchF10 分时·资金·资料"
```

---

### Task 5: 深链函数迁入 utils + PlayerDetail 重构

**Files:**
- Modify: `stockboard-app/src/utils/eastmoney.js`、`stockboard-app/src/components/PlayerDetail.vue`

**Interfaces:**
- Consumes: 现有 PlayerDetail 第 17-78 行的深链实现(照搬)。
- Produces: `emNativeUrl(code)`、`emUniversalUrl(code)`、`openEmApp(code)`(自 utils 导出),Task 6 弹窗"打开东方财富"按钮用。
- 玩家详情内删掉本地的 `emMarket/emNativeUrl/emUniversalUrl/openEmApp/emFallbackTimer/onEmVisibility/onPressStart/onPressEnd/onPressCancel/onClickCopy` 之外的深链部分,保留长按逻辑并改为 `import { openEmApp } from '../utils/eastmoney.js'`。

- [ ] **Step 1: 迁入 utils**

把 PlayerDetail 现有以下逻辑**原样**追加到 `eastmoney.js`:`emNativeUrl`、`emUniversalUrl`、`openEmApp`(含 `emFallbackTimer`/`onEmVisibility` 内部变量与函数)。

```js
// 追加到 eastmoney.js —— 深链(原 PlayerDetail,搬移共用)
export function emNativeUrl(code) {
  const m = emMarket(code)
  if (/iPhone|iPad|iPod/i.test(navigator.userAgent)) return `eastmoney://page/stockpage?market=${m}&code=${code}`
  if (/Android/i.test(navigator.userAgent)) return `dfcft://router/market/stock?anchorKey=STOCK_BAR&market=${m}&stockCode=${code}`
  return null
}
export function emUniversalUrl(code) {
  return `https://emh5wap.eastmoney.com/h52n/CommScheme?linktype=818&sharetype=1&market=${emMarket(code)}&stockCode=${code}`
}
let emFallbackTimer = null
function onEmVisibility() { if (document.hidden) clearTimeout(emFallbackTimer) }
export function openEmApp(code) {
  const native = emNativeUrl(code)
  const universal = emUniversalUrl(code)
  if (!native) { window.location.href = universal; return }
  clearTimeout(emFallbackTimer)
  document.addEventListener('visibilitychange', onEmVisibility)
  emFallbackTimer = setTimeout(() => { if (!document.hidden) window.location.href = universal }, 2000)
  window.location.href = native
}
```

- [ ] **Step 2: PlayerDetail 改为 import**

改 PlayerDetail.vue:
- 顶部 `import { openEmApp } from '../utils/eastmoney.js'`。
- 删除本地 `emMarket/emNativeUrl/emUniversalUrl/emFallbackTimer/onEmVisibility/openEmApp`(第 16-55 行区域)。
- 保留 `lpTimer/lpFired/onPressStart/onPressEnd/onPressCancel/onClickCopy`,它们现在调用 `openEmApp`。

- [ ] **Step 3: 验证不破坏**

Run: `node /tmp/pw/verify-quote.mjs`(仍通过,证明 utils 模块在浏览器可加载)。
Run: `cd /Users/xywang/stockboard/stockboard-app && npm run build`
Expected: 构建通过;PlayerDetail 无未定义引用。

- [ ] **Step 4: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/utils/eastmoney.js stockboard-app/src/components/PlayerDetail.vue && git commit -m "refactor: 深链函数迁入 eastmoney utils, PlayerDetail 复用"
```

---

### Task 6: StockDetailModal 组件(含 lightweight-charts 懒加载)

**Files:**
- Create: `stockboard-app/src/components/StockDetailModal.vue`
- Modify: `stockboard-app/package.json`(安装 lightweight-charts)
- Test: `/tmp/pw/verify-modal.mjs`

**Interfaces:**
- Consumes: `fetchQuote/fetchKline/fetchTrend/fetchFundFlow/fetchF10/openEmApp`(utils)、`useCopyCode` 组合式函数。
- Produces: 组件 `<StockDetailModal v-model="visible" :code="code" :name="name" />`;props `code:String`(必填)、`name:String`(默认''),v-model 控制显隐。Task 7/8 在两个页面引入。

- [ ] **Step 1: 安装依赖**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm install lightweight-charts
```

- [ ] **Step 2: 写失败验证(先弹窗能开、数据能灌进组件)**

```js
// /tmp/pw/verify-modal.mjs
import { withDevServer } from './harness.mjs'
withDevServer(async (page, url) => {
  await page.goto(url)
  await page.evaluate(async () => { await import('/src/main.js') })  // 挂载 App
  await page.waitForSelector('.rank-row, .search-box', { timeout: 20000 })
  // 通过 StockTab 的 📈 触发(先保证 StockTab 已加图标;若本任务先于 Task7,则改为直接挂载测试页)
  const opened = await page.evaluate(async () => {
    const m = await import('/src/utils/eastmoney.js')
    const q = await m.fetchQuote('000938')
    const k = await m.fetchKline('000938', '101', 30)
    return { name: q?.name, klen: k?.length, last: k?.at(-1)?.close }
  })
  console.log('数据自检:', JSON.stringify(opened))
  if (!opened.name || !opened.klen) { console.error('❌ 数据不可用'); process.exit(1) }
})
```

> 说明: 弹窗本体是一个组件,纯逻辑单元测试意义有限;本任务把"数据层可用 + 组件构建通过"作为验证门槛,Task 9 再做完整 E2E(点击 📈 → 弹窗 → 各 Tab 渲染)。

- [ ] **Step 3: 实现组件(核心交付)**

```vue
<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { useCopyCode } from '../composables/useCopyCode.js'
import {
  fetchQuote, fetchKline, fetchTrend, fetchFundFlow, fetchF10, openEmApp,
} from '../utils/eastmoney.js'

const props = defineProps({
  code: { type: String, required: true },
  name: { type: String, default: '' },
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])
const { copiedKey, copyStockCode } = useCopyCode()

const tab = ref('走势')
const klt = ref('101')
const tabs = ['走势', '分时', '资金', '资料']
const kltLabel = { '101': '日K', '102': '周K', '103': '月K' }

const quote = ref(null)
const candles = ref([])
const trend = ref([])
const flow = ref([])
const f10 = ref(null)
const loading = ref({ quote: false, kline: false, trend: false, flow: false, f10: false })
const error = ref('')
const chartEl = ref(null)

let chart = null, candleSeries = null, volSeries = null, maSeries = []
const fmt = new Intl.NumberFormat('zh-CN')

function close() { emit('update:modelValue', false) }
function setError(msg) { error.value = msg || '' }
function gb(v) { return v == null ? '—' : v >= 0 ? '#e74c3c' : '#27ae60' }
function chgPct(v) { return v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%` }

async function loadQuote() {
  loading.value.quote = true; setError('')
  try { quote.value = await fetchQuote(props.code) } catch (e) { setError(e.message) } finally { loading.value.quote = false }
}
async function loadKline() {
  loading.value.kline = true
  try { candles.value = (await fetchKline(props.code, klt.value)) || [] } catch (e) { setError(e.message) }
  finally { loading.value.kline = false }
  await nextTick(); drawChart()
}
async function loadTrend() {
  if (trend.value.length) return
  loading.value.trend = true
  try { trend.value = (await fetchTrend(props.code)) || [] } catch (e) { setError(e.message) } finally { loading.value.trend = false }
}
async function loadFlow() {
  if (flow.value.length) return
  loading.value.flow = true
  try { flow.value = (await fetchFundFlow(props.code)) || [] } catch (e) { setError(e.message) } finally { loading.value.flow = false }
}
async function loadF10() {
  if (f10.value) return
  loading.value.f10 = true
  try { f10.value = await fetchF10(props.code) } catch (e) { setError(e.message) } finally { loading.value.f10 = false }
}

// ── lightweight-charts(懒加载)──
function ma(values, n) {
  const out = []; let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= n) sum -= values[i - n]
    out.push({ time: values[i].date, value: +(sum / Math.min(i + 1, n)).toFixed(3) })
  }
  return out
}
async function drawChart() {
  const el = chartEl.value
  if (!el || !candles.value.length) return
  if (!chart) {
    const { createChart } = await import('lightweight-charts')
    chart = createChart(el, {
      width: el.clientWidth, height: 260,
      layout: { background: { type: 'solid', color: '#fff' }, textColor: '#888', fontSize: 10 },
      grid: { vertLines: { color: 'rgba(0,0,0,.04)' }, horzLines: { color: 'rgba(0,0,0,.04)' } },
      timeScale: { borderColor: 'rgba(0,0,0,.06)' },
      rightPriceScale: { borderColor: 'rgba(0,0,0,.06)' },
    })
    candleSeries = chart.addCandlestickSeries({ upColor: '#e74c3c', downColor: '#27ae60', borderVisible: false, wickUpColor: '#e74c3c', wickDownColor: '#27ae60' })
    volSeries = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol' })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } })
  }
  const data = candles.value
  candleSeries.setData(data.map(c => ({ time: c.date, open: c.open, high: c.high, low: c.low, close: c.close })))
  const maxV = Math.max(...data.map(c => c.volume), 1)
  volSeries.setData(data.map(c => ({ time: c.date, value: c.volume, color: c.close >= c.open ? 'rgba(231,76,60,.4)' : 'rgba(39,174,96,.4)' })))
  maSeries.forEach(s => chart.removeSeries(s)); maSeries = []
  const closes = data.map(c => ({ date: c.date, close: c.close }))
  for (const n of [5, 10, 20]) {
    maSeries.push(chart.addLineSeries({ color: n === 5 ? '#f39c12' : n === 10 ? '#3498db' : '#9b59b6', lineWidth: 1, priceLineVisible: false, lastValueVisible: false }))
    maSeries.at(-1).setData(ma(closes, n))
  }
  chart.timeScale().fitContent()
}
function onResize() { if (chart && chartEl.value) chart.applyOptions({ width: chartEl.value.clientWidth }) }

watch(() => props.modelValue, (v) => {
  if (!v) return
  tab.value = '走势'; klt.value = '101'; error.value = ''
  loadQuote(); loadKline()
})
watch(() => props.code, () => { if (props.modelValue) { candles.value = []; trend.value = []; flow.value = []; f10.value = null; loadQuote(); loadKline() } })
watch(klt, loadKline)
watch(tab, (t) => { if (t === '分时') loadTrend(); if (t === '资金') loadFlow(); if (t === '资料') loadF10() })
onBeforeUnmount(() => { chart?.remove(); chart = null })
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="sdm-mask" @click.self="close">
      <div class="sdm-panel">
        <div class="sdm-head">
          <div class="sdm-title">
            <strong>{{ quote?.name || name || code }}</strong>
            <span class="sdm-code">{{ code }}</span>
            <span v-if="quote" class="sdm-price" :style="{ color: gb(quote.change) }">{{ quote.price?.toFixed(2) }}</span>
            <span v-if="quote" class="sdm-chg" :style="{ color: gb(quote.change) }">{{ chgPct(quote.changePct) }}</span>
          </div>
          <button class="sdm-close" @click="close">×</button>
        </div>
        <div v-if="error" class="sdm-err">{{ error }} <button @click="loadQuote()">重试</button></div>
        <div class="sdm-strip" v-if="quote">
          <span>今开 <b>{{ quote.open?.toFixed(2) }}</b></span>
          <span>昨收 <b>{{ quote.prevClose?.toFixed(2) }}</b></span>
          <span>最高 <b>{{ quote.high?.toFixed(2) }}</b></span>
          <span>最低 <b>{{ quote.low?.toFixed(2) }}</b></span>
          <span>成交量 <b>{{ quote.volume }}手</b></span>
          <span>成交额 <b>{{ quote.amount != null ? (quote.amount / 1e8).toFixed(2) + '亿' : '—' }}</b></span>
          <span>换手 <b>{{ quote.turnoverRate }}%</b></span>
          <span>PE <b>{{ quote.pe?.toFixed(2) }}</b></span>
          <span>PB <b>{{ quote.pb?.toFixed(2) }}</b></span>
          <span>总市值 <b>{{ quote.marketCap != null ? (quote.marketCap / 1e8).toFixed(1) + '亿' : '—' }}</b></span>
          <span>流通 <b>{{ quote.floatCap != null ? (quote.floatCap / 1e8).toFixed(1) + '亿' : '—' }}</b></span>
        </div>
        <div class="sdm-tabs">
          <button v-for="t in tabs" :key="t" :class="['sdm-tab', { on: tab === t }]" @click="tab = t">{{ t }}</button>
          <span v-if="tab === '走势'" class="sdm-klt">
            <button v-for="(lab, key) in kltLabel" :key="key" :class="{ on: klt === key }" @click="klt = key">{{ lab }}</button>
          </span>
        </div>
        <div class="sdm-body">
          <div v-show="tab === '走势'" ref="chartEl" class="sdm-chart" :class="{ dim: loading.kline }"></div>
          <div v-show="tab === '分时'" class="sdm-pane">
            <div v-if="loading.trend" class="sdm-loading">加载分时…</div>
            <div v-else-if="trend.length" class="sdm-trendlist">
              <div v-for="t in trend.slice(-10).reverse()" :key="t.time" class="sdm-trendrow">
                <span>{{ t.time.slice(11) }}</span><span :style="{ color: gb(t.price - (quote?.prevClose ?? t.open)) }">{{ t.price.toFixed(2) }}</span><span class="dim">均价 {{ t.avg.toFixed(2) }}</span>
              </div>
            </div>
            <div v-else class="sdm-loading">分时数据暂无</div>
          </div>
          <div v-show="tab === '资金'" class="sdm-pane">
            <div v-if="loading.flow" class="sdm-loading">加载资金流向…</div>
            <table v-else-if="flow.length" class="sdm-table">
              <thead><tr><th>日期</th><th>主力净流入</th><th>超大单</th><th>大单</th><th>中单</th><th>小单</th></tr></thead>
              <tbody>
                <tr v-for="r in flow" :key="r.date">
                  <td>{{ r.date }}</td>
                  <td :style="{ color: gb(r.main) }">{{ (r.main / 1e8).toFixed(2) }}亿</td>
                  <td :style="{ color: gb(r.super) }">{{ (r.super / 1e8).toFixed(2) }}亿</td>
                  <td :style="{ color: gb(r.large) }">{{ (r.large / 1e8).toFixed(2) }}亿</td>
                  <td :style="{ color: gb(r.mid) }">{{ (r.mid / 1e8).toFixed(2) }}亿</td>
                  <td :style="{ color: gb(r.small) }">{{ (r.small / 1e8).toFixed(2) }}亿</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="sdm-loading">资金流向暂无</div>
          </div>
          <div v-show="tab === '资料'" class="sdm-pane">
            <div v-if="loading.f10" class="sdm-loading">加载公司资料…</div>
            <div v-else-if="f10" class="sdm-f10">
              <p v-if="f10.intro"><b>简介</b><br>{{ f10.intro }}</p>
              <p v-if="f10.business"><b>主营业务</b><br>{{ f10.business }}</p>
              <p v-if="f10.industry"><b>所属行业</b> {{ f10.industry }}</p>
            </div>
            <div v-else class="sdm-loading">公司资料暂不支持</div>
          </div>
        </div>
        <div class="sdm-foot">
          <button class="sdm-btn" @click="copyStockCode(code, code)">复制代码{{ copiedKey === code ? ' ✓' : '' }}</button>
          <button class="sdm-btn primary" @click="openEmApp(code)">打开东方财富</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.sdm-mask { position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 999; display: flex; align-items: center; justify-content: center; }
.sdm-panel { width: min(640px, 94vw); max-height: 88vh; overflow-y: auto; background: #fff; border-radius: 14px; display: flex; flex-direction: column; }
.sdm-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 16px 8px; }
.sdm-title { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.sdm-code { color: #999; font-size: 12px; }
.sdm-price { font-size: 22px; font-weight: 600; }
.sdm-chg { font-size: 14px; }
.sdm-close { border: none; background: none; font-size: 22px; color: #888; cursor: pointer; padding: 0 4px; }
.sdm-err { color: #e74c3c; font-size: 12px; padding: 0 16px 6px; }
.sdm-err button { margin-left: 8px; font-size: 12px; }
.sdm-strip { display: flex; gap: 12px; overflow-x: auto; padding: 6px 16px 10px; font-size: 11px; color: #666; white-space: nowrap; }
.sdm-strip b { color: #111; font-weight: 500; }
.sdm-tabs { display: flex; align-items: center; gap: 4px; padding: 0 16px; border-bottom: 1px solid rgba(0,0,0,.06); }
.sdm-tab { border: none; background: none; padding: 8px 10px; font-size: 13px; color: #666; cursor: pointer; }
.sdm-tab.on { color: #2980b9; font-weight: 600; box-shadow: inset 0 -2px #2980b9; }
.sdm-klt { margin-left: auto; display: flex; gap: 2px; }
.sdm-klt button { border: none; background: #f0f2f5; font-size: 11px; padding: 3px 8px; border-radius: 6px; color: #666; cursor: pointer; }
.sdm-klt button.on { background: #2980b9; color: #fff; }
.sdm-body { flex: 1; min-height: 0; }
.sdm-chart { height: 260px; }
.sdm-chart.dim { opacity: .4; }
.sdm-pane { padding: 12px 16px; min-height: 200px; }
.sdm-loading { color: #999; font-size: 12px; text-align: center; padding: 40px 0; }
.sdm-trendlist { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.sdm-trendrow { display: flex; gap: 12px; }
.sdm-trendrow .dim { color: #999; }
.sdm-table { width: 100%; font-size: 11px; border-collapse: collapse; }
.sdm-table th, .sdm-table td { padding: 5px 4px; text-align: right; border-bottom: 1px solid rgba(0,0,0,.04); }
.sdm-table th { color: #888; font-weight: 500; }
.sdm-table td:first-child, .sdm-table th:first-child { text-align: left; }
.sdm-f10 { font-size: 12px; color: #333; line-height: 1.6; }
.sdm-f10 p { margin: 0 0 10px; }
.sdm-f10 b { color: #111; }
.sdm-foot { display: flex; gap: 10px; padding: 12px 16px; border-top: 1px solid rgba(0,0,0,.06); }
.sdm-btn { flex: 1; padding: 10px 0; border: 1px solid #2980b9; background: none; color: #2980b9; border-radius: 10px; font-size: 13px; cursor: pointer; }
.sdm-btn.primary { background: #2980b9; color: #fff; border-color: #2980b9; }
</style>
```

> 说明: 分时 Tab 暂以"最近 10 条价格行"呈现(先不画分时图,避免任务过大);若用户后续要求完整分时曲线,再扩展。

- [ ] **Step 4: 运行验证**

Run: `node /tmp/pw/verify-modal.mjs`
Expected: `✅ 数据自检` 通过,无 console error。

- [ ] **Step 5: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/package.json stockboard-app/package-lock.json stockboard-app/src/components/StockDetailModal.vue && git commit -m "feat: StockDetailModal 股票详情弹窗(走势/分时/资金/资料)"
```

---

### Task 7: StockTab 接入 📈

**Files:**
- Modify: `stockboard-app/src/components/StockTab.vue`
- Test: `/tmp/pw/verify-modal.mjs`(复用)

**Interfaces:**
- Consumes: `StockDetailModal` 组件。
- Produces: StockTab 内每只股票名旁有 📈 图标,点击打开弹窗。

- [ ] **Step 1: 引入组件 + 状态**

在 `StockTab.vue` `<script setup>` 加入:
```js
import StockDetailModal from './StockDetailModal.vue'
const stockModal = ref({ visible: false, code: '', name: '' })
function openStockDetail(c, n) { stockModal.value = { visible: true, code: c, name: n } }
```

- [ ] **Step 2: 模板加图标 + 弹窗**

重仓共识表格的股票单元格(第 78 行附近)改为:
```html
<td>
  <strong class="stock-name" @click="copyStockCode(s.c)" :title="'点击复制代码 ' + s.c">{{ s.n }}</strong>
  <button class="stock-icon" @click.stop="openStockDetail(s.c, s.n)" title="查看走势详情">📈</button>
  <span v-if="copiedKey === s.c" class="copied-tip">✓ 已复制</span>
</td>
```
并在模板末尾(根元素内)加:
```html
<StockDetailModal v-model="stockModal.visible" :code="stockModal.code" :name="stockModal.name" />
```
CSS 加:
```css
.stock-icon { border: none; background: none; cursor: pointer; font-size: 13px; padding: 0 4px; vertical-align: middle; }
```

- [ ] **Step 3: 验证**

Run: `node /tmp/pw/verify-modal.mjs`(无回归)。
Run: `cd /Users/xywang/stockboard/stockboard-app && npm run build`(通过)。

- [ ] **Step 4: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/components/StockTab.vue && git commit -m "feat: StockTab 重仓共识加 📈 打开股票详情"
```

---

### Task 8: PlayerDetail 接入 📈(3 张表)

**Files:**
- Modify: `stockboard-app/src/components/PlayerDetail.vue`
- Test: `/tmp/pw/verify-modal.mjs`(复用)

**Interfaces:**
- Consumes: `StockDetailModal` 组件。
- Produces: 当前持仓/推测持仓/调仓记录 3 张表的股票名旁都有 📈,点击打开弹窗。

- [ ] **Step 1: 引入组件 + 状态**

`PlayerDetail.vue` `<script setup>` 加:
```js
import StockDetailModal from './StockDetailModal.vue'
const stockModal = ref({ visible: false, code: '', name: '' })
function openStockDetail(c, n) { stockModal.value = { visible: true, code: c, name: n } }
```

- [ ] **Step 2: 3 处股票单元格加图标 + 弹窗**

当前持仓(第 161 行)、推测持仓(第 186 行)、调仓记录(第 218 行)的 `<strong class="stock-name">` 后各加:
```html
<button class="stock-icon" @click.stop="openStockDetail(x.sc, x.sn)" title="查看走势详情">📈</button>
```
(变量名按所在表格: `x.sc` 代码、`x.sn` 名称;推测持仓是 `s.cd`/`s.sn`。)

模板末尾加:
```html
<StockDetailModal v-model="stockModal.visible" :code="stockModal.code" :name="stockModal.name" />
```
CSS 加:
```css
.stock-icon { border: none; background: none; cursor: pointer; font-size: 13px; padding: 0 4px; vertical-align: middle; }
```

- [ ] **Step 3: 验证**

Run: `node /tmp/pw/verify-modal.mjs`(无回归)。
Run: `cd /Users/xywang/stockboard/stockboard-app && npm run build`(通过)。

- [ ] **Step 4: Commit**

```bash
cd /Users/xywang/stockboard && git add stockboard-app/src/components/PlayerDetail.vue && git commit -m "feat: PlayerDetail 3 张表加 📈 打开股票详情"
```

---

### Task 9: 完整 E2E 验证 + 构建 + 体积确认

**Files:**
- Create: `/tmp/pw/e2e-modal.mjs`
- Test: `/tmp/pw/e2e-modal.mjs`

**Interfaces:**
- Consumes: 全部已完成组件。
- Produces: 端到端可用的功能 + 验证报告。

- [ ] **Step 1: 写 E2E 脚本(点击 📈 → 弹窗 → 各 Tab)**

```js
// /tmp/pw/e2e-modal.mjs
import { withDevServer } from './harness.mjs'
withDevServer(async (page, url) => {
  await page.goto(url + '#/stocks')
  await page.waitForSelector('.stock-icon', { timeout: 30000 })
  await page.locator('.stock-icon').first().click()
  await page.waitForSelector('.sdm-panel', { timeout: 10000 })
  // 走势 Tab: K线图容器出现
  await page.waitForSelector('.sdm-chart', { timeout: 10000 })
  // 分时 Tab
  await page.locator('.sdm-tab', { hasText: '分时' }).click()
  await page.waitForTimeout(4000)
  const trendText = await page.locator('.sdm-trendlist').count()
  // 资金 Tab
  await page.locator('.sdm-tab', { hasText: '资金' }).click()
  await page.waitForTimeout(3000)
  const flowRows = await page.locator('.sdm-table tbody tr').count()
  // 资料 Tab
  await page.locator('.sdm-tab', { hasText: '资料' }).click()
  await page.waitForTimeout(3000)
  const f10Text = await page.locator('.sdm-f10, .sdm-loading').count()
  // 周期切换
  await page.locator('.sdm-klt button', { hasText: '周K' }).click()
  await page.waitForTimeout(2000)
  const chartOk = await page.locator('.sdm-chart').isVisible()
  console.log(`结果: 分时列表=${trendText} 资金行=${flowRows} 资料块=${f10Text} 周期切换chart=${chartOk}`)
  if (trendText === 0 || flowRows === 0 || !chartOk) { console.error('❌ E2E 部分失败'); process.exit(1) }
  console.log('✅ E2E 通过')
})
```

- [ ] **Step 2: 运行 E2E**

Run: `node /tmp/pw/e2e-modal.mjs`
Expected: `✅ E2E 通过`,且无 console error。

- [ ] **Step 3: 完整构建 + 确认懒加载分割**

```bash
cd /Users/xywang/stockboard/stockboard-app && npm run build
```
Expected: 构建成功。确认输出里 lightweight-charts 被单独分包(`dist/assets/*.js` 中有一个体积约 40-50KB 的 chunk,且**入口 bundle 不包含它**)。

- [ ] **Step 4: 真机冒烟(可选,用户手机)**

`npm run dev`(局域网 IP)或 build 后部署,手机浏览器点 📈 验证弹窗与长按复制互不干扰。若不便真机,以 Playwright 结果为准。

- [ ] **Step 5: Commit(若有遗留改动)**

```bash
cd /Users/xywang/stockboard && git status --short && git add -A stockboard-app/src && git commit -m "chore: 股票详情弹窗收尾"
```

---

## 风险提示(实现时留意)

- **F10(datacenter-web)JSONP 是否生效**:Task 2 探针是唯一事实来源;不通则按 Task 4 Step 4 回退(返回 null,资料 Tab 显示"暂不支持")。
- **港股缩放**:若 Task 3 验证中港股(5 位代码)价对不上,`fetchQuote` 需按 `emMarket(code)==='116'` 分支调整 scale。
- **lightweight-charts API 版本**:v4 用 `addCandlestickSeries()/addHistogramSeries()/addLineSeries()`,若装到 v5 需按官方迁移调整(装时锁定 `^4`)。

## 自检

- 规范覆盖: spec 的"数据源/范围/点击行为/图表方案/富化功能"→ Task 1-9 全映射。
- 无占位符: 每个 step 有实际命令/代码。
- 类型一致: `fetchQuote/fetchKline/fetchTrend/fetchFundFlow/fetchF10/emMarket/secid/secuCode/jsonp/openEmApp` 签名在各 Task 一致;组件 props `code/name/modelValue`、emit `update:modelValue` 跨 Task 7/8 一致。
