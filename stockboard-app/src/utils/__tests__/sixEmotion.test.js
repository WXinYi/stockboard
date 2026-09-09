import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { normHistory, computeSixLive, buildLiveRow, parseBidYi } from '../sixEmotion.js'

const hist = JSON.parse(readFileSync(new URL('../../../public/data/latest/six_history.json', import.meta.url), 'utf8'))

describe('sixEmotion 实时引擎 · 结构自检', () => {
  // 精确数值对拍由 sixParity.test.js(按日截断夹具)负责; 此处只做与数据同步的结构断言,
  // 避免把"最后一日"写死(历史回补/新增交易日后必然过期)。
  it('以 six_history 最后一日作实时输入, 输出结构完整', () => {
    const rows = normHistory(hist)
    const live = { ...rows[rows.length - 1] }
    const res = computeSixLive(rows.slice(0, -1), live)
    expect(res.date).toBe(rows[rows.length - 1].date)
    for (const k of ['market', 'spec', 'sector', 'm_market', 'm_spec', 'm_sector']) {
      expect(typeof res[k]).toBe('number')
      expect(res[k]).toBeGreaterThanOrEqual(0)
      expect(res[k]).toBeLessThanOrEqual(100)
    }
    expect(res.dominant).toBeTruthy()
    expect(typeof res.note).toBe('string')
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
