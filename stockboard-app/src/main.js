import { createApp } from 'vue'
import { registerSW } from 'virtual:pwa-register'
import './style.css'
import App from './App.vue'
import router from './router.js'

createApp(App).use(router).mount('#app')

// PWA 新版本提示: 检测到新 SW 处于 waiting 状态时弹条, 点击后 SKIP_WAITING 并自动刷新
// (手机浏览器等待 SW 常驻不改管, 靠用户"多刷几次"不可靠 → 改为显式按钮)
registerSW({
  onNeedRefresh() {
    if (document.getElementById('sw-update-bar')) return
    const bar = document.createElement('div')
    bar.id = 'sw-update-bar'
    bar.style.cssText = 'position:fixed;top:8px;left:50%;transform:translateX(-50%);z-index:9999;' +
      'display:flex;align-items:center;gap:10px;background:#2b3a55;color:#fff;padding:8px 14px;' +
      'border-radius:10px;font-size:13px;box-shadow:0 4px 14px rgba(0,0,0,.25)'
    bar.innerHTML = '<span>🔄 发现新版本</span>'
    const btn = document.createElement('button')
    btn.textContent = '立即更新'
    btn.style.cssText = 'background:#ffd166;color:#2b3a55;border:0;border-radius:7px;padding:4px 12px;font-weight:700'
    btn.addEventListener('click', () => {
      btn.textContent = '更新中…'
      updateSW(true)
    })
    bar.appendChild(btn)
    document.body.appendChild(bar)
  },
  onOfflineReady() {},
})

