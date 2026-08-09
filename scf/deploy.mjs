// 部署 KPL 代理到腾讯云 SCF (函数 URL 方式, 幂等)
// API 网关已停售 → 用 Type=http 函数 URL; CLS 未开通 → 不关联日志
// 用法: TENCENT_SECRET_ID=... TENCENT_SECRET_KEY=... node deploy.mjs
import tencentcloud from 'tencentcloud-sdk-nodejs-scf'
import fs from 'fs'
import { execSync } from 'child_process'

const FUNCTION = 'kpl-proxy'
const REGION = 'ap-guangzhou'
const { scf } = tencentcloud
const client = new scf.v20180416.Client({
  credential: {
    secretId: process.env.TENCENT_SECRET_ID,
    secretKey: process.env.TENCENT_SECRET_KEY,
  },
  region: REGION,
  profile: { httpProfile: { endpoint: 'scf.tencentcloudapi.com' } },
})

// 打包 index.js + scf_bootstrap
execSync('zip -q /tmp/kpl-proxy.zip index.js scf_bootstrap')
const zip = fs.readFileSync('/tmp/kpl-proxy.zip').toString('base64')

async function hasFunction() {
  const r = await client.ListFunctions({ Namespace: 'default' })
  return (r.Functions || []).some(f => f.FunctionName === FUNCTION)
}
async function hasUrlTrigger() {
  const r = await client.ListTriggers({ FunctionName: FUNCTION })
  return (r.Triggers || []).some(t => t.Type === 'http')
}

try {
  if (await hasFunction()) {
    try {
      const r = await client.UpdateFunctionCode({ FunctionName: FUNCTION, ZipFile: zip })
      console.log('✅ 代码已更新', r.RequestId)
    } catch (e) {
      if (e.code === 'FailedOperation.UpdateFunctionCode' && e.message.includes('Updating')) {
        await new Promise(r => setTimeout(r, 5000))   // 上次更新未完成, 等 5s 重试一次
        await client.UpdateFunctionCode({ FunctionName: FUNCTION, ZipFile: zip })
        console.log('✅ 代码已更新(重试)', )
      } else throw e
    }
  } else {
    await client.CreateFunction({
      FunctionName: FUNCTION, Type: 'HTTP', Runtime: 'Nodejs18.15',
      MemorySize: 128, Timeout: 10,
      Code: { ZipFile: zip },
    })
    console.log('✅ 函数已创建')
  }
  if (!(await hasUrlTrigger())) {
    await client.CreateTrigger({
      FunctionName: FUNCTION, Type: 'http', TriggerName: 'kpl-proxy-url',
      TriggerDesc: JSON.stringify({ AuthType: 'NONE', NetConfig: { EnableIntranet: true, EnableExtranet: true } }),
      Qualifier: '$DEFAULT',
    })
    console.log('✅ 函数 URL 已创建')
  } else {
    console.log('✅ 函数 URL 已存在')
  }
  const r = await client.ListTriggers({ FunctionName: FUNCTION })
  const t = (r.Triggers || []).find(x => x.Type === 'http')
  const desc = t ? JSON.parse(t.TriggerDesc) : {}
  console.log('公网 URL:', desc.NetConfig?.ExtranetUrl || '(未找到)')
} catch (e) {
  console.error('FAIL:', e.code, e.message)
  process.exit(1)
}
