# 股票详情弹窗:走势 + 基本信息 + 分时 + 资金 + F10

日期: 2026-08-08

## 背景

看板现有股票名称交互:
- StockTab 重仓共识表: 单击股票名 → 复制代码
- PlayerDetail 3 张表(当前持仓/推测持仓/调仓记录): 单击复制代码、长按打开东财 App

用户希望"点击股票名称可以查看股票的走势及以下基本信息",并进一步要求集成东财数据源富化功能。

## 关键约束

- 看板是 GitHub Pages **静态站,无后端**。运行时数据只能来自浏览器直连东财**公开接口**。
- **mx-data 妙想 skill 是代理侧工具**(`mkapi2.dfcfs.com` + 私有 API Key),不能进前端运行时;只用于开发期校验字段/口径。
- 东财 push2 系列接口支持 JSONP(`cb=` 参数),可绕 CORS。
- 已在 Git 环境验证过 shell 网络可能受限,**运行时网络路径是用户浏览器**,与开发 shell 无关。

## 已确认决策

| 决策 | 结论 |
|---|---|
| 数据源 | 浏览器直连东财 push2 JSONP(实时) |
| 范围 | 所有可点股票(重仓共识 + PlayerDetail 3 张表) |
| 点击行为 | 单击复制**保留**;股票名旁**另加 📈 图标**打开详情弹窗 |
| 图表方案 | lightweight-charts(~45KB,动态 import 懒加载) |
| 富化功能 | 分时图 + 主力资金流向 + F10 资料(不含 5 档盘口) |

## 架构与数据流

```
前端(GitHub Pages)                      东财公开接口(push2, JSONP)
─────────────                           ─────────────────────────
📈 图标 → StockDetailModal.vue           ├─ 行情 get      push2.eastmoney.com/api/qt/stock/get
    │                                    ├─ K线 get       push2his.eastmoney.com/api/qt/stock/kline/get
    ├─ fetchQuote ─────────────────────►│     klt=101日/102周/103月, fqt=前复权, 120根
    ├─ fetchKline ─────────────────────►├─ 分时 trends2  push2his.eastmoney.com/api/qt/stock/trends2/get
    ├─ fetchTrend ─────────────────────►│     ndays=1
    ├─ fetchFundFlow ──────────────────►├─ 资金 fflow    push2.eastmoney.com/api/qt/stock/fflow/kline/get
    └─ fetchF10 ───────────────────────►└─ 资料 datacenter/emweb (见风险)
```

### JSONP 要点
- `<script>` 标签注入,`cb=回调名` 参数;回调完成后清理 script 标签与全局函数。
- 每请求 **8s 超时** + onerror → 弹窗内显示"数据获取失败" + 重试按钮。
- 防重入:同 code 同类型请求进行中不重复发。

## 新增文件

### 1. `src/utils/eastmoney.js`
东财接口统一封装(从 PlayerDetail 抽出深链/市场判定,消除重复):

| 函数 | 说明 |
|---|---|
| `emMarket(code)` | 市场判定: 0深/1沪/2北交所/116港股 |
| `secid(code)` | `${emMarket(code)}.${code}` |
| `jsonp(url, cbParam)` | JSONP 助手(8s 超时,错误回调) |
| `fetchQuote(code)` | 实时行情: f57代码 f58名称 f43现价 f44高 f45低 f46开 f60昨收 f47量 f48额 f168换手 f116总市值 f117流通 f162PE动 f167PB f169涨跌额 f170涨跌幅 |
| `fetchKline(code, klt)` | 日/周/月 K线;klines: `"date,open,close,high,low,volume,amount"` |
| `fetchTrend(code)` | 当日分时 trends2,ndays=1 |
| `fetchFundFlow(code)` | 近5日资金流: 主力/超大/大单/中单/小单净流入(fflow/kline) |
| `fetchF10(code)` | 公司简介/主营/概念/行业(见风险节) |
| `emNativeUrl`/`emUniversalUrl`/`openEmApp` | 深链(原 PlayerDetail,搬移共用) |

### 2. `src/components/StockDetailModal.vue`
自包含弹窗组件,props: `code`(必填)、`name`(可选,缺则从行情回填)、`visible`(v-model)。

```
┌ 紫光股份  000938                        [×]
│  +2.35  +8.67%        (现价 红涨绿跌,红=涨 #e74c3c 绿=跌 #27ae60)
│ 今开·昨收·最高·最低·量·额·换手·PE·PB·市值   ← 基础信息条(横向滚动)
│ [走势] [分时] [资金] [资料]                ← Tab 栏
│  走势Tab: 日K/周K/月K 蜡烛+MA5/10/20+量    ← lightweight-charts
│  分时Tab: 分时线+均价线
│  资金Tab: 近5日 主力/超大/大单/中单/小单 净流入(正红负绿)
│  资料Tab: 公司简介·主营业务·所属概念·行业
│ [复制代码] [打开东方财富]
```

- 打开弹窗 → 并行 `fetchQuote` + `fetchKline('101')` 立即出首屏。
- 切 Tab 按需取 分时/资金/F10;切周期(日/周/月)重取 K线。
- `lightweight-charts` **动态 import**: `const { createChart } = await import('lightweight-charts')`,仅在弹窗首次打开时下载。
- 移动端: 全屏遮罩弹层,图表区自适应高度;点遮罩/× 关闭。
- 现价颜色复用项目约定(红涨绿跌)。

## 修改文件

| 文件 | 改动 |
|---|---|
| `src/components/StockTab.vue` | 重仓共识表股票名旁加 📈 图标 → 打开弹窗 |
| `src/components/PlayerDetail.vue` | 3 张表股票名旁加 📈;深链/market 函数改为 import utils |
| `package.json` | 新增 `lightweight-charts` |

## 错误处理

- 每接口 8s 超时/onerror → Tab 内"加载失败"+ 重试。
- 港股/北交所部分接口缺数据 → 显示 `—`,不阻断弹窗。
- 全部接口失败时弹窗仍可关,不白屏。

## 验证(先本地,不直接 push)

1. **Playwright 真实浏览器**(用户浏览器同款网络路径): 打开弹窗 → 断言行情/K线/分时/资金/F10 数据加载、图表渲染、周期切换、Tab 切换。
2. **每接口确认 JSONP 无 CORS 拦截**(浏览器 console 无 CORS 报错)。
3. `npm run build` 通过;确认 lightweight-charts 被代码分割(首屏 bundle 不含它)。
4. 手工: StockTab + PlayerDetail 点 📈 弹窗;复制/长按原交互不受影响。

## 风险与回退

| 风险 | 回退 |
|---|---|
| F10(datacenter-web/emweb)不支持 JSONP/CORS | 该 Tab 显示"暂不支持",或改爬虫预抓静态 JSON(B 方案) |
| 港股(5位)部分接口数据缺失 | 显示 `—` |
| lightweight-charts 与 PWA 缓存兼容问题 | 若异常,退回手写 Canvas 蜡烛图 |
