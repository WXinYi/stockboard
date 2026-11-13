import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('../../data/loader.js', () => ({
  fetchPlayers: vi.fn(),
  fetchStocks: vi.fn(),
  fetchToday: vi.fn(),
}))

import { fetchPlayers, fetchStocks, fetchToday } from '../../data/loader.js'
import { useDataV2 } from '../useDataV2.js'

const PLAYERS = {
  date: '2026-11-13',
  rankTime: '09:26',
  players: [
    {
      id: '900456476', name: '狼之行一', userId: 'u-1', watched: true,
      prevClose: { totalReturn: 100, dailyReturn: 1.5, netValue: 2 },
      today: { updatedAt: '15:15', tradeCount: 2 },
    },
    {
      id: '900111111', name: '未关注', userId: 'u-2', watched: false,
      prevClose: { totalReturn: 10, dailyReturn: -1, netValue: 1.1 },
      today: null,
    },
  ],
}
const STOCKS = {
  date: '2026-11-13', snapshot: 'close', updatedAt: '2026-11-13T15:15:00+08:00',
  stats: [{ code: '002086', name: '东方海洋', h: 42, tp: 310.5, ap: 3.2 }],
}
const TODAY = {
  date: '2026-11-13', updatedAt: '2026-11-13T15:15:00+08:00',
  tradedPlayerIds: ['900456476'],
  emContract: { ok: true, checkedAt: '2026-11-13T09:26:00+08:00' },
}

describe('useDataV2 · P3 4 契约数据层', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchToday.mockResolvedValue(TODAY)
    fetchPlayers.mockResolvedValue(PLAYERS)
    fetchStocks.mockResolvedValue(STOCKS)
  })

  it('R5#5: loadData 只等 today，不预拉 players/stocks', async () => {
    const d = useDataV2()
    await d.loadData()
    expect(fetchToday).toHaveBeenCalledTimes(1)
    expect(fetchPlayers).not.toHaveBeenCalled()
    expect(fetchStocks).not.toHaveBeenCalled()
  })

  it('R5#1/#2: crawlTime / currentDate 来自 today.json', async () => {
    const d = useDataV2()
    await d.loadData()
    expect(d.crawlTime.value).toBe('2026-11-13T15:15:00+08:00')
    expect(d.currentDate.value).toBe('2026-11-13')
  })

  it('emContract 透传（ok=false 时下游应 0 请求）', async () => {
    fetchToday.mockResolvedValue({ ...TODAY, emContract: { ok: false, checkedAt: 'x' } })
    const d = useDataV2()
    await d.loadData()
    expect(d.emContract.value.ok).toBe(false)
  })

  it('tradedPlayerIds 来自 today.json', async () => {
    const d = useDataV2()
    await d.loadData()
    expect([...d.tradedPlayerIds.value]).toEqual(['900456476'])
  })

  it('players 派生: watched 置顶、收益取 prevClose、带 userId', async () => {
    const d = useDataV2()
    await d.ensureSlices(['players'])
    const { pinned, rest } = d.sortedPlayers.value
    expect(pinned.map(p => p.zh_id)).toEqual(['900456476'])
    expect(rest.map(p => p.zh_id)).toEqual(['900111111'])
    expect(pinned[0].total_return).toBe(100)
    expect(pinned[0].daily_return).toBe(1.5)
    expect(pinned[0].net_value).toBe(2)
    expect(d.playerLookup.value['900456476'].userId).toBe('u-1')
    expect([...d.watchedIds.value]).toEqual(['900456476'])
  })

  it('stockStats 直接映射 stats（新字段 code/name，无 c/n 兼容层）', async () => {
    const d = useDataV2()
    await d.ensureSlices(['stocks'])
    expect(d.stockStats.value).toHaveLength(1)
    expect(d.stockStats.value[0].code).toBe('002086')
    expect(d.stockStats.value[0].c).toBeUndefined()
    expect(d.stocksSnapshot.value).toBe('close')
  })

  it('优质口径已废除: isQuality 恒 false', () => {
    const d = useDataV2()
    expect(d.isQuality({ days: 999 })).toBe(false)
  })

  it('分片失败可辨: sliceErrors 标记且抛错', async () => {
    fetchPlayers.mockRejectedValue(new Error('boom'))
    const d = useDataV2()
    await expect(d.ensureSlices(['players'])).rejects.toThrow('boom')
    expect(d.sliceErrors.value.players).toBe(true)
  })
})
