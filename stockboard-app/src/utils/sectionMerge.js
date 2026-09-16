// 对象型 section 的静默失败合并规则(2026-09-15 静默审计 G5 修复的纯函数层)。
//
// 背景: 市场二级页对 newhighs/mood/live/cycle 这类"对象型"section 用 silent 轮询拉数据,
// 失败时接口层会返回带 null 字段的对象(而非抛错), 原写法 `if (res) data.value = res`
// 会把上一次的好数据整体清空 → 页面显示"暂无数据", 等于把"取不到"伪装成"市场是空的"。
//
// 规则: 新值为 null/undefined 且旧值非空 → 保留旧值, 并记入 staleFields;
//       其余情况一律用新值(包括新值是真值 0 / '' / false —— 那些是真实读数)。
export function mergeSection(oldVal, res) {
  if (!res || typeof res !== 'object' || Array.isArray(res)) return { value: res, staleFields: [] }
  const base = (oldVal && typeof oldVal === 'object' && !Array.isArray(oldVal)) ? { ...oldVal } : {}
  const staleFields = []
  for (const k of Object.keys(res)) {
    if (res[k] == null && base[k] != null) {
      staleFields.push(k)
      continue
    }
    base[k] = res[k]
  }
  return { value: base, staleFields }
}
