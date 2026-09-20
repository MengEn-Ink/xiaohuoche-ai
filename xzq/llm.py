"""LLM 客户端：兼容 OpenAI Chat Completions 协议（豆包 / DeepSeek / OpenAI 等）。

- chat()：纯文本对话，用于写作。
- vision()：多模态，输入本地图片路径做 OCR / 截图转录。
未配置 LLM_* 环境变量时 is_configured() 为 False，调用方应走离线兜底。
"""
from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import requests


class LLMNotConfigured(RuntimeError):
    """未配置 LLM 环境变量。"""


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.vision_model = vision_model or os.getenv("LLM_VISION_MODEL", "") or self.model
        self.timeout = timeout

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def _post(self, payload: dict[str, Any]) -> str:
        if not self.is_configured():
            raise LLMNotConfigured("缺少 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def chat(self, system: str, user: str, temperature: float = 0.9) -> str:
        return self._post(
            {
                "model": self.model,
                "temperature": temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        )

    def vision(self, image_paths: list[str | Path], prompt: str) -> str:
        """对一张或多张本地图片做多模态识别（base64 内联）。"""
        if not (self.base_url and self.api_key and self.vision_model):
            raise LLMNotConfigured("缺少 LLM 视觉模型配置")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for p in image_paths:
            mime = mimetypes.guess_type(str(p))[0] or "image/png"
            b64 = base64.b64encode(Path(p).read_bytes()).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        return self._post(
            {
                "model": self.vision_model,
                "messages": [{"role": "user", "content": content}],
            }
        )
