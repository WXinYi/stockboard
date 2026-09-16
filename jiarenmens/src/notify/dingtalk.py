"""钉钉机器人推送(加签模式, 与 crawl.yml 现有逻辑一致)

用法:
    from src.notify.dingtalk import DingTalk

    dt = DingTalk()  # 读环境变量 DINGTALK_URL / DINGTALK_SECRET
    dt.send_markdown("标题", "markdown 内容")

errcode != 0 视为失败抛出(2026-09-15 修复): 钉钉对 token 失效/被限流/关键词不符
一律回 HTTP 200, 只看 HTTP 状态会把"没送到"记成"已推送"。
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request


class DingTalk:
    def __init__(self, url: str = "", secret: str = ""):
        self.url = url or os.environ.get("DINGTALK_URL", "")
        self.secret = secret or os.environ.get("DINGTALK_SECRET", "")
        if not self.url:
            raise ValueError("缺少钉钉 webhook(DINGTALK_URL)")

    def _signed_url(self) -> str:
        timestamp = str(round(time.time() * 1000))
        sign_str = f"{timestamp}\n{self.secret}" if self.secret else timestamp
        if self.secret:
            hmac_code = hmac.new(self.secret.encode(), sign_str.encode(), hashlib.sha256).digest()
            sign = base64.b64encode(hmac_code).decode()
            return f"{self.url}&timestamp={timestamp}&sign={sign}"
        return f"{self.url}&timestamp={timestamp}"

    def send_markdown(self, title: str, text: str) -> dict:
        payload = json.dumps(
            {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
        ).encode()
        req = urllib.request.Request(
            self._signed_url(), data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        # 钉钉对"token 失效/机器人被限流/关键词不符"等一律回 HTTP 200 + errcode!=0:
        # 只看 HTTP 状态会把"没送到"记成"已推送"(2026-09-15 实测 errcode=300001 仍被判成功)。
        errcode = data.get("errcode") if isinstance(data, dict) else None
        if errcode not in (0, None):
            raise RuntimeError(f"钉钉接口返回错误 errcode={errcode} errmsg={data.get('errmsg')}")
        return data
