import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { normHistory, computeSixLive, buildLiveRow, parseBidYi } from '../sixEmotion.js'

const hist = JSON.parse(readFileSync(new URL('../../../public/data/latest/six_history.json', import.meta.url), 'utf8'))

describe('sixEmotion 实时引擎 · 与 Python six_scores 对拍', () => {
  it('以 six_history 最后一日(09-04)作实时输入, 结果与后端一致', () => {
    const rows = normHistory(hist)
    const live = { ...rows[rows.length - 1] }
    const res = computeSixLive(rows.slice(0, -1), live)
    const close = (got, want, tol = 0.15) => Math.abs((got ?? -999) - want) <= tol
    expect(res.date).toBe('2026-09-04')
    expect(close(res.market, 36.2)).toBe(true)
    expect(close(res.spec, 27.4)).toBe(true)
    expect(close(res.sector, 86.0)).toBe(true)
    expect(close(res.m_market, 26.7)).toBe(true)
    expect(close(res.m_spec, 53.3)).toBe(true)
    expect(close(res.m_sector, 76.2)).toBe(true)
    expect(res.dominant).toBe('退潮防守')
    expect(res.note).toContain('不强行交易')
  })
})

describe('buildLiveRow 今日分量构造', () => {
  const history = [
    { date: '2026-09-01', top_board: '机器人', top_cnt: 8, run_days: 2, switches_5d: 1 },
    { date: '2026-09-02', top_board: '机器人', top_cnt: 10, run_days: 3, switches_5d: 1 },
  ]
  const todayPool = [
    { code: 'A', pid: 3, plates: ['机器人', '减速器'], amount: 5e8 },
    { code: 'B', pid: 1, plates: ['机器人'], amount: 3e8 },
    { code: 'C', pid: 1, plates: ['氢能'], amount: 1e8 },
  ]
  const prevFull = [
    { code: 'X', pid: 4, plates: ['机器人'] }, { code: 'Y', pid: 4, plates: ['机器人'] }, { code: 'Z', pid: 1, plates: ['氢能'] },
  ]
  it('板块聚合/晋级/续板/连任续算正确', () => {
    const row = buildLiveRow({
      todayPool, prevFull, unsealed: [{ code: 'u1' }, { code: 'u2' }], history,
      mood: { strong: 61, df: 7 }, cycle: { metrics: { zt: 30, height: 3, brokeRate: 22 } },
      bidAmt: 164, idxTrend: 1.2, date: '2026-09-03',
    })
    expect(row.top_board).toBe('机器人')
    expect(row.top_cnt).toBe(2)
    expect(row.top_amt).toBe(8e8)
    expect(row.promo).toBeCloseTo(1 / 3, 5)
    expect(row.relay).toBe(0)            // 昨最高板 X/Y 今日均未封板
    expect(row.run_days).toBe(4)         // 主线机器人连续(昨 run=3)
    expect(row.switches_5d).toBe(0)
    expect(row.tc_prev).toBe(2)          // 昨日「机器人」主线家数 = X/Y
    expect(row.zhaban).toBe(2)
    expect(row.date).toBe('2026-09-03')
  })
  it('主线未变时连任天数+1', () => {
    const pool = todayPool.map(r => ({ ...r, plates: ['机器人'] }))
    const row = buildLiveRow({ todayPool: pool, prevFull, unsealed: [], history, mood: {}, cycle: { metrics: {} }, date: '2026-09-03' })
    expect(row.top_board).toBe('机器人')
    expect(row.run_days).toBe(4)
  })
})

describe('parseBidYi', () => {
  it('解析 164亿 / 空值', () => {
    expect(parseBidYi('164亿')).toBe(164)
    expect(parseBidYi('')).toBeNull()
  })
})
