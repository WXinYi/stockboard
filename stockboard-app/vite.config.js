import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

// KPL 接口对浏览器 UA 风控(返回空 List), curl/服务端请求正常 → dev 走本机代理转发并覆盖 UA
// 生产(GitHub Pages 静态)无代理, 需 Cloudflare Worker 之类中转(待定)
const KPL_TARGETS = {
  '/kpl-hq': 'https://apphwhq.longhuvip.com/w1/api/index.php',
  '/kpl-his': 'https://apphis.longhuvip.com/w1/api/index.php',
  '/kpl-art': 'https://apparticle.longhuvip.com/w1/api/index.php',
  '/kpl-sec': 'https://apphwshhq.kaipanhong.com/w1/api/index.php',
  '/kpl-lhb': 'https://applhb.longhuvip.com/w1/api/index.php',
}

export default defineConfig({
  base: './',
  server: {
    proxy: Object.fromEntries(Object.entries(KPL_TARGETS).map(([prefix, target]) => [
      prefix, {
        target,
        changeOrigin: true,
        rewrite: p => p.replace(new RegExp('^' + prefix), ''),
        configure: proxy => proxy.on('proxyReq', pr => {
          pr.setHeader('User-Agent', 'okhttp/3.12.1')   // 覆盖浏览器 UA 绕风控
          pr.removeHeader('Origin')                       // 移除引用头, 避免触发风控
          pr.removeHeader('Referer')
        }),
      },
    ])),
  },
  plugins: [
    vue(),
    VitePWA({
      registerType: 'prompt',
      includeAssets: ['icons.svg'],
      manifest: {
        name: 'StockBoard - 股票数据看板',
        short_name: 'StockBoard',
        description: '实时追踪高手持仓与调仓信号',
        theme_color: '#5b6daa',
        background_color: '#eef2f6',
        display: 'standalone',
        icons: [
          { src: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">📊</text></svg>', sizes: 'any', type: 'image/svg+xml' }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,png,svg,ico}'],
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,
        runtimeCaching: [
          {
            urlPattern: /\/data\/.*\.json$/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'stock-data',
              networkTimeoutSeconds: 10,
              expiration: { maxAgeSeconds: 60 * 60 * 24 }
            }
          }
        ]
      }
    })
  ],
})
