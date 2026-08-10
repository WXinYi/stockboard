# StockDetailPage 图表 Canvas 自绘设计文档

> 日期: 2026-08-10 · 状态: 已确认设计 · 上一阶段: [StockDetailPage 全面升级 spec](2026-08-10-stock-detail-page-upgrade-design.md)

## 1. 背景与目标

当前详情页图表用 **lightweight-charts v5.2.0**(`StockDetailPage.vue` 中 `ensureChart`/`renderSeries`/`addSubIndicator` 等)。用户反馈三个痛点:

1. **不灵活** — 高度由 `chartH`(宽×0.62 clamp 220~420)硬编码,与右侧盘口列不对齐;三区比例、光标样式、轴标签均难定制。
2. **样式丑** — 与设计稿(`full-page-real.html` 的三区白底图表、左右双 Y 轴、底部时间轴)不匹配。
3. **交互受限** — 分时高度低;K 线历史平移靠内置逻辑,光标联动靠订阅回调。

**决策(用户已确认)**: 改用 **Canvas 2D 自绘**,移除 lightweight-charts 依赖,实现与右侧盘口列**高度对齐**、**像素级复刻设计稿视觉**、保留**光标十字线 + 指标切换 + K 线左右平移**交互。

**必须保留**(用户明确要求): **缠论** 与 **波浪** 指标叠加(它们是主图核心叠加层,自绘必须完整支持)。

## 2. 范围

### 2.1 做

| # | 内容 | 说明 |
|---|------|------|
| 1 | 新建 `StockChartCanvas.vue` | Canvas 2D 图表组件,替换 `sd-chart` 区域 |
| 2 | 新建 `chartDraw.js` | 纯几何/布局函数模块(可单测,见 §6) |
| 3 | 高度对齐盘口 | ResizeObserver 测量,图表区填满 `.sd-chart-row` 剩余高度 |
| 4 | 分时视图自绘 | 分时线+均价线+昨收/涨跌停参考线+左右双 Y 轴(左涨跌幅度%/右涨停-跌停价)+底部时间轴 |
| 5 | K 线视图自绘 | K 线蜡烛+MA/BOLL+缠论+波浪;量能柱 |
| 6 | 副图指标 | MACD/KDJ/RSI/WR(现有 `subInds`,无 CCI/OBV) |
| 7 | 光标交互 | 十字线 + 顶部 16 格联动 + 悬浮小卡(现 `cursorTip` 逻辑保留) |
| 8 | K 线平移 | 非分时视图支持拖拽/滚轮左右平移查看历史 |
| 9 | 移除 lightweight-charts | 删除依赖与全部图表渲染函数 |
| 10 | 测试 | `chartDraw.js` 纯函数 vitest 单测 + build 验证 |

### 2.2 不做(明确排除)

- **缩放**: 不做鼠标滚轮缩放/捏合缩放(K 线仅平移)。
- **分时平移**: 分时视图固定全天 09:30~15:00,不平移。
- **CCI/OBV 副图**: `subInds` 保持 `['none','macd','kdj','rsi','wr']` 现状;`indCache.cci`/`indCache.obv` 的遗留引用随 lightweight-charts 代码一并删除。
- **触摸双指缩放**: 保留 `touch-action: pan-y`,垂直手势交还页面滚动,水平拖动平移。
- **图表编辑/自定义指标**: 无。

## 3. 架构

```
┌─────────────────────────────────────────────────┐
│ StockDetailPage.vue (宿主)                       │
│  ├─ overlays.ma/boll · chan · wave · subInd 开关 │
│  ├─ kline/trend/quote/indCache (computeIndicators)│
│  └─ crossInfo/cursorTip (光标联动, 保留现有逻辑)  │
└───────────────┬─────────────────────────────────┘
                │ props: view/kline/trend/overlays/chan/wave/subInd/quote
                │ emit:  crossinfo (替换旧 subscribeCrosshairMove 回调)
                ▼
┌─────────────────────────────────────────────────┐
│ StockChartCanvas.vue   (新组件)                  │
│  ├─ ResizeObserver → 匹配 .sd-chart-row 高度      │
│  ├─ canvas 2D dpr 缩放绘制                         │
│  ├─ pointer/mouse 事件 → 十字线 + 平移 + emit     │
│  └─ 调用 chartDraw.js 纯函数做几何计算             │
└───────────────┬─────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────┐
│ chartDraw.js  (纯函数, 无 DOM/无状态, 可单测)     │
│  panelRects / priceToY / klineWindow / idxToX /   │
│  timeTicks / priceTicks / tickFormat              │
└─────────────────────────────────────────────────┘
```

**职责边界**:
- `StockChartCanvas.vue`: 持有 canvas、DPR 处理、事件、绘制调度、渲染循环。**不含任何业务计算**——所有指标数据由宿主 `computeIndicators()` 计算后经 props 传入。
- `chartDraw.js`: 纯函数,输入数值输出几何坐标/刻度,不碰 DOM。这是可测试单元,也是实现自绘的数学基础。

**数据流(与现有 `computeIndicators` 完全兼容)**:
宿主 `computeIndicators()` 产出 `indCache`,目前结构(已确认):
```
indCache = {
  ma: {n:[...]}, boll: {up, mid, lo}, volma: {n:[...]},
  macd: {dif, dea, hist}, kdj: {k, d, j}, rsi: {n:[...]}, wr: {n:[...]},
  fractals: [{i, type:1|-1}], bis: [{from:{i,type}, to:{i,type}}],
  zhongshu: [{zg, zd, from, to}], chanSignals: [{i, type:'1buy'|'2buy'|'3buy'|'1sell'|'2sell'|'3sell'}],
  waves: {status:'ok'|'ok5'|'unknown', waves:[{i, type, label}], dir},
  divergences: [{i, type:'top'|'bottom'}],
  fractalAt: Map(i→type), signalAt: Map(i→type),
}
```
> `indCache` 依赖的所有 `calcXxx` 函数在 `src/utils/indicators.js` 已存在,本次不修改它们的算法,只新增/调整调用方式。

## 4. 组件接口

### 4.1 Props

| Prop | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `view` | String | ✓ | `'trend'\|'day'\|'week'\|'month'\|'m60'`;`isIntraday` = trend/m60 |
| `kline` | Array | 视 view | K 线数据 `[{time, open, close, high, low, volume}]`(view≠trend 时) |
| `trend` | Array | 视 view | 分时数据 `[{time, price, vol, amount}]`(view=trend 时) |
| `quote` | Object | ✓ | `{prevClose, upPx, downPx, price, name, code}` 等;驱动分时 Y 轴(涨停板边界)/参考线 |
| `overlays` | Object | ✓ | `{ma: Boolean, boll: Boolean}` |
| `chan` | Boolean | ✓ | 缠论叠加开关 |
| `wave` | Boolean | ✓ | 波浪叠加开关 |
| `subInd` | String | ✓ | `'none'\|'macd'\|'kdj'\|'rsi'\|'wr'` |
| `indCache` | Object | ✓ | 宿主 `computeIndicators()` 结果(§3) |
| `isTradingDay` | Boolean | 否 | 分时 Y 轴锁定用;缺省读 `quote` 推导 |

### 4.2 Emit

| Event | Payload | 触发 |
|-------|---------|------|
| `crossinfo` | `Object\|null` | 光标移动/移出: 返回与现有 `onCrosshair` 完全同构的对象 `{open, high, low, close, prevClose, amount, chg, chgPct}`(K 线视图光标处该 K 线;分时/移出 → null) |

### 4.3 宿主接线(StockDetailPage.vue)

- 模板: 将 `<div ref="chartEl" class="sd-chart" :style="{height: chartH+'px'}">` 替换为 `<StockChartCanvas v-bind="..." @crossinfo="onCrosshairFromCanvas">`。
- `onCrosshairFromCanvas(info)`: 调用现有 `crossInfo`/`cursorTip` 赋值逻辑(仅需适配: 原回调参数来自 lightweight-charts,新回调直接收到 `{open,high,low,close,prevClose,amount,chg,chgPct,time}`)。`cursorTip` 的 `x/y` 定位保留在宿主(需传 canvas 相对坐标,组件 emit 时附带 `point:{x,y}`)。
- **删除** 的函数: `ensureChart`/`renderSeries`/`addMaLines`/`addBollLines`/`addVolBars`/`addTrendSeries`/`addSubIndicator`/`addTrendSubIndicator`/`setPaneStretch`/`recomputeChartHeight`/`onResize`/`removeAllSeries`/`track`/`linePoints` 及 `chart`/`series`/`markerPlugins` 状态。
- **保留** 的逻辑: `computeIndicators()`(产出 `indCache`)、`onCrosshair` 的字段计算(改造为从 emit payload 读)、`cursorTip`/`tipDate`、`isUp`/`upColor`、`fmt`/`pct`/`wan`/`fmtVol`/`fmtHand`、顶部 16 格绑定、`subInd`/`overlays`/`chan`/`wave` 响应式绑定。
- `waveNote`: 组件不负责,宿主继续由 `watch(indCache.waves)` 计算展示。

## 5. 布局与高度

### 5.1 容器

```
.sd-chart-row  (flex, align-items: stretch)   ← 现状
├── .sd-chart-wrap  (flex:1, min-width:0, position:relative)  ← 现状
│    └── <StockChartCanvas />                 ← 新组件, 根元素 position:absolute 或高度 100%
└── .sd-pankou  (flex:none, width:180/116)    ← 现状
```

**高度对齐机制**:
1. `.sd-chart-wrap` 与 `.sd-pankou` 已在 `align-items: stretch` 的 flex 行中,高度由**较高者**决定。
2. `StockChartCanvas` 根元素 `height:100%`(相对 `.sd-chart-wrap`),内部 `ResizeObserver` 观察自身 `clientWidth`/`clientHeight`,实时重绘。
3. **空态保底**: 数据未到时 `.sd-chart-wrap` 高度塌缩 → 组件根元素设 `min-height:360px`(桌面 `≥481px`)/`280px`(移动),保证骨架/占位不跳变。

### 5.2 三区比例

图表内容 = 主图 : 量能 : 副图 = **3 : 1 : 1**(与设计稿 `dc-main/dc-vol/dc-sub` 的 `flex-grow 3/1/1` 一致)。

- `subInd === 'none'`: 无副图 → 主图 : 量 = **2.2 : 1**(复用现有 `setPaneStretch` 逻辑的等价布局)。
- 每区之间 gap **2px**(设计稿 `margin-top:2px`),区与区之间独立 `#eef1f5` 1px 边框 + `radius 4px`(设计稿 `.dc-main/.dc-vol/.dc-sub` 边框样式)。
- 副图区背景 `#fafbfd`(设计稿 `.dc-sub`),主图/量能区背景 `#fff`。

## 6. 坐标系统与纯函数 (chartDraw.js)

所有几何计算集中在 `src/utils/chartDraw.js`,纯函数、无 DOM、导出可测。**坐标系**: 内容区左上角为 (0,0),y 向下增大。

### 6.1 `panelRects(w, h, sub)` → `{ main, vol, sub }`

三区矩形。输入画布宽高与是否有副图,输出各区的 `{x, y, width, height}`。
- 每区包含 1px 边框(`#eef1f5`)与 4px 圆角(圆角仅渲染,几何按直角矩形)。
- 区间距 2px。
- `sub=false` 时 `main:vol = 2.2:1`,`sub=null`;`sub=true` 时 `3:1:1`。
- 高度按 `h - 间隔` 分配,向下取整;总高度不超过 `h`。
- **分时视图**: 主图区左右各预留轴带 —— 左轴(百分比)占 `leftGutter≈32px`,右轴(价格)占 `rightGutter≈40px`;K 线视图仅在右侧留 `rightGutter≈40px`。轴带为内容区之外的**绝对保留区**,不参与蜡烛/线绘制(仅画刻度标签)。

### 6.2 `priceToY(price, min, max, rect)` → `y`

将价格线性映射到区矩形内 y 坐标(上下各留 4% padding 之外由调用方传入的 min/max 已含 padding)。

### 6.3 `klineWindow(kline, count, offset)` → `{ window, offset }`

K 线可见窗口。`count` = 可视 K 线数,`offset` = 右端在数据中的偏移(0 = 显示最新 N 根)。
- `offset=0`: 返回末尾 `count` 根。
- 平移: offset 增大 → 回看更早;`clamp(offset, 0, max(0, len-count))`。
- 返回 `window`(可见数组)与原 offset(经 clamp)。**历史平移的状态归宿主或组件内部,纯函数只算窗口。**

### 6.4 `idxToX(i, w, count)` → `x`

窗口内第 `i` 根 K 线的 x 中心。`x = (i + 0.5) / count * w`。用于 K 线/量/副图/缠论波浪标注。

### 6.5 `timeTicks(items, w, isIntraday)` → `[{x, label}]`

底部时间轴刻度。**X 轴固定全天交易时间**(分时视图: 横轴固定 09:30~15:00,不平移、不缩放,数据只画到当前时刻)。
- **分时** (`isIntraday && view==='trend'`): 固定 5 个刻度 `09:30 | 10:30 | 11:30/13:00 | 14:00 | 15:00`,等分宽;`11:30/13:00` 为午休合并标签;午休(11:30~13:00)在轴上不额外留空(时间轴按等分渲染)。
- **m60/日/周/月**: 按 `items` 的时间均匀取 4~6 个刻度(首/末必含),标签格式分时 `HH:MM`、日/周/月 `MM-DD`。
- x 为内容区等分位置,label 居中或左右对齐避免溢出。

### 6.6 `priceTicks`(分时左右双轴) 与 `priceTicks(min, max, rect)`(K线)

**分时视图: 左右双 Y 轴**(用户要求)。

- **左轴 — 涨跌幅度百分比**: 5 等分,标签 `+10% / +5% / 0% / -5% / -10%`(百分比按板块涨跌停幅度算,支持主板 ±10%、创业板/科创板 ±20%、ST ±5%、北交所 ±30% —— 由 `(upPx-prevClose)/prevClose` 推导,不硬编码 ±10%);昨收 = 0% 居中。标签渲染在主图区**左侧**。
- **右轴 — 价格**: 上界 = 涨停价 `quote.upPx`(**最大**)、下界 = 跌停价 `quote.downPx`(**最低**)、昨收居中;标签渲染在主图区**右侧**,与左轴百分比**一一对应同 y**。
- 两轴等分数一致(5 格),每行 `左: +x% | 右: 价格`,y 坐标相同。
- 数据缺失回退: 无 `upPx/downPx` 时用 `prevClose×(1±10%)` 推导双轴。

**K 线视图**: `priceTicks(min, max, rect)` 按窗口实际 min/max 5 等分,右侧仅显示价格。

### 6.7 `tickFormat` 辅助

数字 → 紧凑格式(如成交额 `30.62亿`、量 `34万手`),复用宿主 `wan`/`fmtVol` 现有函数(直接 import,不重复实现)。

### 6.8 可测性

| 函数 | 单测要点 |
|------|---------|
| `panelRects` | 三区高度比 3:1:1 / 2.2:1;区间距 2;总高不越界 |
| `priceToY` | min→rect.bottom, max→rect.top, 线性插值 |
| `klineWindow` | 末段窗口;offset clamp 上下界 |
| `idxToX` | i=0→x≈w/count/2; i=count-1→x≈w-w/count/2 |
| `timeTicks` | 分时 5 固定刻度(09:30~15:00 全天);K 线含首末;间隔均匀 |
| `priceTicks` | 分时左右双轴: 左轴百分比 +x% 同 y 对应右轴价格,上界涨停 `upPx` / 下界跌停 `downPx` / 昨收居中;K 线按实际范围 |

## 7. 绘制规范(像素级对齐设计稿)

### 7.1 画布与 DPR

- canvas 物理尺寸 = `clientWidth × dpr`、`clientHeight × dpr`,`ctx.setTransform(dpr,0,0,dpr,0,0)`。
- 字体: `10px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif`(图表内 10px,轴标签 9px)。
- `font-variant-numeric: tabular-nums`(宿主已有)。

### 7.2 颜色 token

| Token | 值 | 用途 |
|-------|-----|------|
| `UP` | `#e74c3c` | 涨(蜡烛/分时线/量柱/买) |
| `DOWN` | `#27ae60` | 跌(蜡烛/量柱/卖) |
| `ACCENT` | `#2980b9` | 十字线/副图 label/光标卡主色 |
| `AVG` | `#f2a900` | 分时均价线 / MA5 / MACD DIF |
| `SIGNAL_PURPLE` | `#9b59b6` | MACD DEA / MA10 |
| `CHAN` | `#7d3c98` | 缠论标注(笔/中枢/买卖点文字) |
| `WAVE` | `#d35400` | 波浪标注(数浪标签/连线) |
| `MA20` | `#27ae60` | MA20 |
| `MA60` | `#2980b9` | MA60 |
| `BOLL_UP` | `#e74c3c` | BOLL 上轨 |
| `BOLL_MID` | `#f39c12` | BOLL 中轨 |
| `BOLL_LO` | `#27ae60` | BOLL 下轨 |
| `GRID` | `#f2f4f7` | 网格线 |
| `PRE_CLOSE` | `#b9c2cc` | 昨收虚线 |
| `BORDER` | `#eef1f5` | 三区边框/分隔线 |
| `AXIS_TEXT` | `#9aa2ac` | Y 轴刻度文字 |
| `TIME_TEXT` | `#b3bac3` | 底部时间文字 |
| `VOL_UP` | `rgba(231,76,60,.6)` | 涨量柱 |
| `VOL_DOWN` | `rgba(39,174,96,.6)` | 跌量柱 |
| `MACD_HIST_UP` | `rgba(231,76,60,.5)` | MACD 红柱 |
| `MACD_HIST_DOWN` | `rgba(39,174,96,.5)` | MACD 绿柱 |

### 7.3 分时视图 (`view='trend'`)

**主图区**:
1. 网格: 4 条水平线(25%/50%/75% 及上下边沿),`GRID` 1px。
2. **昨收线**: 水平虚线 `PRE_CLOSE` 于 0% 位置(y = 中央)。
3. **涨/跌停线**: 顶部/底部虚线 `rgba(231,76,60,.55)` / `rgba(39,174,96,.55)`,即 Y 轴上下边沿(涨停 `quote.upPx` / 跌停 `quote.downPx`)。
4. **Y 轴固定涨停板(左右双轴)**: 左轴 = 涨跌幅度百分比(5 等分 `+10%/+5%/0%/-5%/-10%`,按板块幅度),右轴 = 价格(**上界涨停 `quote.upPx`、下界跌停 `quote.downPx`**,昨收居中),两轴同 y 一一对应。无 `upPx/downPx` 数据时回退 `prevClose×(1±10%)`。
5. **分时线**: `UP` `#e74c3c`,线宽 1.2px,连接各 `trend[i].price` 点。
6. **均价线**: `AVG` `#f2a900`,线宽 1px。均价 = `Σamount / (Σvol×100)` 累计值(逐点累计计算)。
7. **区顶标签**: 左上角 `分时 ─ 均价`(设计稿 `.cd-ma`,`均价` 字色 `AVG`)。
8. **X 轴固定全天**: 横轴固定 09:30~15:00 全天(不平移、不缩放),数据只画到当前时刻(`trend` 数据到哪画到哪);分时 Y 轴范围不受数据范围影响,始终是涨停板边界。

**量能区**: 每个分时点的成交量柱,红涨绿跌,`VOL_UP/VOL_DOWN`。

**底部时间轴**: 固定 `09:30 | 10:30 | 11:30/13:00 | 14:00 | 15:00`(`TIME_TEXT`)。

### 7.4 K 线视图 (view = day/week/month/m60)

**主图区**:
1. 网格: 4 条水平线,`GRID`。
2. **K 线蜡烛**: 每根 `open/close/high/low`,涨红跌绿;实体宽 = 柱宽×0.6(柱宽 = `w/count`),空心/实心均可但统一;影线 1px。
3. **MA 叠加**(`overlays.ma`): MA5 `AVG` / MA10 `SIGNAL_PURPLE` / MA20 `MA20` / MA60 `MA60`,线宽 1px,画在整个窗口范围(计算值已有)。
4. **BOLL 叠加**(`overlays.boll`): 上轨 `BOLL_UP` / 中轨 `BOLL_MID` / 下轨 `BOLL_LO`,线宽 1px,`BOLL_MID` 虚线(线型区分)。
5. **Y 轴**: 按窗口内 min/max 5 等分,右侧显示价格(`AXIS_TEXT`)。
6. **区顶标签**: 动态显示当前叠加(`MA5:xx MA10:xx ...` 各线最新值,字色随线色),与设计稿 `.cd-ma` 一致。

**缠论叠加**(`chan=true`,仅日/周/月;m60 不画缠论):
1. **分型**: 顶分型 → 向上小三角 `CHAN`(位于该 K 线高点上方);底分型 → 向下小三角(低点下方)。位置来自 `indCache.fractals`。
2. **笔 (bis)**: 相邻有效分型连线,`CHAN` 1px 实线,连接 `from → to` 的 K 线收/高/低锚点(取分型 K 线的价格位)。
3. **中枢 (zhongshu)**: 每个中枢画矩形框(上沿 `zg`、下沿 `zd`,水平范围 `from→to`),`CHAN` 半透明填充(`rgba(125,60,152,.15)`) + 1px 描边。只画最近 `CHAN_MAX_ZHONGSHU=10` 个。
4. **三类买卖点**: 在对应 K 线 `i` 处标文字 `1买/2买/3买/1卖/2卖/3卖`(`SIGNAL_LABELS`),买点 `CHAN`、卖点 `DOWN`;位置在 K 线低点下方(买)/高点上方(卖)。
5. **背驰**: `indCache.divergences` 中 `type:'bottom'` → 低点下方标 `底背驰`;`type:'top'` → 高点上方标 `顶背驰`,字色 `CHAN`。

**波浪叠加**(`wave=true`,仅日/周/月;m60 不画波浪):
1. 依次连接 `indCache.waves.waves` 各标注点,`WAVE` 1px 线。
2. 每个 `label` 标注在转折点旁(起点起浪、1~5 推进浪、A/B/C 调整浪),`WAVE` 文字。
3. `status='ok'/'ok5'`: 画完整数浪;`status='unknown'`: 只画已识别部分 + 宿主 `waveNote` 明示"无法判定"。
4. 标注位置: 推进浪 label 在 K 线上方,调整浪 label 在下方,避免与缠论文字重叠。

**量能区**: 每根 K 线成交量柱,红涨绿跌;叠加 `volma` MA5/MA10 线(色随 `AVG/SIGNAL_PURPLE`)。

### 7.5 副图指标 (subInd ≠ none)

| subInd | 绘制 | 说明 |
|--------|------|------|
| `macd` | DIF 线 `AVG`、DEA 线 `SIGNAL_PURPLE`、hist 柱红绿 | 柱宽同量柱;零轴网格 |
| `kdj` | K 线 `AVG`、D 线 `SIGNAL_PURPLE`、J 线 `WAVE` | 波动大,J 线可出界,按 0~100 映射 |
| `rsi` | RSI6 `AVG` / RSI12 `SIGNAL_PURPLE` / RSI24 `MA20`,0~100 映射,画 30/50/70 参考虚线 | `indCache.rsi.n` 结构 |
| `wr` | WR 线 1~2 条,0~-100 反向(上方 0,下方 -100),画 -20/-80 参考虚线 | 反向轴 |

- 副图区左上角 label: `MACD ▾`(字色 `ACCENT`,背景 `rgba(255,255,255,.75)`,设计稿 `.cd-sublabel`)。下拉切换按钮 `subInd` 保持宿主 `<select>`,视觉上叠加在副图区左上角(现状定位逻辑保留)。
- 副图 Y 轴: 指标有固定区间(KDJ/RSI 0~100,WR 0~-100)则固定;MACD 按窗口极值对称。

### 7.6 十字光标与悬浮卡

1. **十字线**: 竖线 `rgba(41,128,185,.45)` 1px 虚线贯穿三区 + 横线贯穿主图;交点小圆点标记该 K 线价格。
2. **emit `crossinfo`**: 光标所在 K 线(分时则为 null)完整字段 + `point:{x,y}`(canvas 相对坐标,供宿主定位 `cursorTip`)。
3. **宿主 `cursorTip`**: 现有小卡(开/高/低/收/涨跌/量)保留,定位逻辑不变,坐标来自 emit 的 `point`。

### 7.7 手势

| 手势 | 行为 |
|------|------|
| 鼠标移动 | 十字线 + emit `crossinfo`;移出画布 → 清除十字线 + emit null |
| 鼠标按住拖动 (K线视图) | 水平平移窗口(offset 增减);垂直忽略 |
| 滚轮 (K线视图) | 水平平移(每次 ±3 根,`deltaY` 符号映射) |
| 滚轮 (分时) | 忽略 |
| 触摸单指水平拖动 | 平移(K线);分时忽略 |
| 触摸单指垂直 | 交还页面滚动(`touch-action: pan-y`,根元素) |

## 8. 移除项

| 文件 | 移除内容 |
|------|---------|
| `package.json` | `lightweight-charts` 依赖(`^5.2.0`) |
| `StockDetailPage.vue` | `chartEl`/`chart`/`series`/`markerPlugins` 状态;`ensureChart`/`renderSeries`/`addMaLines`/`addBollLines`/`addVolBars`/`addTrendSeries`/`addSubIndicator`/`addTrendSubIndicator`/`setPaneStretch`/`recomputeChartHeight`/`onResize`/`removeAllSeries`/`track`/`linePoints`;`chartH`/`VIEW_MAX_BARS`/`MA_COLORS`/`BOLL_COLORS`/`SIGNAL_LABELS`/`CHAN_MAX_ZHONGSHU`(移至 chartDraw 或组件内);`.sd-chart` 内联 height 样式与相关 CSS 注释 |
| `StockDetailPage.vue` | `indCache.cci`/`indCache.obv` 遗留引用(若存在,随旧渲染代码删除) |

> `indCache` 计算函数(`calcMACD` 等)在 `src/utils/indicators.js`,**保留不动**。

## 9. 测试策略

- **单元测试** (vitest): `src/utils/__tests__/chartDraw.test.js`,覆盖 §6.8 全部函数 + 边界(空数组/单根/超长平移)。
- **构建验证**: `npm run build` 必须通过(移除 lightweight-charts 后无编译错误)。
- **手工验证清单**(用户在浏览器确认):
  - 分时视图: 分时线红、均价黄、昨收虚线、左轴涨跌幅度百分比 + 右轴价格(上界涨停/下界跌停,创业板 ±20% 正确,两轴同 y)、X 轴固定全天(09:30~15:00,只画到当前时刻)、5 时间刻度。
  - 日 K: 蜡烛红绿、MA4 线、BOLL 开关、缠论(分型/笔/中枢/买卖点/背驰)、波浪(数浪)。
  - 光标: 十字线 + 顶部 16 格联动 + 悬浮卡。
  - K 线平移: 拖拽/滚轮回看历史,clamp 到最左。
  - 副图: MACD/KDJ/RSI/WR 切换,`none` 时主图:量 = 2.2:1。
  - 高度: 与右侧盘口列底部对齐;移动端/桌面 min-height 保底。
  - 分时不平移,滚轮/拖动无响应。

## 10. 迁移步骤(实施计划将细化)

1. 新增 `chartDraw.js` + 单测(TDD,先红后绿)。
2. 新增 `StockChartCanvas.vue`,先实现分时视图。
3. 实现 K 线视图 + 缠论 + 波浪 + 量能。
4. 实现副图指标 + 光标 + 平移交互。
5. 接线宿主: 替换模板、删除 lightweight-charts 代码、改 `onCrosshair` 适配。
6. 移除依赖、build、手工验证。

每步独立可提交(TDD 节奏,见 writing-plans 阶段)。

## 11. 风险与注意

- **缠论/波浪与 K 线文字重叠**: 买卖点/数浪文字 + 悬浮卡可能挤占;用"买点下方/卖点上方/推进浪上方/调整浪下方"分侧避开,必要时跳过多余标注(最近 N 个)。
- **分时均价累计**: 需逐点累计 `Σamount/(Σvol×100)`,注意 `amount` 单位(元)、`vol` 单位(手=100股)。
- **量能柱宽度**: 数据密集时柱宽 < 1px,需 clamp 最小 1px 或按 dpr 对齐半像素避免闪烁。
- **高 DPI**: 所有尺寸计算基于 CSS 像素,canvas 用 dpr 放大,文字/线宽需随 `ctx.scale` 归一,避免模糊。
