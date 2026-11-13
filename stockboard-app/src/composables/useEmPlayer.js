import { ref } from 'vue'

/**
 * P3 / AC-3.3：EM rtV2 按区块独立直连。
 *
 * [A4] 8s 超时 / 最多 1 次重试(500ms 退避) / 并发 ≤3。
 * [A6] payload 只带公开 userId + 非敏感占位；绝不携带 ctToken/utToken/HIDDEN_UIDS。
 * [A7] emContract.ok=false → 0 请求，全站 🟡 回退（不发版）。
 */
export const TIMEOUT_MS = 8000
export const MAX_RETRY = 1
export const BACKOFF_MS = 500
export const MAX_CONCURRENCY = 3
export const RT_V2 = 'https://spzhapi.dfcfs.cn/rtV2'

export const BLOCKS = ['position', 'yield', 'trades']
export const BLOCK_LABELS = { position: '持仓', yield: '收益', trades: '调仓' }

export const REASON = {
  9035: '缺少该选手 userId 或组合已不可访问',
  9061: '组合已被删除，仅展示历史数据',
  '-1': '该组合收益需登录态（隐藏组合），已回退',
  400: '实时接口参数校验失败，已回退',
  empty: '实时接口返回空数据，已回退',
  network: '实时接口不可达（网络/跨域/风控），已回退',
  contract: '东财接口策略变更，全站回退历史数据',
}

// rtV2 是「包一层」协议：{ args, method, client, clientType, clientVersion }。
// 形状以 em_contract_probe.py::send_probe() 为准（该形状已实测 code=0 / emContract.ok=true）。
// 不手动设 User-Agent / Origin：由浏览器提供，避免额外 preflight 被拒。
export const CLIENT_VERSION = '11.5.5_3.0.0.261105130728749'
export const BLOCK_METHOD = {
  position: 'CombinationHoldPositionPermitHandler',
  yield: 'combination_yield_detail_handler',
  trades: 'CombinationRelocatePositionHandler',
}

const EMPTY = () => ({
  state: 'idle',      // idle | loading | live | stale | error
  at: '',             // HH:MM:SS（live 时）
  reason: '',
  detail: null,       // { code, message }（可展开）
  data: null,
})

function hhmmss() {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hourCycle: 'h23',
  }).format(new Date())
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function requestOnce(userId, zhId, block) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  try {
    // args 只带公开 userId + combinationId；调仓另需分页参数。
    // 顶层不附加 randomCode/deviceId：哨兵的最小 payload 已验证可用，多余字段会被服务端严格校验拒绝。
    const args = { userId, combinationId: String(zhId) }
    if (block === 'trades') {
      Object.assign(args, { pageNum: 1, pageSize: 20, beginDate: '', endDate: '' })
    }
    const payload = {
      args,
      method: BLOCK_METHOD[block],
      client: 'ios',
      clientType: 'cfw',
      clientVersion: CLIENT_VERSION,
    }
    const resp = await fetch(RT_V2, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
      },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
      credentials: 'omit',
    })
    // 不按 resp.ok 短路：400 的 body 里带 code/message，必须读出来才能给出准确原因
    const json = await resp.json()
    const code = json?.code
    if (code !== 0 && code !== '0') {
      return { ok: false, kind: String(code ?? '-1'), detail: { code, message: json?.message || '' } }
    }
    const data = json?.data
    if (!data || (Array.isArray(data) && !data.length) ||
        (typeof data === 'object' && !Array.isArray(data) && !Object.keys(data).length)) {
      return { ok: false, kind: 'empty', detail: { code, message: 'data 为空' } }
    }
    return { ok: true, data }
  } catch (e) {
    const aborted = e?.name === 'AbortError' || ctrl.signal.aborted
    return {
      ok: false,
      kind: 'network',
      detail: { code: aborted ? 'TIMEOUT' : 'CORS', message: e?.message || String(e) },
    }
  } finally {
    clearTimeout(timer)
  }
}

export async function requestBlock(userId, zhId, block) {
  let last = null
  for (let i = 0; i <= MAX_RETRY; i++) {
    if (i > 0) await sleep(BACKOFF_MS)
    last = await requestOnce(userId, zhId, block)
    if (last.ok) return last
  }
  return last
}

export function useEmPlayer() {
  const blocks = ref(Object.fromEntries(BLOCKS.map(b => [b, EMPTY()])))
  // 便于单测断言「ok=false 时 0 请求」
  const requestCount = ref(0)

  function reset() {
    blocks.value = Object.fromEntries(BLOCKS.map(b => [b, EMPTY()]))
    requestCount.value = 0
  }

  /** 每批至多 MAX_CONCURRENCY 个区块并发；区块独立失败独立回退。 */
  async function load(userId, zhId, emContract, fetchImpl = null) {
    reset()
    const allowed = emContract?.ok === true
    if (!allowed || !userId) {
      const reason = REASON[allowed ? 9035 : 'contract']
      blocks.value = Object.fromEntries(BLOCKS.map(b => [b, {
        ...EMPTY(), state: 'stale', reason,
      }]))
      return blocks.value
    }

    const impl = fetchImpl || requestBlock
    for (let i = 0; i < BLOCKS.length; i += MAX_CONCURRENCY) {
      const batch = BLOCKS.slice(i, i + MAX_CONCURRENCY)
      const results = await Promise.all(batch.map(b => {
        requestCount.value++
        return impl(userId, zhId, b)
      }))
      const next = { ...blocks.value }
      batch.forEach((b, idx) => {
        const r = results[idx]
        if (r?.ok) {
          next[b] = { ...EMPTY(), state: 'live', at: hhmmss(), data: r.data }
        } else {
          const kind = r?.kind || 'network'
          next[b] = {
            ...EMPTY(),
            state: 'error',
            reason: REASON[kind] || REASON.network,
            detail: r?.detail || null,
          }
        }
      })
      blocks.value = next
    }
    return blocks.value
  }

  return { blocks, requestCount, load, reset, REASON }
}
