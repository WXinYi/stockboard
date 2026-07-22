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
      const na = typeof va === 'number' ? va : (parseFloat(va) || 0)
      const nb = typeof vb === 'number' ? vb : (parseFloat(vb) || 0)
      const cmp = na - nb
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
