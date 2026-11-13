// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { useDataRefresh, inProbeWindow } from '../useUX.js'

const TODAY_URL = 'data/latest/today.json'
const fetchMock = vi.fn(async () => ({
  json: async () => ({ date: '2026-11-13', updatedAt: '2026-11-13T16:52:46+08:00' }),
}))

function Harness(onNewData) {
  return defineComponent({
    setup() {
      const r = useDataRefresh(onNewData)
      return { r }
    },
    template: '<div />',
  })
}

describe('inProbeWindow · 北京工作日 09:00–15:30', () => {
  it('盘中窗口内为 true，边界闭区间', () => {
    expect(inProbeWindow({ weekday: 'Fri', hhmm: 900 })).toBe(true)
    expect(inProbeWindow({ weekday: 'Fri', hhmm: 1000 })).toBe(true)
    expect(inProbeWindow({ weekday: 'Fri', hhmm: 1530 })).toBe(true)
  })
  it('收盘后/开盘前/周末一律为 false', () => {
    expect(inProbeWindow({ weekday: 'Fri', hhmm: 859 })).toBe(false)
    expect(inProbeWindow({ weekday: 'Fri', hhmm: 1531 })).toBe(false)
    expect(inProbeWindow({ weekday: 'Fri', hhmm: 2200 })).toBe(false)
    expect(inProbeWindow({ weekday: 'Sat', hhmm: 1000 })).toBe(false)
    expect(inProbeWindow({ weekday: 'Sun', hhmm: 1000 })).toBe(false)
  })
})

describe('useDataRefresh · 探测不应在收盘后空转', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    fetchMock.mockClear()
    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(document, 'visibilityState', {
      configurable: true, get: () => 'visible',
    })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('北京周五 22:00：静置 3 分钟 0 次 today.json 请求', async () => {
    vi.setSystemTime(new Date('2026-11-13T14:00:00Z'))  // = 北京 22:00
    const w = mount(Harness())
    await vi.advanceTimersByTimeAsync(180_000)
    expect(fetchMock).not.toHaveBeenCalled()
    w.unmount()
  })

  it('北京周六 10:00：静置 3 分钟 0 次 today.json 请求', async () => {
    vi.setSystemTime(new Date('2026-11-14T02:00:00Z'))  // = 北京周六 10:00
    const w = mount(Harness())
    await vi.advanceTimersByTimeAsync(180_000)
    expect(fetchMock).not.toHaveBeenCalled()
    w.unmount()
  })

  it('北京周五 10:00：每 60s 恰好探测一次', async () => {
    vi.setSystemTime(new Date('2026-11-13T02:00:00Z'))  // = 北京 10:00（避开 09:20–09:40 auction 窗口）
    const w = mount(Harness())
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain(TODAY_URL)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    w.unmount()
  })

  it('首屏 initCheck 复用快照：不再额外请求 today.json', async () => {
    vi.setSystemTime(new Date('2026-11-13T14:00:00Z'))
    const w = mount(Harness())
    await w.vm.r.initCheck({ date: '2026-11-13', updatedAt: '2026-11-13T16:52:46+08:00' })
    expect(fetchMock).not.toHaveBeenCalled()
    w.unmount()
  })

  it('收盘后切回标签页仍补一次探测，保证新部署不漏', async () => {
    vi.setSystemTime(new Date('2026-11-13T14:00:00Z'))
    const w = mount(Harness())
    document.dispatchEvent(new Event('visibilitychange'))
    await vi.advanceTimersByTimeAsync(10)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    w.unmount()
  })
})
