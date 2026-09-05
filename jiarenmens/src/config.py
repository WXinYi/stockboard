from pathlib import Path
import os

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

BASE_URL = "https://groupwap.eastmoney.com"

PLAYER_LIST_URL = f"{BASE_URL}/group/invest/reality.html"
PLAYER_INFO_URL = f"{BASE_URL}/group/reality/info.html"
POSITION_URL = f"{BASE_URL}/group/reality/detail.html"
TRADE_URL = f"{BASE_URL}/group/reality/change.html"

# groupwap 是目标平台 APP 内嵌 H5 站点。
# 站点 JS 通过 (UA 含 EMProjJs / EMRead 关键字) + (window.emh5 桥接对象存在) 判定 "在 APP 内"。
# 任一条件不满足就弹"前往APP"对话框，不渲染 detail-content。
# 所以 UA 伪装为目标平台 iPhone WebView，并在 BrowserContext 中注入 emh5 占位桥接。
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Mobile/15E148 EMProjJs-IPhone/EMRead 12.0.0 (em_appid/200)"
)

# 移动设备模拟参数（与 USER_AGENT 配套，同时给 Playwright 和 requests 使用）
MOBILE_VIEWPORT = {"width": 414, "height": 896}
DEVICE_SCALE_FACTOR = 3

HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": BASE_URL,
}

# ---- 开盘啦(KPL)竞价数据 ----
# 非官方 APP 接口(2026-08-09 实测 19/19 可用)。Token 为公开示例账号,
# 落地后注册自有账号替换(KPL_TOKEN/KPL_USERID 一行切换)。
KPL_UA = "Dalvik/2.1.0 (Linux; U; Android 14; V2178A Build/UP1A.231005.007)"
KPL_TOKEN = "036ca9cad6e44ee4a585c22cb2c298ed"
KPL_USERID = "3807176"
KPL_HOST_RT = "https://apphwhq.longhuvip.com"  # 今日实时
KPL_HOST_HIS = "https://apphis.longhuvip.com"  # 历史(回测)
KPL_HOST_APP = "https://apphq.longhuvip.com"   # 实时(情绪/大单)
KPL_HOST_LHB = "https://applhb.longhuvip.com"  # 龙虎榜
KPL_TIMEOUT = 10

# His 域名对 GitHub Actions 出口 IP 风控(2026-08-29 起 limit_pool/market_breadth 断供,
# 本地大陆直连正常) → CI 侧设 KPL_HIS_PROXY=SCF 函数地址后, His 请求改走 <proxy>/kpl-his
# 中转(scf/index.js 契约: 强制 okhttp UA, 转发 w1/api/index.php)。本地不设, 保持直连。
KPL_HIS_PROXY = os.environ.get("KPL_HIS_PROXY", "").rstrip("/")

# 竞价扫描结果输出路径(前端读取)
AUCTION_OUT = Path(__file__).resolve().parents[2] / "stockboard-app" / "public" / "data" / "latest" / "auction.json"