import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  base: './',
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico'],
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
        globPatterns: ['**/*.{js,css,html,json,png,svg,ico}'],
        maximumFileSizeToCacheInBytes: 3 * 1024 * 1024,  // summary.json ~2.5MB
        runtimeCaching: [
          {
            urlPattern: /\/data\/.*\.json$/,
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'stock-data',
              expiration: { maxAgeSeconds: 60 * 30 }
            }
          }
        ]
      }
    })
  ],
})
