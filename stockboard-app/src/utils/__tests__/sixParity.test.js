// 六情绪「双实现对拍」(P0-2)：JS 实时引擎 vs Python 参考实现，同一输入必须逐日全等。
//
// 参考值由 jiarenmens/scripts/six_parity_fixture.py 生成（按日截断口径，不含未来日）：
//   rows  = Python _load_all() 产出的逐日原始分量（同一输入源）
//   cases = 各日参考值 market/spec/sector/m_*/dominant
// 前端按 case.date 切片 rows 后喂 computeSixLive() —— 比较的是"同输入→同输出"，
// 而不是拿一个可能已过期的产物文件当基准。
//
// ⚠️ 不要改用 public/data/latest/six_history.json 做基准：该切片生产者已下线
// （export_market.py 明确不再补），文件是冻结旧产物，其 zt/dt 与当前 DB 的
// market_breadth 口径在 172/258 天上不一致，会伪造出"双实现漂移"的假象。
//
// 改公式/阈值：Python 侧改完必须重跑 six_parity_fixture.py 并提交新夹具，两边同批提交。
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { normHistory, computeSixLive } from '../sixEmotion.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE = path.join(HERE, 'fixtures/six_ref_asof.json')
const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf8'))
const allRows = normHistory({ rows: fixture.rows })

const FIELDS = ['market', 'spec', 'sector', 'm_market', 'm_spec', 'm_sector', 'dominant']

describe('sixParity: JS 实时引擎 vs Python 参考(同输入对拍)', () => {
  it('夹具自身结构完整（rows 升序、覆盖每个 case 日期）', () => {
    expect(Array.isArray(fixture.rows)).toBe(true)
    expect(fixture.rows.length).toBeGreaterThan(200)
    expect(fixture.cases.length).toBeGreaterThanOrEqual(3)
    const dates = fixture.rows.map(r => r.d)
    expect([...dates].sort()).toEqual(dates)          // 升序
    for (const c of fixture.cases) {
      expect(dates).toContain(c.date)
      for (const f of FIELDS) expect(c).toHaveProperty(f)
    }
  })

  for (const c of fixture.cases) {
    it(`${c.date} 七项全等（含 dominant 与 3 日整体）`, () => {
      const upTo = allRows.filter(r => r.date <= c.date)
      expect(upTo.length).toBeGreaterThan(0)
      const got = computeSixLive(upTo, null)
      expect(got).not.toBeNull()
      expect(got.date).toBe(c.date)
      for (const f of FIELDS) {
        expect(got[f], `${c.date} ${f}`).toBe(c[f])
      }
    })
  }

  it('切片边界：同一日期序列不含未来日时结果稳定（可重入）', () => {
    const c = fixture.cases[0]
    const upTo = allRows.filter(r => r.date <= c.date)
    const a = computeSixLive(upTo, null)
    const b = computeSixLive(upTo, null)
    expect(a).toEqual(b)
    expect(upTo.some(r => r.date > c.date)).toBe(false)
  })

  it('传入同日 liveRow 不会重复计入该日（去重按 date）', () => {
    const c = fixture.cases[0]
    const upTo = allRows.filter(r => r.date <= c.date)
    const last = upTo[upTo.length - 1]
    const withDup = [...upTo, { ...last }]
    const a = computeSixLive(upTo, null)
    const b = computeSixLive(upTo.slice(0, -1), last)
    const d = computeSixLive(withDup, null)
    expect(b.date).toBe(a.date)
    for (const f of ['market', 'spec', 'sector']) expect(b[f]).toBe(a[f])
    // withDup 是外部直接 push 的重复行，computeSixLive 只对 liveRow 去重，故仅校验不抛错
    expect(d).not.toBeNull()
  })

  it('空/单行输入不崩（缺乏分位基准时返回结构而非 NaN）', () => {
    expect(computeSixLive([], null)).toBeNull()
    expect(computeSixLive(null, null)).toBeNull()
    const one = computeSixLive([allRows[0]], null)
    expect(one).not.toBeNull()
    expect(one.date).toBe(allRows[0].date)
    expect(Number.isFinite(one.market)).toBe(true)
  })
})
