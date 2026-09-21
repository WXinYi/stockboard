# stockboard-app — StockBoard 前端

Vue 3 + Vite + PWA 移动端看板，生产地址 https://WXinYi.github.io/stockboard/ 。
数据全部来自 `public/data/latest/` 下的静态 JSON（由 `jiarenmens/` 采集管道导出，见 `docs/DATA_PIPELINE.md`）；个股行情/K 线由浏览器直连东财/腾讯，KPL 数据经 SCF/Worker 代理直连（见根目录 `CLAUDE.md` §6.3）。

```bash
npm ci
npm run dev      # 本地开发
npm run test     # vitest 单测（含 sixParity 对拍回归）
npm run build    # 生产构建（CI 里由 crawl.yml 执行）
```

技术要点：`<script setup>`、无 TypeScript/Pinia（`provide/inject` + composables）、图表 Canvas 自绘、24 组件 + 9 composables + 16 utils。
