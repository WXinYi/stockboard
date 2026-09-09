// 六情绪「按日截断」回归对拍: JS 实时引擎 vs Python 截断参考(six_ref_asof.json)
// 夹具再生成: cd jiarenmens && venv/bin/python scripts/six_parity_fixture.py
// 红了说明 Python/JS 六情绪管线出现漂移 —— 改公式/阈值必须两边同步并重生成夹具同批提交。
//
// ⚠️ 舍入口径(2026-09-09 对齐): Python 侧一律用 `_round_half_up` + `_fsum_naive`, 不用内置
//   round()/sum() —— round 是银行家舍入(round(81.25,1)=81.2 vs JS 81.3), sum 自 CPython 3.12
//   起对 float 改补偿求和(本机 3.12 与 CI 3.11/JS 差 1 ULP)。这两处曾让 09-03 的 m_sector
//   差 0.4(3 日均值落在基期重复值 52.7 两侧 → bisect 差 2 档)。改六情绪公式时请沿用这两个助手。
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { normHistory, computeSixLive } from '../sixEmotion.js'
import histJson from '../../../public/data/latest/six_history.json'

const fixture = JSON.parse(readFileSync(new URL('./fixtures/six_ref_asof.json', import.meta.url), 'utf8'))
const rowsAll = normHistory(histJson)
const numEq = (a, b, tol = 0.15) => (a == null && b == null) || (a != null && b != null && Math.abs(a - b) <= tol)

describe('sixParity 按日截断对拍(JS vs Python 截断参考)', () => {
  it('夹具非空且字段齐全', () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(3)
    for (const c of fixture.cases) {
      expect(c.date).toBeTruthy()
      expect(c.dominant).toBeTruthy()
    }
  })

  it.each(fixture.cases.map(c => [c.date, c]))('%s 与 Python 一致', (_d, c) => {
    const i = rowsAll.findIndex(r => r.date === c.date)
    expect(i).toBeGreaterThan(0)
    const res = computeSixLive(rowsAll.slice(0, i), { ...rowsAll[i] })
    for (const k of ['market', 'spec', 'sector', 'm_market', 'm_spec', 'm_sector']) {
      expect(numEq(res[k], c[k]), `${c.date} ${k}: js=${res[k]} ref=${c[k]}`).toBe(true)
    }
    expect(res.dominant).toBe(c.dominant)
  })
})
