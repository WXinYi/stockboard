// emotionCycle 单测（P0-2 补测）—— 与 jiarenmens/src/analysis/emotion_cycle.py 成对维护的
// 周期判定引擎，此前零覆盖。这里钉住：阶段判定的 6 个分支、阈值边界、PidType=5 封顶近似、
// 分层口径、主线聚合与龙头分类，以及"零输入不崩"。
import { describe, it, expect } from 'vitest'
import {
  CYCLE_CFG, STAGES, STAGE_PLAYBOOK, STAGE_RULES, STAGE_COLORS,
  tierOf, boardMainlines, classifyLeaders, computeCycle,
} from '../emotionCycle.js'

const row = (code, level, extra = {}) => ({ code, name: code, level, bkName: '', cap: 0, plates: [], ...extra })

describe('常量与配置一致性', () => {
  it('六个阶段在 playbook/rules/colors 中都有定义（漏一个页面就会空白）', () => {
    expect(STAGES).toEqual(['冰点', '启动', '发酵', '高潮', '分歧', '退潮'])
    for (const s of STAGES) {
      expect(STAGE_PLAYBOOK[s], s).toBeTruthy()
      expect(STAGE_RULES[s], s).toBeTruthy()
      expect(STAGE_COLORS[s], s).toMatch(/^#[0-9a-f]{6}$/i)
    }
  })

  it('阈值配置成组自洽（退潮阈值高于冰点、高潮高于发酵高度）', () => {
    expect(CYCLE_CFG.iceHeight).toBeLessThan(CYCLE_CFG.fajiaoHeight)
    expect(CYCLE_CFG.fajiaoHeight).toBeLessThan(CYCLE_CFG.gaochaoHeight)
    expect(CYCLE_CFG.iceZtRatio).toBeLessThan(CYCLE_CFG.tuiZtRatio)
    expect(CYCLE_CFG.tuiZtRatio).toBeLessThan(CYCLE_CFG.gaochaoZtRatio)
  })
})

describe('tierOf 三层分层（leaderBattle 与 stage_candidates 共用口径）', () => {
  it('2 板及以下 low / 3-5 板 mid / 6 板以上 high', () => {
    expect([0, 1, 2].map(tierOf)).toEqual(['low', 'low', 'low'])
    expect([3, 4, 5].map(tierOf)).toEqual(['mid', 'mid', 'mid'])
    expect([6, 9].map(tierOf)).toEqual(['high', 'high'])
  })
})

describe('computeCycle 阶段判定（六分支）', () => {
  const today = '2026-09-11'
  const rf = (zt, brokeRate, series = []) => ({ today: { day: today, zt, brokeRate }, series })

  it('退潮：高度较昨日降 ≥2 级', () => {
    const c = computeCycle({
      ladderRows: [row('A', 2)],
      riseFall: rf(30, 20),
      moodSeries: [{ day: '2026-09-10', strong: 40, lbgd: 5 }],
    })
    expect(c.stage).toBe('退潮')
    expect(c.metrics.heightPrev).toBe(5)
    expect(c.metrics.height).toBe(2)
    expect(c.reasons[0]).toContain('降3级')
  })

  it('退潮：涨停缩量到 ma5 的 80% 以下且破板率高于 ma5', () => {
    const series = [10, 10, 10, 10, 10].map((zt, i) => ({ day: `2026-09-0${9 - i}`, zt, brokeRate: 10 }))
    const c = computeCycle({ ladderRows: [row('A', 4)], riseFall: rf(5, 20, series) })
    expect(c.metrics.ztMa5).toBe(10)
    expect(c.stage).toBe('退潮')
  })

  it('冰点：高度 ≤3 且破板率 >30%', () => {
    const c = computeCycle({ ladderRows: [row('A', 2)], riseFall: rf(25, 35) })
    expect(c.stage).toBe('冰点')
    expect(c.metrics.height).toBe(2)
  })

  it('冰点边界：高度 3 仍在冰点区，高度 4 不再命中', () => {
    expect(computeCycle({ ladderRows: [row('A', 3)], riseFall: rf(25, 35) }).stage).toBe('冰点')
    expect(computeCycle({ ladderRows: [row('A', 4)], riseFall: rf(25, 35) }).stage).not.toBe('冰点')
  })

  it('高潮：高度 ≥6 直接确认', () => {
    const c = computeCycle({ ladderRows: [row('A', 7)], riseFall: rf(60, 25) })
    expect(c.stage).toBe('高潮')
  })

  it('发酵：高度持平且 ≥5，中位晋级率健康', () => {
    const c = computeCycle({
      ladderRows: [row('A', 4), row('B', 5), row('C', 5)],
      prevPool: [{ code: 'A', pid: 3 }, { code: 'B', pid: 4 }],
      riseFall: rf(50, 20),
      moodSeries: [{ day: '2026-09-10', strong: 55, lbgd: 5 }],
    })
    expect(c.metrics.promo.mid).toBe(1)
    expect(c.stage).toBe('发酵')
  })

  it('发酵需要中位晋级率 ≥0.4，不达标则回落分歧', () => {
    const c = computeCycle({
      // D 撑起 5 板高度；A/B/C 为昨日 3 板，只有 A 晋级到 4 板 → 中位晋级率 1/3
      ladderRows: [row('D', 5), row('A', 4), row('B', 3), row('C', 3)],
      prevPool: [{ code: 'A', pid: 3 }, { code: 'B', pid: 3 }, { code: 'C', pid: 3 }],
      riseFall: rf(50, 20),
      moodSeries: [{ day: '2026-09-10', strong: 55, lbgd: 5 }],
    })
    expect(c.metrics.promo.mid).toBeCloseTo(1 / 3, 5)
    expect(c.stage).toBe('分歧')
  })

  it('启动：高度较昨日回升但未到五板', () => {
    const c = computeCycle({
      ladderRows: [row('A', 4)],
      riseFall: rf(40, 20),
      moodSeries: [{ day: '2026-09-10', strong: 40, lbgd: 3 }],
    })
    expect(c.stage).toBe('启动')
  })

  it('分歧：高度持平且不满足其它条件（默认分支）', () => {
    const c = computeCycle({
      ladderRows: [row('A', 4)],
      riseFall: rf(40, 20),
      moodSeries: [{ day: '2026-09-10', strong: 40, lbgd: 4 }],
    })
    expect(c.stage).toBe('分歧')
    expect(c.playbook).toBe(STAGE_PLAYBOOK['分歧'])
  })
})

describe('computeCycle 边界与口径', () => {
  it('零输入不崩：返回结构完整而非 NaN', () => {
    const c = computeCycle({})
    expect(c.stage).toBe('分歧')
    expect(c.date).toBe('')
    expect(c.metrics.height).toBe(0)
    expect(c.metrics.zt).toBeNull()
    expect(Number.isFinite(c.confidence)).toBe(true)
    expect(c.leaders).toEqual([])
    expect(c.mainlines).toEqual([])
  })

  it('昨日数据缺失时不臆造高度趋势（heightPrev 为 null）', () => {
    const c = computeCycle({ ladderRows: [row('A', 3)], riseFall: { today: { day: '2026-09-11', zt: 30, brokeRate: 10 } } })
    expect(c.metrics.heightPrev).toBeNull()
  })

  it('PidType=5 封顶：昨 5 板计入 mid 桶，需今日 ≥6 板才算晋级（与 Python 同口径）', () => {
    const base = {
      riseFall: { today: { day: '2026-09-11', zt: 50, brokeRate: 10 } },
      moodSeries: [{ day: '2026-09-10', lbgd: 5 }],
    }
    const capped = [{ code: 'A', pid: 5 }]
    const hit = computeCycle({ ...base, ladderRows: [row('A', 6)], prevPool: capped })
    const miss = computeCycle({ ...base, ladderRows: [row('A', 5)], prevPool: capped })
    expect(hit.metrics.promo.mid).toBe(1)
    expect(miss.metrics.promo.mid).toBe(0)
    // pid 封顶为 5 时 high 桶(6..99)无样本 → null；Python 侧同此（emotion_cycle.py:238）
    expect(hit.metrics.promo.high).toBeNull()
  })

  it('梯队计数按今日 level / 昨日 pid 分别归层', () => {
    const c = computeCycle({
      ladderRows: [row('A', 1), row('B', 3), row('C', 7)],
      prevPool: [{ code: 'X', pid: 2 }, { code: 'Y', pid: 5 }],
      riseFall: { today: { day: '2026-09-11', zt: 30, brokeRate: 20 } },
    })
    expect(c.metrics.ladder).toEqual({ low: 1, mid: 1, high: 1 })
    expect(c.metrics.ladder).toBeTruthy()
  })

  it('晋级率无昨日样本时返回 null，不返回 0 冒充', () => {
    const c = computeCycle({ ladderRows: [row('A', 3)], riseFall: { today: { day: '2026-09-11', zt: 30, brokeRate: 20 } } })
    expect(c.metrics.promo.low).toBeNull()
    expect(c.metrics.promo.mid).toBeNull()
    expect(c.metrics.promo.high).toBeNull()
  })

  it('zt 缺失时 zr 为 null（不以 0 冒充），文案回退"无ma5"', () => {
    const series = [{ day: '2026-09-10', zt: 40, brokeRate: 10 }]
    const c = computeCycle({ ladderRows: [row('A', 4)], riseFall: { today: { day: '2026-09-11', brokeRate: 12 }, series } })
    expect(c.metrics.zt).toBeNull()
    expect(c.metrics.ztMa5).toBe(40)
    expect(c.stage).toBe('分歧')
  })
})

describe('boardMainlines 主线聚合', () => {
  it('家数 ≥3 才入榜，按家数降序、同家数看最高板', () => {
    const rows = [
      row('A', 2, { plates: ['半导体'] }), row('B', 3, { plates: ['半导体'] }), row('C', 1, { plates: ['半导体'] }),
      row('D', 5, { plates: ['算力'] }), row('E', 2, { plates: ['算力'] }), row('F', 4, { plates: ['算力'] }),
      row('G', 2, { plates: ['孤板'] }),
    ]
    const m = boardMainlines(rows)
    // 两个板块家数同为 3 → 次级键 maxLevel 降序，算力(5) 在半导体(3) 之前
    expect(m.map(x => x.board)).toEqual(['算力', '半导体'])
    expect(m[0].count).toBe(3)
    expect(m[0].maxLevel).toBe(5)
    expect(m[1].maxLevel).toBe(3)
    expect(m.some(x => x.board === '孤板')).toBe(false)   // 家数不足 3
  })

  it('最多返回 6 条主线（页面只展示前 6）', () => {
    const rows = []
    for (let b = 0; b < 9; b++) for (let i = 0; i < 3; i++) rows.push(row(`${b}-${i}`, 1, { plates: [`板${b}`] }))
    expect(boardMainlines(rows)).toHaveLength(6)
  })
})

describe('classifyLeaders 龙头分类', () => {
  it('空间锚取最高连板，且封单衰减会提示分歧', () => {
    const c = computeCycle({
      ladderRows: [
        row('A', 5, { bkName: '半导体', plates: ['半导体'], seal: 1e7 }),
        row('B', 2, { bkName: '算力', plates: ['算力'] }),
      ],
      riseFall: { today: { day: '2026-09-11', zt: 30, brokeRate: 20 } },
    })
    expect(c.leaders[0].code).toBe('A')
    expect(c.leaders[0].role).toContain('空间锚')
    expect(c.leaders[0].note).toContain('封单衰减')
  })

  it('最高板属于该主线时，不再重复推一条板块龙头', () => {
    const leaders = classifyLeaders(
      [row('A', 5, { plates: ['半导体'] }), row('B', 3, { plates: ['半导体'] }), row('C', 2, { plates: ['半导体'] })],
      [{ board: '半导体', count: 3, maxLevel: 5, cap: 0, names: [] }],
    )
    expect(leaders).toHaveLength(1)
    expect(leaders[0].role).toContain('空间锚')
  })

  it('空梯队返回空数组', () => {
    expect(classifyLeaders([], [])).toEqual([])
  })
})
