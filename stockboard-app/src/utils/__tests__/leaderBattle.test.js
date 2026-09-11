import { describe, it, expect } from 'vitest'
import { computeBattle, gateSentence, whyNot } from '../leaderBattle.js'

// 合成一盘 AI 主线: 6板空间锚 + 3板梯队 + 2板梯队, 全部早封09:30+封单保持95%+主力净买
// 每只基础得分 = base + 10(封单) + 8(早封) + 4(主力) + 8(板块扩容, 今3-昨0)
const T930 = Date.UTC(2026, 7, 28, 1, 30) / 1000 // 北京时间 09:30

function makeCycle(stage, matrix) {
  const c = {
    stage,
    playbook: '',
    mainlines: [{ board: 'AI', count: 3, maxLevel: 6, cap: 0, names: [] }],
    leaders: [{ code: '300001', name: '锚哥', pid: 6, role: '空间锚(最高连板)', note: '6连板 · AI' }],
  }
  if (matrix) c.matrix = matrix
  return c
}

function makeBattle(stage, matrix) {
  const todayPool = [
    { code: '300001', name: '锚哥', pid: 6, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9 },
    { code: '300002', name: '梯队三', pid: 3, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9 },
    { code: '300003', name: '梯队二', pid: 2, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9 },
  ]
  const ladderRows = todayPool.map(r => ({ code: r.code, name: r.name, level: r.pid, bkName: 'AI', cap: 0, seal: 0, plates: ['AI'] }))
  return computeBattle({ ladderRows, todayPool, prevFull: [], unsealed: [], cycle: makeCycle(stage, matrix) })
}

const byCode = (b, code) => b.strike.candidates.find(c => c.code === code)

describe('computeStrike 矩阵分层闸门', () => {
  it('发酵×强|强: 全谱系可出击, gate 带矩阵标注', () => {
    const b = makeBattle('发酵', { high: '强', mid: '强' })
    expect(b.strike.gate.cap).toBe(100)
    expect(b.strike.gate.matrix.note).toContain('全谱系')
    expect(b.strike.gate.banner).toContain('📐')
    expect(byCode(b, '300002').status).toBe('出击')
    expect(byCode(b, '300003').status).toBe('出击')
  })

  it('发酵×强|弱: 中位禁买, 低位仍可做, cap 降到 70', () => {
    const b = makeBattle('发酵', { high: '强', mid: '弱' })
    expect(b.strike.gate.cap).toBe(70)
    expect(byCode(b, '300002').status).toBe('观察(矩阵禁买)') // 3板=中位
    expect(byCode(b, '300002').risk).toContain('中位禁买')
    expect(byCode(b, '300003').status).toBe('备选') // 2板=低位, 80→cap70
  })

  it('发酵×强|平衡: 中位转备选(care 封顶70)', () => {
    const b = makeBattle('发酵', { high: '强', mid: '平衡' })
    expect(byCode(b, '300002').score).toBeLessThanOrEqual(70)
    expect(byCode(b, '300002').status).toBe('备选')
    expect(byCode(b, '300003').status).toBe('出击') // 低位不受影响
  })

  it('高潮×平衡|弱: 高中位全禁买, cap 45', () => {
    const b = makeBattle('高潮', { high: '平衡', mid: '弱' })
    expect(b.strike.gate.cap).toBe(45)
    expect(byCode(b, '300001').status).toBe('观察(矩阵禁买)') // 6板=高位
    expect(byCode(b, '300002').status).toBe('观察(矩阵禁买)') // 3板=中位被矩阵禁买(谨慎接力可买, 但矩阵更严)
  })

  it('高潮×弱|强: 高位只观察, 中位谨慎接力按评分可做(受 cap70 限制)', () => {
    const b = makeBattle('高潮', { high: '弱', mid: '强' })
    expect(byCode(b, '300001').status).toBe('观察(矩阵)') // 6板=高位 watch
    expect(byCode(b, '300002').status).toBe('备选') // 3板=中位 go, 75→cap70 → 备选(去弱留强)
  })

  it('分歧×平衡|强: 阶段 cap60 与矩阵 cap100 取更严', () => {
    const b = makeBattle('分歧', { high: '平衡', mid: '强' })
    expect(b.strike.gate.cap).toBe(60)
    expect(byCode(b, '300001').status).toBe('备选') // 低吸不追高, 高位care×阶段60
  })

  it('退潮: 阶段禁买压过一切矩阵', () => {
    const b = makeBattle('退潮', { high: '强', mid: '强' })
    expect(b.strike.gate.cap).toBe(0)
    expect(byCode(b, '300001').status).toBe('观察(阶段禁买)')
  })

  it('无 matrix 数据: 不炸, banner 无矩阵段', () => {
    const b = makeBattle('发酵', null)
    expect(b.strike.gate.cap).toBe(100)
    expect(b.strike.gate.banner).not.toContain('📐')
    expect(b.strike.gate.matrix).toBeNull()
  })
})

describe('两端状态一致性(2026-09-11 与 stage_candidates.py 统一)', () => {
  it('高潮期"谨慎接力"按评分去弱留强: 满加成可出击(板块爆炸买跟风但去弱留强)', () => {
    const b = makeBattle('高潮', { high: '强', mid: '强' })
    const c = byCode(b, '300002')            // 3板主线高位 → mode 谨慎接力, 满加成 base45+30=75
    expect(c.mode).toBe('谨慎接力')
    expect(c.status).toBe('出击')            // 评分达标 → 可买("去弱留强"的实现)
    expect(c.buyTip.buy).toBe('秒板接力')     // 买点三件套在位
  })

  it('缩量板(≥2板 换手<3%)评分够也降级为 观察(缩量板)', () => {
    const todayPool = [
      { code: '300001', name: '锚哥', pid: 6, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9, turnover: 2 },
    ]
    const ladderRows = todayPool.map(r => ({ code: r.code, name: r.name, level: r.pid, bkName: 'AI', cap: 0, seal: 0, plates: ['AI'] }))
    const b = computeBattle({ ladderRows, todayPool, prevFull: [], unsealed: [], cycle: makeCycle('发酵', { high: '强', mid: '强' }) })
    const c = byCode(b, '300001')
    expect(c.score).toBeGreaterThanOrEqual(75)   // 评分本身够"出击"
    expect(c.status).toBe('观察(缩量板)')          // 但缩量板被降级
  })

  it('冰点期 cap60: 1进2 最多"备选"(待确认), 火种保持观察', () => {
    const todayPool = [
      { code: '300001', name: '锚哥', pid: 6, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9, turnover: 8 },
      { code: '300002', name: '梯队三', pid: 3, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9, turnover: 8 },
      { code: '300004', name: '首板哥', pid: 2, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9, turnover: 8 },
    ]
    const ladderRows = todayPool.map(r => ({ code: r.code, name: r.name, level: r.pid, bkName: 'AI', cap: 0, seal: 0, plates: ['AI'] }))
    const prevFull = [{ code: '300004', name: '首板哥', pid: 1, seal: 5e8, maxSeal: 5e8 }]
    const b = computeBattle({ ladderRows, todayPool, prevFull, unsealed: [], cycle: makeCycle('冰点', { high: '强', mid: '强' }) })
    expect(b.strike.gate.cap).toBe(60)
    const jin12 = byCode(b, '300004')            // 昨日首板今晋级2板 → 1进2排板, 满加成 75→cap60
    expect(jin12.status).toBe('备选')             // 待确认, 给不出"出击"
    expect(byCode(b, '300002').status).toBe('观察(火种)')  // 冰点火种保持观察
  })

  it('封单衰减(≥5板 封单<5000万)评分够也降级为 观察(封单衰减)', () => {
    // 满配三只票保证 板块扩容+8 生效; 空间锚封单 4e7(<5000万) 触发衰减
    const todayPool = [
      { code: '300001', name: '锚哥', pid: 6, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 4e7, maxSeal: 1e9, turnover: 8 },
      { code: '300002', name: '梯队三', pid: 3, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9, turnover: 8 },
      { code: '300003', name: '梯队二', pid: 2, plates: ['AI'], ztTime: T930, mainNet: 1e7, seal: 9.5e8, maxSeal: 1e9, turnover: 8 },
    ]
    const ladderRows = todayPool.map(r => ({ code: r.code, name: r.name, level: r.pid, bkName: 'AI', cap: 0, seal: 0, plates: ['AI'] }))
    const b = computeBattle({ ladderRows, todayPool, prevFull: [], unsealed: [], cycle: makeCycle('发酵', { high: '强', mid: '强' }) })
    const c = byCode(b, '300001')
    expect(c.score).toBeGreaterThanOrEqual(75)
    expect(c.status).toBe('观察(封单衰减)')
    expect(c.risk).toContain('封单衰减')
  })
})

describe('gateSentence 首页人话结论', () => {
  it('高潮×平衡|弱 + 无达标: 一句话给出禁买范围与只看结论', () => {
    const t = gateSentence('高潮', '平衡', '弱', 45, true)
    expect(t).toContain('情绪高潮')
    expect(t).toContain('高位禁买')
    expect(t).toContain('中位禁买')
    expect(t).toContain('低位轻仓备选')
    expect(t).toContain('池限45分')
    expect(t).toContain('暂无达标候选 → 只看')
  })
  it('有达标候选时不追加"只看"尾巴', () => {
    const t = gateSentence('发酵', '强', '强', 100, false)
    expect(t).toContain('池全开')
    expect(t).not.toContain('只看')
  })
  it('矩阵缺失退化为 阶段+池', () => {
    const t = gateSentence('分歧', null, null, 60, false)
    expect(t).toBe('情绪分歧 · 池限60分')
  })
  it('cap=0 显示池关闭', () => {
    expect(gateSentence('退潮', '强', '强', 0, true)).toContain('池关闭')
  })
})

describe('whyNot 差什么提示', () => {
  it('池关闭优先', () => {
    expect(whyNot({ level: 5, score: 88 }, 0, '强', '强')).toContain('阶段禁买')
  })
  it('矩阵禁买按梯队报', () => {
    expect(whyNot({ level: 5, score: 88 }, 45, '平衡', '弱')).toContain('中位被矩阵禁买')
    expect(whyNot({ level: 7, score: 88 }, 45, '平衡', '弱')).toContain('高位被矩阵禁买')
  })
  it('矩阵 watch / 跟风 / 评分不足', () => {
    expect(whyNot({ level: 7, score: 88 }, 70, '平衡', '平衡')).toContain('仅观察')
    expect(whyNot({ level: 3, score: 60, status: '观察(跟风回避)' }, 100, '强', '强')).toContain('跟风')
    expect(whyNot({ level: 1, score: 48 }, 100, '强', '强')).toContain('48 < 55')
  })
  it('可买/备选返回空', () => {
    expect(whyNot({ level: 1, score: 80 }, 100, '强', '强')).toBe('')
    expect(whyNot({ level: 1, score: 60 }, 100, '强', '强')).toBe('')
  })
})
