import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { withTimeout, fetchZhangTingGene, fetchStockPankou } from '../useKplApi.js'

describe('KPL 请求超时兜底（响应体挂起不再永久 pending）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('withTimeout 正常返回且清掉定时器', async () => {
    expect(await withTimeout(Promise.resolve('ok'), 50)).toBe('ok')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('withTimeout 超时抛「请求超时」且清掉定时器', async () => {
    const p = withTimeout(new Promise(() => {}), 50)
    const assertion = expect(p).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(50)
    await assertion
    expect(vi.getTimerCount()).toBe(0)
  })

  it('GET 通道：resp.json() 永不 resolve 时仍触发 8s 超时', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: () => new Promise(() => {}) })))
    const p = fetchZhangTingGene('002594')
    const assertion = expect(p).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(8000)
    await assertion
  })

  it('POST 通道：resp.json() 永不 resolve 时同样触发超时', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: () => new Promise(() => {}) })))
    const p = fetchStockPankou('002594')
    const assertion = expect(p).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(8000)
    await assertion
  })

  it('fetch 同步抛错也进入统一错误通道（非 silent 时抛出）', async () => {
    vi.stubGlobal('fetch', vi.fn(() => { throw new Error('bad url') }))
    await expect(fetchZhangTingGene('002594')).rejects.toThrow('bad url')
  })

  it('silent=true 时超时返回 null 而不是抛错', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ json: () => new Promise(() => {}) })))
    const p = fetchZhangTingGene('002594', true)
    await vi.advanceTimersByTimeAsync(8000)
    await expect(p).resolves.toBeNull()
  })

  it('withTimeout 的 onTimeout 只在超时触发，成功时不触发', async () => {
    const onTimeout = vi.fn()
    await withTimeout(Promise.resolve('ok'), 50, onTimeout)
    expect(onTimeout).not.toHaveBeenCalled()
    const p = withTimeout(new Promise(() => {}), 50, onTimeout)
    const assertion = expect(p).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(50)
    await assertion
    expect(onTimeout).toHaveBeenCalledTimes(1)
  })

  it('GET 超时会 abort 底层 fetch（signal 传入且被取消）', async () => {
    let signal = null
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      signal = opts.signal
      return { json: () => new Promise(() => {}) }
    }))
    const p = fetchZhangTingGene('002594')
    const assertion = expect(p).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(8000)
    await assertion
    expect(signal).toBeInstanceOf(AbortSignal)
    expect(signal.aborted).toBe(true)
  })

  it('POST 超时同样 abort 底层 fetch，成功路径不会误取消', async () => {
    let signal = null
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      signal = opts.signal
      return { json: () => new Promise(() => {}) }
    }))
    const p = fetchStockPankou('002594')
    const assertion = expect(p).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(8000)
    await assertion
    expect(signal.aborted).toBe(true)

    signal = null
    vi.stubGlobal('fetch', vi.fn(async (_url, opts) => {
      signal = opts.signal
      return { json: async () => ({ errcode: '0', List: [] }) }
    }))
    await fetchStockPankou('002594')
    expect(signal.aborted).toBe(false)
  })
})
