import { describe, it, expect } from 'vitest'
import { mergeSection } from '../sectionMerge.js'

// 2026-09-15 静默审计 G5: 静默轮询失败不得把上一次的好数据清空
describe('mergeSection (对象型 section 静默失败合并)', () => {
  it('新值为 null 且旧值非空 → 保留旧值并记 stale', () => {
    const old = { mood: [{ zt: 30 }], effect: { up: 12 } }
    const res = { mood: null, effect: { up: 15 } }
    const { value, staleFields } = mergeSection(old, res)
    expect(value.mood).toEqual([{ zt: 30 }])      // 旧值保留, 不清空
    expect(value.effect).toEqual({ up: 15 })      // 新值可用则用新值
    expect(staleFields).toEqual(['mood'])
  })

  it('整体失败(全 null)不清空整页, 全部字段记 stale', () => {
    const old = { trend: [1], boards: [2], stocks: [3] }
    const { value, staleFields } = mergeSection(old, { trend: null, boards: null, stocks: null })
    expect(value).toEqual({ trend: [1], boards: [2], stocks: [3] })
    expect(staleFields.sort()).toEqual(['boards', 'stocks', 'trend'])
  })

  it('真值 0/空串/false 是真实读数, 必须覆盖旧值', () => {
    const old = { a: 5, b: 'x', c: true }
    const { value, staleFields } = mergeSection(old, { a: 0, b: '', c: false })
    expect(value).toEqual({ a: 0, b: '', c: false })
    expect(staleFields).toEqual([])
  })

  it('首次加载无旧值 → 直接用新值, 不误报 stale', () => {
    const { value, staleFields } = mergeSection(null, { mood: null })
    expect(value).toEqual({ mood: null })
    expect(staleFields).toEqual([])
  })

  it('数组/非对象结果按原样返回(风控/天梯等列表型 section 不受影响)', () => {
    expect(mergeSection({ a: 1 }, [1, 2])).toEqual({ value: [1, 2], staleFields: [] })
    expect(mergeSection({ a: 1 }, null).value).toBeNull()
  })

  it('旧值字段比新值多时不被删除', () => {
    const { value } = mergeSection({ keep: 1, other: 2 }, { keep: 3 })
    expect(value).toEqual({ keep: 3, other: 2 })
  })
})
