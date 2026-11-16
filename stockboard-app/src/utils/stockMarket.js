// 股票市场过滤：共识页只看 A 股（2026-11-16 用户要求去掉港股）。
//
// 规则依据（对生产 1905+538 只票实测，零反例）：
//   * 港股代码 = 5 位数字（01810 小米、03690 美团、09988 阿里 …）
//   * A 股代码 = 6 位数字（60/00/30/68 沪深、920 北交所、11/12 可转债、15/51/58 ETF …）
// 没有 5 位的 A 股，也没有 6 位的港股，故按长度即可精确区分，无需白名单。
//
// 只做展示层过滤，不改数据契约、不重跑采集：stocks.json / today.json 仍保留全量，
// 两个共识页各自调用本函数过滤，随时可回退（去掉过滤即恢复港股）。

/** 是否 A 股（6 位数字代码） */
export function isAStock(code) {
  return /^\d{6}$/.test(String(code || ''))
}

/** 是否港股（5 位数字代码） */
export function isHKStock(code) {
  return /^\d{5}$/.test(String(code || ''))
}

/** 从 {code,...} 数组里过滤出 A 股，保持原顺序 */
export function onlyAStock(list) {
  return (Array.isArray(list) ? list : []).filter(s => isAStock(s && s.code))
}
