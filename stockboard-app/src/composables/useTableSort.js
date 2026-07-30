import { ref, computed } from 'vue'

export function useTableSort(dataRef, defaultKey = '') {
  const sortKey = ref(defaultKey)
  const sortDir = ref(defaultKey ? 'desc' : '') // 'desc' | 'asc' | ''

  function toggle(key) {
    if (sortKey.value === key) {
      // 同一列: desc → asc → 默认
      if (sortDir.value === 'desc') {
        sortDir.value = 'asc'
      } else if (sortDir.value === 'asc') {
        sortKey.value = ''
        sortDir.value = ''
      }
    } else {
      sortKey.value = key
      sortDir.value = 'desc'
    }
  }

  const sorted = computed(() => {
    const list = [...dataRef.value]
    if (!sortKey.value) return list
    list.sort((a, b) => {
      const va = a[sortKey.value]
      const vb = b[sortKey.value]
      let cmp
      if (typeof va === 'string' && typeof vb === 'string') {
        cmp = va.localeCompare(vb)
      } else {
        const na = typeof va === 'number' ? va : (parseFloat(va) || 0)
        const nb = typeof vb === 'number' ? vb : (parseFloat(vb) || 0)
        cmp = na - nb
      }
      // 二级排序: 同值用 _id 保证稳定
      if (cmp === 0 && a._id !== undefined && b._id !== undefined) {
        cmp = a._id - b._id
      }
      return sortDir.value === 'desc' ? -cmp : cmp
    })
    return list
  })

  function indicator(key) {
    if (sortKey.value !== key) return ''
    return sortDir.value === 'desc' ? ' ▾' : ' ▴'
  }

  return { sorted, sortKey, sortDir, toggle, indicator }
}
