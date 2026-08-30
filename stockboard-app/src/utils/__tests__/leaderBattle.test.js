import { describe, it, expect } from 'vitest'
import { computeBattle } from '../leaderBattle.js'

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
    expect(byCode(b, '300002').status).toBe('观察(矩阵禁买)') // 3板=中位
  })

  it('高潮×弱|强: 高位只观察, 中位仍可做(受 cap70 限制)', () => {
    const b = makeBattle('高潮', { high: '弱', mid: '强' })
    expect(byCode(b, '300001').status).toBe('观察(矩阵)') // 6板=高位 watch
    expect(byCode(b, '300002').status).toBe('备选') // 3板=中位 go, 75→cap70
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
