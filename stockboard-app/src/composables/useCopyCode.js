import { ref } from 'vue'

// 点击股票名称 → 复制代码到剪贴板。key 用于区分哪一行显示"已复制"反馈。
export function useCopyCode() {
  const copiedKey = ref('')
  let timer = null

  function copyStockCode(code, key = code) {
    const done = () => {
      copiedKey.value = key
      clearTimeout(timer)
      timer = setTimeout(() => { copiedKey.value = '' }, 1500)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(code).then(done).catch(() => {})
    } else {
      // 非安全上下文兜底（Pages 为 HTTPS，正常走不到这里）
      const ta = document.createElement('textarea')
      ta.value = code
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      try { document.execCommand('copy'); done() } catch (e) {}
      document.body.removeChild(ta)
    }
  }

  return { copiedKey, copyStockCode }
}
