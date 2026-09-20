"""配置加载：config.yaml + .env 环境变量。

- config.yaml 存业务配置（栏目模板、crew、checklist 等），缺失时回退内置默认。
- .env 存密钥（WECHAT_*、STRAVA_*、LLM_*），绝不入库。
"""
from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

def _load_local_env(path: str | Path = ".env") -> None:
    """在未安装 python-dotenv 时读取常见 .env 语法，且不覆盖显式环境变量。"""
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "a").isalnum():
            continue
        try:
            parts = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            continue
        value = " ".join(parts) if parts else ""
        os.environ.setdefault(key, value)


try:  # python-dotenv 是可选便利项，缺失时使用内置解析器
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - 是否安装依赖由运行环境决定
    _load_local_env()


DEFAULT_CONFIG_PATH = "config.yaml"
FALLBACK_CONFIG_PATH = "config.example.yaml"


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | None = None) -> "Config":
        p = Path(path or DEFAULT_CONFIG_PATH)
        if not p.exists():  # 没复制 config.yaml 时用示例兜底，保证离线可跑
            p = Path(FALLBACK_CONFIG_PATH)
        data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
        return cls(raw=data or {})

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def columns(self) -> dict[str, dict[str, str]]:
        return self.get("columns", default={}) or {}

    @property
    def review_checklist(self) -> list[str]:
        return self.get("review_checklist", default=[]) or []

    def path(self, name: str, default: str) -> str:
        """路径优先取 config.paths，其次环境变量，最后默认值。"""
        env_map = {
            "inbox_dir": "XHC_INBOX_DIR",
            "work_dir": "XHC_WORK_DIR",
        }
        val = self.get("paths", name)
        if val:
            return val
        env = env_map.get(name)
        if env and os.getenv(env):
            return os.environ[env]
        return default


def require_env(*names: str) -> dict[str, str]:
    """读取必填环境变量；缺失项汇总报错，避免一个个撞。"""
    missing = [n for n in names if not os.getenv(n)]
    if missing:
        raise RuntimeError(f"缺少环境变量：{', '.join(missing)}（请在 .env 配置）")
    return {n: os.environ[n] for n in names}
