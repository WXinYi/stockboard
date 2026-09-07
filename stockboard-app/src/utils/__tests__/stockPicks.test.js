import { describe, it, expect } from 'vitest'
import {
  gateTier, capWord, basisLine, statusWord, holdInfo, mergeCandidates,
  isMorning, sectionOrder, resolveVerdict, sameDataDay, divergenceNote, limitPctOf, prevCloseOf, distToLimit, pxLine, sortPicksFirst, sortReviewRows,
} from '../stockPicks.js'

describe('gateTier 结论头池判定', () => {
  it('禁买 = 池关闭', () => {
    expect(gateTier(0)).toEqual({ verdict: '禁买', pool: '关闭', cls: 'ban' })
  })
  it('全开 = 可买', () => {
    expect(gateTier(100)).toEqual({ verdict: '可买', pool: '全开', cls: 'go' })
  })
  it('限分 = 谨慎可买', () => {
    expect(gateTier(45)).toEqual({ verdict: '谨慎可买', pool: '限45分', cls: 'warn' })
  })
  it('脏输入按禁买/占位处理(不崩)', () => {
    expect(gateTier(null)).toMatchObject({ verdict: '—' })
    expect(gateTier('x')).toMatchObject({ verdict: '—' })
  })
})

describe('capWord / basisLine', () => {
  it('阶段→成上限', () => {
    expect(capWord('退潮')).toBe('≤2成')
    expect(capWord('发酵')).toBe('6-8成')
    expect(capWord('不存在的阶段')).toBe('—')
  })
  it('basis 无注记不带多余分隔', () => {
    expect(basisLine('发酵', '投机强')).toBe('发酵 · 投机强')
    expect(basisLine('')).toBe('—')
  })
})

describe('statusWord 全站唯一状态词', () => {
  const cases = [
    ['出击', '可买'], ['出击·xxx', '可买'], ['可做(龙头接力)', '可买'], ['可买', '可买'],
    ['备选', '待确认'], ['可做(矩阵谨慎)', '待确认'], ['备选·需确认', '待确认'],
    ['观察', '只看不买'], ['观察(只看最强)', '只看不买'], ['', '只看不买'],
    ['观察(弱转强·禁买期)', '只看不买'], ['观察(火种)', '只看不买'],
  ]
  it.each(cases)('%s → %s', (raw, want) => {
    expect(statusWord(raw).txt).toBe(want)
  })
  it('类名映射正确', () => {
    expect(statusWord('出击').cls).toBe('go')
    expect(statusWord('备选').cls).toBe('alt')
    expect(statusWord('观察').cls).toBe('watch')
  })
})

describe('持仓重叠标记', () => {
  const pos = [{ code: 'A', name: '甲', weight: '3成' }]
  it('命中返回已持仓+权重', () => {
    expect(holdInfo({ code: 'A' }, pos)).toEqual({ held: true, txt: '已持仓 3成' })
  })
  it('未命中为 null', () => {
    expect(holdInfo({ code: 'B' }, pos)).toBeNull()
  })
  it('mergeCandidates 无候选空数组不崩', () => {
    expect(mergeCandidates(undefined, pos)).toEqual([])
  })
})

describe('时段排序', () => {
  it('10 点前盘前优先', () => {
    expect(isMorning(new Date('2026-09-07T09:30:00'))).toBe(true)
    expect(sectionOrder(true)).toEqual(['pre', 'strike'])
  })
  it('10 点后今日出击优先', () => {
    expect(isMorning(new Date('2026-09-07T10:01:00'))).toBe(false)
    expect(sectionOrder(false)).toEqual(['strike', 'pre'])
  })
})

describe('resolveVerdict 结论与达标候选联动', () => {
  it('禁买保持禁买(即使候选为0)', () => {
    const t = gateTier(0)
    expect(resolveVerdict(t, 0)).toEqual(t)
  })
  it('池开但零达标 → 仅观察', () => {
    expect(resolveVerdict(gateTier(100), 0)).toEqual({ verdict: '仅观察', pool: '全开', cls: 'warn' })
    expect(resolveVerdict(gateTier(45), 0)).toEqual({ verdict: '仅观察', pool: '限45分', cls: 'warn' })
  })
  it('有达标候选 → 维持原结论', () => {
    expect(resolveVerdict(gateTier(100), 1).verdict).toBe('可买')
    expect(resolveVerdict(gateTier(45), 2)).toEqual({ verdict: '谨慎可买', pool: '限45分', cls: 'warn' })
  })
})

describe('sameDataDay 日终指标展示守卫', () => {
  it('同日返回 true, 跨日(含旧数据)返回 false', () => {
    expect(sameDataDay('2026-09-07', '2026-09-07')).toBe(true)
    expect(sameDataDay('2026-09-04', '2026-09-07')).toBe(false)
    expect(sameDataDay('', '2026-09-07')).toBe(false)
    expect(sameDataDay(null, '2026-09-07')).toBe(false)
  })
})

describe('divergenceNote 双引擎背离提示', () => {
  const TIER = { verdict: '仅观察', pool: '限45分', cls: 'warn' }
  it('六情绪极强+池未开/无达标 → 提示背离与豁免出路', () => {
    const t = divergenceNote('板块情绪极强', TIER, 0)
    expect(t).toContain('双引擎背离')
    expect(t).toContain('人工豁免')
  })
  it('六情绪非极强 / 有达标候选 / 已可买 → 不提示', () => {
    expect(divergenceNote('退潮防守', TIER, 0)).toBe('')
    expect(divergenceNote('板块情绪极强', TIER, 2)).toBe('')
    expect(divergenceNote('板块情绪极强', { verdict: '可买' }, 1)).toBe('')
  })
})

describe('距涨停可执行度', () => {
  it('limitPctOf 分板块', () => {
    expect(limitPctOf('600519')).toBe(10)
    expect(limitPctOf('300750')).toBe(20)
    expect(limitPctOf('832000')).toBe(30)
  })
  it('prevCloseOf 反推昨收', () => {
    expect(prevCloseOf(11, 10)).toBeCloseTo(10, 6)
    expect(prevCloseOf(null, 10)).toBeNull()
  })
  it('distToLimit: 触板=0, 未到为正', () => {
    expect(distToLimit(11, 10, '600519')).toBe(0)
    expect(distToLimit(10, 0, '600519')).toBe(10)
    expect(distToLimit(10, 0, '300750')).toBe(20)
  })
})

describe('pxLine 距买点行(按模式)', () => {
  it('打板类: 对照涨停价(10% 板)', () => {
    const t = pxLine({ mode: '排板接力', price: 10, pct: 0, code: '600519' })
    expect(t).toContain('距买点(涨停) 10%')
    expect(t).toContain('止损参考 9.70')
  })
  it('低吸类: 对照分时均价(价在上方为负=等回踩)', () => {
    const t = pxLine({ mode: '低吸不追高', price: 11, pct: 10, code: '600519', avg: 10.5 })
    expect(t).toContain('距买点(分时均价) -4.5%')
  })
  it('无均价/非买点模式: 只给现价+止损', () => {
    const t = pxLine({ mode: '观察', price: 9.9, pct: -1, code: '600519' })
    expect(t).toContain('现价 9.9(-1.00%)')
    expect(t).not.toContain('距买点')
  })
  it('脏价返回空串', () => {
    expect(pxLine({ price: null })).toBe('')
  })
})

describe('列表排序(最适合买入在前)', () => {
  it('弱转强: 可买>待确认>只看, 同级竞价高在前', () => {
    const rows = [
      { name: 'C', w: { txt: '只看不买' }, bid_pct: '+6.7' },
      { name: 'B', w: { txt: '可买' }, bid_pct: '+2.0' },
      { name: 'A', w: { txt: '可买' }, bid_pct: '+4.6' },
      { name: 'D', w: { txt: '待确认' }, bid_pct: '+3.0' },
    ]
    expect(sortPicksFirst(rows).map(r => r.name)).toEqual(['A', 'B', 'D', 'C'])
  })
  it('复核: 持有>兑现>减半>开盘走>不出手', () => {
    const rows = [
      { tag: '不出手' }, { tag: '开盘走' }, { tag: '持有到尾盘' }, { tag: '减半(弱于预期)' }, { tag: '冲高兑现' },
    ]
    expect(sortReviewRows(rows).map(r => r.tag)).toEqual(['持有到尾盘', '冲高兑现', '减半(弱于预期)', '开盘走', '不出手'])
  })
})
