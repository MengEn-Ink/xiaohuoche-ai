"""微信 access_token 获取与本地缓存。

access_token 有效期 7200s，全局限额（频繁刷新会被微信踢），所以落盘缓存、
过期前 5 分钟才刷新。AppID/AppSecret 从环境变量读，不入库。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

API_BASE = "https://api.weixin.qq.com/cgi-bin"


class WeChatTokenError(RuntimeError):
    pass


class TokenManager:
    def __init__(self, cache_path: str = "./data/.wechat_token.json") -> None:
        self.app_id = os.getenv("WECHAT_APP_ID", "")
        self.app_secret = os.getenv("WECHAT_APP_SECRET", "")
        self.cache_path = Path(cache_path)

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def get_token(self) -> str:
        cached = self._read_cache()
        if (
            cached
            and cached.get("app_id") == self.app_id
            and cached.get("access_token")
            and cached.get("expires_at", 0) > time.time() + 300
        ):
            return cached["access_token"]
        return self._refresh()

    def _refresh(self) -> str:
        if not self.is_configured():
            raise WeChatTokenError("缺少 WECHAT_APP_ID / WECHAT_APP_SECRET")
        resp = requests.get(
            f"{API_BASE}/token",
            params={
                "grant_type": "client_credential",
                "appid": self.app_id,
                "secret": self.app_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if "access_token" not in body:
            errcode = body.get("errcode")
            hints = {
                40013: "AppID 无效，请核对公众号后台的开发者 ID",
                40125: "AppSecret 无效，请在公众号后台重置后更新本地 .env",
                40164: "当前出口 IP 不在公众号白名单，请在后台“基本配置”中加入后重试",
            }
            hint = hints.get(errcode, "请核对公众号凭证、接口权限和平台状态")
            raise WeChatTokenError(
                f"获取 token 失败（errcode={errcode}）：{hint}"
            )
        token = body["access_token"]
        self._write_cache(token, int(body.get("expires_in", 7200)))
        return token

    def _read_cache(self) -> dict | None:
        if not self.cache_path.exists():
            return None
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_cache(self, token: str, expires_in: int) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(
                {
                    "app_id": self.app_id,
                    "access_token": token,
                    "expires_at": time.time() + expires_in,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            self.cache_path.chmod(0o600)
        except OSError:
            pass
