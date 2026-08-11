// 通用格式化工具
// 注: pctHtml 返回带颜色 class 的 HTML, 仅用于 v-html 输出;
//     普通插值 {{ }} 请用各组件自己的 pct()(纯文本) + :style 内联色

// 红涨绿跌百分比(全局 .positive/.negative 类定义于 style.css)
export function pctHtml(v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  const cls = n >= 0 ? 'positive' : 'negative'
  const sign = n >= 0 ? '+' : ''
  return `<span class="${cls}">${sign}${n.toFixed(2)}%</span>`
}

// 排行榜单元格渲染: 收益类 → 百分比着色; 净值 → 原样数字(≥1 红, <1 绿); 关注 → 千分位
export function rankCellHtml(key, v) {
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  if (key === 'net_value') {
    return `<span class="${n >= 1 ? 'positive' : 'negative'}">${n.toFixed(3)}</span>`
  }
  if (key === 'followers') {
    return `<span class="dim">${Number(n).toLocaleString()}</span>`
  }
  return pctHtml(v)
}

// 回撤风险梯度: 0/缺失灰, ≤5% 绿(安全), 5~15% 橙(警示), >15% 红(危险)
export function drawdownColor(v) {
  const n = parseFloat(v)
  if (!isFinite(n) || n <= 0) return '#999'
  if (n > 15) return '#c0392b'
  if (n > 5) return '#e67e22'
  return '#27ae60'
}
