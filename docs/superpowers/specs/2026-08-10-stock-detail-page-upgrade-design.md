# StockDetailPage 详情页补齐设计(功能 + 交互 + 图表视觉)

> 视觉锚点: `.superpowers/brainstorm/88960-1786340330/content/full-page-real.html`(桌面+移动完整页面设计稿, 真实数据渲染)
> 状态: **已获用户批准**(含缠论/波浪主图叠加的归属修正)
> 日期: 2026-08-10

---

## 1. 背景与目标

当前 [StockDetailPage.vue](../../../stockboard-app/src/components/StockDetailPage.vue) 已有: 报价 16 格、5 视图图表(分时/60分/日K/周K/月K)、MA/BOLL/缠论/波浪主图叠加、MACD/KDJ/RSI/WR 副屏、涨停原因、板块胶囊、资讯/F10、盘中轮询、十字光标 16 格联动。

对照主流行情软件(同花顺/东财/富途), 详情页缺少**短线核心数据**与**图表视觉规格**, 交互也缺关键反馈。本次按三层补齐:

- **P0 功能**: 盘口五档、大单监控、涨停基因、历史涨停、竞价分时、龙虎榜个股
- **图表视觉**: 方案 B 紧凑三栏(高度=图表宽×0.62, 主图:量:副图=3:1:1), 分时也显示副图
- **盘口布局**: 桌面右盘口 180px、移动右盘口 116px(用户已确认)
- **P1 交互**: 吸顶、悬浮图例 tooltip、代码复制、数据时效、骨架屏

---

## 2. 页面结构(自上而下)

参照设计稿 `full-page-real.html`, 桌面与移动共用同一信息架构, 仅响应式排布差异:

```
┌ 报价区: 名称/代码 + 现价·涨跌幅·涨跌额 + 今开/最高/最低/昨收/振幅/量/额/换手/量比/涨停/跌停
├ 图表区(方案B) ────────────────┬─ 右盘口
│ [分时|日K|周K|月K|60分][MA|BOLL|缠论|波浪][前复权]  │ 卖1-5(红)
│ 主图(3份): 分时线+均价 或 K线+MA/BOLL+缠/波标记  │ 现价高亮
│ 量能(1份): 红涨绿跌 + VOL5/10                     │ 买1-5(绿)
│ 副图(1份): MACD▾ 可切 KDJ/RSI/CCI/WR/OBV          │ 委比/委差/外盘/内盘
│ 十字光标联动 16 格                                  │ 涨停/跌停/换手/量比
├ 功能卡区(4列)
│ [竞价分时] [涨停基因] [大单监控] [龙虎榜个股]
├ 第二行卡区: [历史涨停表格] [资讯|研报|公告 + F10(公司/财务/股东/估值)]
├ 板块胶囊(所属板块, 强度红涨绿跌)
└ 底部 tab: 📰资讯 | 🏢基本面(现有, 保留)
```

**移动端差异**: 报价两行 + 图表/右盘口 **116px** + 功能卡**纵向堆叠**。

---

## 3. 图表区改造(方案B)

### 3.1 尺寸与比例(定稿)

| 项 | 桌面(≥768px) | 移动(≤480px) | 说明 |
|---|---|---|---|
| 图表区高度 | `min(max(图表区宽×0.62, 220), 420)` | `min(max(屏宽×0.62, 220), 280)` | 跟随容器宽度, 不再是固定 360px |
| 主图:量:副图 | 3:1:1 | 3:1:1 | 现 `setPaneStretch` 2.2/0.6/1.0 改为 **3/1/1** |
| 右盘口宽 | 180px | 116px | 设计稿确认 |

### 3.2 副屏指标(副图 pane 2)

- 现有 `subInds` 数组扩为: `none/macd/kdj/rsi/cci/wr/obv`(`addSubIndicator` 已有 cci/obv 分支, 仅缺选项入口)
- **分时视图也要副图, 且副图指标切换对分时/K线统一生效**: `subInd` 由"仅 K 线"提升为"两个视图共用"。
  - 分时视图: 当前 trend 分支硬编码画 MACD, 改为按 `subInd` 渲染(对 `trend.value` 价格序列算指标, 复用 `calcKDJ/calcRSI/calcWR` 等)。
  - 指标切换下拉 select 从 `v-if="isKline"` 改为常显(分时也显示)。
  - `none` 时两个视图均不画副图(pane 2 留空或隐藏)。

### 3.3 主图叠加(缠论/波浪保留)

MA/BOLL/缠论/波浪**全部保留**且不改变归属 —— 它们是主图叠加层, 不占副图栏位:
- 缠论: 分型箭头 + 三类买卖点 + 笔连线 + 中枢线 + 背离(紫色系 `#7d3c98`)
- 波浪: 1-2-3-4-5-A-B-C 浪型数字标签(顶红底绿) + 无法判定时 waveNote 明示

设计稿中主图按钮完整为 `MA | BOLL | 缠论 | 波浪`(桌面+移动同)。

### 3.4 图表区改动落点

[StockDetailPage.vue](../../../stockboard-app/src/components/StockDetailPage.vue) 中:
- `ensureChart()` 初始 `height: 360` → 改为响应式高度(计算函数 `chartHeight()`)
- `renderSeries()` 里 `chart.applyOptions({ height: 360 })` → 用同一计算
- `setPaneStretch()` 的 2.2/0.6/1.0 → 3/1/1(3 pane) / 2.2/1(2 pane 兜底)
- 外层 `.sd-chart` CSS 高度同步改为计算高度(注释已强调内外高度必须一致)
- 新增响应式: 监听容器宽度, 高度随宽度变化(现有 `onResize` 只改 width)

---

## 4. 新增功能卡(P0)

### 4.1 组件拆分

| 新组件 | 位置 | 内容 | 数据源 |
|---|---|---|---|
| `PankouPanel.vue` | 图表右侧(桌面 180px / 移动 116px) | 卖1-5→现价→买1-5, 委比/委差/外盘/内盘/涨停/跌停/换手/量比 | 腾讯 `qt.gtimg.cn` 五档 |
| `BidAuction.vue` | 功能卡 1 | 竞价分时 09:15-09:25 价+量迷你图 | KPL `GetStockBid` |
| `LimitGeneCard.vue` | 功能卡 2 | 涨停基因: 涨停次数/5%溢价/次日红盘%/首板封板率/破板率/连板率 | KPL `GetZhangTingGene` |
| `BigOrderCard.vue` | 功能卡 3 | 大单监控逐笔列表(时间/价/方向/手数/金额/类型) | KPL `GetMainMonitor_w30` |
| `LhbStockCard.vue` | 功能卡 4 | 龙虎榜个股上榜记录 + 游资席位映射 | KPL `GetStockList` + `seat_map.py` |
| `ZtHistoryTable.vue` | 第二行卡区 | 历史涨停列表(日期/类型/封板时间/原因) | KPL `GetDayZhangTing`(待验证是否含逐条历史) |

### 4.2 接口封装(新增到 [useKplApi.js](../../../stockboard-app/src/composables/useKplApi.js))

以下接口的参数格式已从 jiarenmens 爬虫实测调用确认:

| 函数 | 接口 | Host | 关键参数 | 返回 |
|---|---|---|---|---|
| `fetchStockPankou` | `GetStockPanKou` | HOST_HQ | `StockID, State:1` | 完整盘口(含 10 级 weituo、内外盘) |
| `fetchMainMonitor` | `GetMainMonitor_w30` | HOST_HQ | `StockID, Money:2(100万档), st:20` | 逐笔大单列表 |
| `fetchZhangTingGene` | `GetZhangTingGene` | HOST_HQ | `StockID` | `List[涨停次数, 5%溢价次, 次日红盘%, 首板封板率%, 破板率%, 连板率%]` |
| `fetchStockBid` | `GetStockBid` | HOST_HQ | `StockID` | `bid[[时间,价格,买卖方向,累计量],...]` |
| `fetchStockLhbHistory` | `GetStockList` | HOST_LHB | `StockID, Time` | 该股上榜历史 |

**游资席位映射**: 项目已有 `jiarenmens/src/analysis/seat_map.py`(营业部名→游资标签: 孙哥/养家/方新侠等, 包含匹配)。前端新增 `utils/seatMap.js` 移植同名映射表, `LhbStockCard` 用它给营业部打标签。

### 4.3 轮询并入(现有 timers 扩展)

| 现有 | 频次 | 新增 |
|---|---|---|
| `timers.quote` | 5s | 盘口五档并入报价轮询(同 5s) |
| `timers.mainFlow` | 15s | — |
| `timers.chart` | 15s | — |
| `timers.board` | 30s | — |
| `timers.limit` | 30s | 大单监控(15s)并入; 涨停基因/竞价/龙虎榜/历史涨停为低频, 页面激活时加载一次 + 手动刷新即可(不轮询) |

> 所有轮询沿用 `tick()` 静默模式: 仅交易时段执行, 失败保留旧数据。

---

## 5. P1 交互优化

| 项 | 现状 | 目标 |
|---|---|---|
| **吸顶** | 无 | 报价区(名称+现价+涨跌幅)滚动时吸顶, 页内 `position: sticky; top:0`, z-index 高于图表(吸顶条背景必须不透明, 否则滚动时图表文字透出); 吸顶条为独立子组件 `StickyQuoteBar.vue`, 数据与顶部报价区同源(quote + crossInfo 联动) |
| **悬浮图例 tooltip** | 十字光标只联动顶部 16 格, 图表内不悬浮 | 光标处显示浮动 tooltip(时间/开/高/低/收/涨跌幅/量), 分时则显示价/量/均价 |
| **代码复制** | 无 | 报价区代码旁复制按钮, `navigator.clipboard`, 复制成功给短暂提示 |
| **数据时效** | 无 | 报价区显示数据时间(如 `15:00`), 非交易时段给"已收盘"标记 |
| **骨架屏** | 图表加载中只有文字 | 图表区加载时显示骨架屏(灰块), 报价/卡片同样处理 |
| **副图切换入口** | 下拉 `<select>` | 保留 select, 但移动到副图 pane 2 左上角(现有位置), 分时视图也显示 |

> 注意: 吸顶需与现有 KeepAlive + `onDeactivated` 停轮询逻辑兼容; tooltip 不遮挡 K 线(现有设计已用顶部 16 格联动避免遮挡, 悬浮 tooltip 需轻量、定位在光标侧)。

---

## 6. 数据流

```
详情页初始化(onActivated)
  ├─ loadQuote()          → fetchKplQuote(主) → emQuoteApi(降级1) → qqQuoteUrl(降级2)
  ├─ loadChart()          → trend: loadTrend(腾讯分时) / kline: loadKline(腾讯 fqkline)
  ├─ loadBoards()         → fetchBoards(GetFeaturedSection)
  ├─ loadLimit()          → fetchLimitReason(GetDayZhangTing)
  ├─ loadMainFlow()       → fetchMainFlow(StockDPRealData)
  ├─ NEW loadPankou()     → fetchStockPankou / 腾讯五档
  ├─ NEW loadGene()       → fetchZhangTingGene
  ├─ NEW loadBid()        → fetchStockBid
  ├─ NEW loadBigOrder()   → fetchMainMonitor
  ├─ NEW loadLhb()        → fetchStockLhbHistory
  ├─ NEW loadZtHistory()  → fetchLimitReason 历史
  └─ loadInfo()           → fetchInfoList

盘中轮询(仅交易时段)
  ├─ 5s  报价 + 盘口
  ├─ 15s 主力 + 图表 + 大单监控
  └─ 30s 板块 + 涨停原因
```

---

## 7. 分阶段交付

| 阶段 | 内容 | 验收 |
|---|---|---|
| **Phase 1 图表视觉** | 方案B尺寸/3:1:1、分时副图、副图选项补全(cci/obv)、图表高随宽响应 | 设计稿与实现对比 ≤2px |
| **Phase 2 盘口+大单** | 右盘口(桌面180/移动116)、PankouPanel、BigOrderCard、游资映射 | 五档真实数据正确, 轮询并入 |
| **Phase 3 涨停+竞价** | LimitGeneCard、BidAuction、ZtHistoryTable | 涨停基因/竞价分时真实数据渲染 |
| **Phase 4 龙虎榜** | LhbStockCard + 游资标签 | 上榜历史真实渲染 |
| **Phase 5 交互** | 吸顶、tooltip、代码复制、数据时效、骨架屏 | 全部交互项可用 |

> 每阶段独立可合并到 main(遵守 workflow-rules: 禁止直接 push, 本地验证后再走 PR)。

---

## 8. 风险与待验证

- **GetStockPanKou 完整盘口字段**: 爬虫只用了 `GetStockPanKou_Narrow`(行情), 完整盘口 `GetStockPanKou` 的 weituo 字段结构需前端联调时 curl 验证(设计稿盘口数据暂以腾讯五档实现, KPL 盘口作为增强可选)。
- **GetStockList 个股过滤**: 全市场龙虎榜已有 `fetchLhbList`, 个股历史是否支持 `StockID` 过滤参数需实测; 不支持则前端用全量列表按 code 过滤(榜单 500 条, 可接受)。
- **历史涨停数据源(关键)**: 现有 `fetchLimitReason`(`GetDayZhangTing`)只取 `list[0]`(最近一次涨停原因), **不一定含逐条历史记录**。需联调时验证 `GetDayZhangTing` 的完整 `List` 是否返回多次涨停; 若无, 历史涨停卡降级为:
  - 用涨停基因接口的 `涨停次数` + `最近涨停原因` 合并展示(无逐条日期, 表格降级为摘要卡), 或
  - 按日期遍历全市场涨停原因(`GetPlateInfo_w38`, 需近期交易日)筛出该股 —— N 次请求成本高, 仅作 Phase 3 备选。
  此点在 writing-plans 阶段需先 curl 验证再定 Phase 3 交付形态。
- **龙虎榜/涨停基因仅盘中或收盘后可用**: 涨停基因对非涨停股可能返回空, 需空态处理(卡片显示"暂无数据"而非报错)。
- **竞价分时 09:15-09:25**: 非交易时段该接口可能返回空, 显示"非竞价时段"占位。

---

## 9. 明确不做的(P2 延后)

- 筹码分布、股吧(无数据源)
- 分钟K线(m60 已有, 5/15/30 分延后)
- 资金分档趋势、联动股、自选股、跳转交易 App
- 多图联动(放大缩小同步)
