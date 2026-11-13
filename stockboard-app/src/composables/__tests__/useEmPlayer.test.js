import { describe, it, expect, vi, afterEach } from 'vitest'
import { useEmPlayer, requestBlock, BLOCKS, REASON } from '../useEmPlayer.js'

const ok = (data = { row: 1 }) => ({ ok: true, data })
const fail = (kind, code = kind) => ({ ok: false, kind, detail: { code, message: '' } })

afterEach(() => { vi.unstubAllGlobals() })

describe('useEmPlayer · emContract 硬门槛', () => {
  it('ok=false → 0 请求，三块全部 🟡 回退', async () => {
    const e = useEmPlayer()
    await e.load('u1', '900456476', { ok: false })
    expect(e.requestCount.value).toBe(0)
    for (const b of BLOCKS) {
      expect(e.blocks.value[b].state).toBe('stale')
      expect(e.blocks.value[b].reason).toBe(REASON.contract)
    }
  })

  it('缺 userId → 0 请求', async () => {
    const e = useEmPlayer()
    await e.load('', '900456476', { ok: true })
    expect(e.requestCount.value).toBe(0)
  })
})

describe('useEmPlayer · 三态与六类失败原因', () => {
  it('三块成功 → 🟢，带 HH:MM:SS 时点', async () => {
    const e = useEmPlayer()
    await e.load('u1', '900456476', { ok: true }, async () => ok())
    expect(e.requestCount.value).toBe(3)
    for (const b of BLOCKS) {
      expect(e.blocks.value[b].state).toBe('live')
      expect(e.blocks.value[b].at).toMatch(/^\d{2}:\d{2}:\d{2}$/)
    }
  })

  const cases = [
    ['9035', '缺少该选手 userId'],
    ['9061', '组合已被删除'],
    ['-1', '需登录态'],
    ['empty', '返回空数据'],
    ['network', '不可达'],
    ['400', '参数校验失败'],
  ]
  for (const [kind, text] of cases) {
    it(`${kind} → 🔴 且原因可见`, async () => {
      const e = useEmPlayer()
      await e.load('u1', '900456476', { ok: true }, async () => fail(kind))
      expect(e.blocks.value.position.state).toBe('error')
      expect(e.blocks.value.position.reason).toContain(text)
      expect(e.blocks.value.position.detail.code).toBe(kind)
    })
  }

  it('单块失败不影响其它块（独立回退）', async () => {
    const e = useEmPlayer()
    await e.load('u1', '900456476', { ok: true }, async (_u, _z, b) => b === 'yield' ? fail('9061') : ok())
    expect(e.blocks.value.yield.state).toBe('error')
    expect(e.blocks.value.position.state).toBe('live')
    expect(e.blocks.value.trades.state).toBe('live')
  })
})

describe('useEmPlayer · rtV2 协议形状（回归 400）', () => {
  it('payload 包一层，且不含 token / 自造 block 字段', async () => {
    const spy = vi.fn().mockResolvedValue({ json: async () => ({ code: 0, data: { row: 1 } }) })
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900293400', 'position')
    expect(r.ok).toBe(true)
    const body = JSON.parse(spy.mock.calls[0][1].body)
    expect(body).toMatchObject({
      method: 'CombinationHoldPositionPermitHandler',
      client: 'ios',
      clientType: 'cfw',
      args: { userId: 'u1', combinationId: '900293400' },
    })
    expect(body.args.block).toBeUndefined()
    expect(JSON.stringify(body)).not.toMatch(/ctToken|utToken|HIDDEN_UIDS/)
  })

  it('调仓带分页参数', async () => {
    const spy = vi.fn().mockResolvedValue({ json: async () => ({ code: 0, data: [{ r: 1 }] }) })
    vi.stubGlobal('fetch', spy)
    await requestBlock('u1', '900293400', 'trades')
    const body = JSON.parse(spy.mock.calls[0][1].body)
    expect(body.args).toMatchObject({ pageNum: 1, pageSize: 20 })
  })

  it('HTTP 400 读出 code/message，不误判为网络失败', async () => {
    const spy = vi.fn().mockResolvedValue({
      json: async () => ({ code: 400, message: '请传入正确的参数', data: {} }),
    })
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900293400', 'position')
    expect(r.kind).toBe('400')
    expect(r.detail.message).toContain('参数')
  })
})

describe('useEmPlayer · 限流', () => {
  it('并发不超过 3', async () => {
    let active = 0, peak = 0
    const impl = async () => {
      active++; peak = Math.max(peak, active)
      await new Promise(r => setTimeout(r, 5))
      active--
      return ok()
    }
    const e = useEmPlayer()
    await e.load('u1', '900456476', { ok: true }, impl)
    expect(peak).toBeLessThanOrEqual(3)
  })

  it('瞬时故障最多重试 1 次（合计 2 次请求）', async () => {
    const spy = vi.fn().mockResolvedValue({ ok: false, status: 503 })
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900456476', 'position')
    expect(spy).toHaveBeenCalledTimes(2)
    expect(r.kind).toBe('network')
  })

  it('-1 登录失效是确定性失败：0 重试，立即回退', async () => {
    const spy = vi.fn().mockResolvedValue({
      json: async () => ({ code: -1, message: '登录失效，请重新登录', data: {} }),
    })
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900438148', 'yield')
    expect(spy).toHaveBeenCalledTimes(1)
    expect(r.kind).toBe('-1')
    expect(r.retryable).toBe(false)
  })
})

describe('useEmPlayer · 调仓多页合并', () => {
  const tradeFetch = (totalPages = 1, failFrom = Infinity) => vi.fn().mockImplementation((_url, opts) => {
    const { method, args } = JSON.parse(opts.body)
    if (method !== 'CombinationRelocatePositionHandler') {
      return Promise.resolve({ json: async () => ({ code: 0, data: { row: 1 } }) })
    }
    const p = args.pageNum
    if (p >= failFrom) {
      return Promise.resolve({ json: async () => ({ code: 9061, message: 'gone', data: {} }) })
    }
    return Promise.resolve({
      json: async () => ({
        code: 0,
        data: { pages: [{ page: p }], totalPages, pageIndex: p, totalCount: totalPages },
      }),
    })
  })

  it('totalPages=3：按 1→2→3 翻页并合并 pages', async () => {
    const spy = tradeFetch(3)
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900293400', 'trades')
    expect(r.ok).toBe(true)
    expect(r.data.pages.map(x => x.page)).toEqual([1, 2, 3])
    expect(spy.mock.calls.map(c => JSON.parse(c[1].body).args.pageNum)).toEqual([1, 2, 3])
  })

  it('totalPages 虚高：最多只翻 MAX_TRADE_PAGES 页', async () => {
    const spy = tradeFetch(99)
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900293400', 'trades')
    expect(spy).toHaveBeenCalledTimes(5)
    expect(r.data.pages).toHaveLength(5)
  })

  it('第 2 页失败：保留第 1 页，不把整个调仓区块判死', async () => {
    const spy = tradeFetch(3, 2)
    vi.stubGlobal('fetch', spy)
    const r = await requestBlock('u1', '900293400', 'trades')
    expect(r.ok).toBe(true)
    expect(r.data.pages.map(x => x.page)).toEqual([1])
  })
})
