"""受控网络贴图源：搜索开放素材，下载后登记到本地贴纸库。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests

from xzq.renderer.stickers import _MANIFEST_LOCK, _atomic_write

ICONIFY_SEARCH_URL = "https://api.iconify.design/search"
TWEMOJI_VERSION = "17.0.3"
MAX_ASSET_BYTES = 2 * 1024 * 1024
_OPENMOJI_ID = re.compile(r"openmoji:[a-z0-9][a-z0-9-]*\Z")


@dataclass(frozen=True)
class StickerCandidate:
    provider: str
    identifier: str
    title: str
    download_url: str
    source_url: str
    license: str
    license_url: str
    attribution: str


class NetworkStickerCatalog:
    """只接入允许本地化且许可可记录的网络素材源。"""

    def __init__(
        self,
        style_dir: str | Path = "style",
        http_get: Callable = requests.get,
    ):
        self.style_dir = Path(style_dir)
        self.sticker_dir = self.style_dir / "stickers"
        self.external_dir = self.sticker_dir / "external"
        self.manifest_path = self.sticker_dir / "stickers.json"
        self.http_get = http_get

    def search_openmoji(
        self, query: str, *, limit: int = 8
    ) -> list[StickerCandidate]:
        query = query.strip()
        if not query:
            raise ValueError("贴图搜索词不能为空")
        if not 1 <= limit <= 20:
            raise ValueError("搜索数量必须在 1 到 20 之间")
        try:
            response = self.http_get(
                ICONIFY_SEARCH_URL,
                params={"query": query, "prefix": "openmoji", "limit": limit},
                timeout=12,
            )
            response.raise_for_status()
            identifiers = response.json().get("icons", [])
        except (requests.RequestException, ValueError, AttributeError) as error:
            raise RuntimeError(f"OpenMoji 搜索失败：{error}") from error
        return [
            self.openmoji(identifier)
            for identifier in identifiers[:limit]
            if isinstance(identifier, str) and _OPENMOJI_ID.fullmatch(identifier)
        ]

    def openmoji(self, identifier: str) -> StickerCandidate:
        if not identifier.startswith("openmoji:"):
            identifier = f"openmoji:{identifier}"
        if not _OPENMOJI_ID.fullmatch(identifier):
            raise ValueError(f"无效 OpenMoji 标识：{identifier}")
        slug = identifier.split(":", 1)[1]
        return StickerCandidate(
            provider="openmoji",
            identifier=identifier,
            title=slug,
            download_url=f"https://api.iconify.design/{identifier}.svg",
            source_url=f"https://icon-sets.iconify.design/openmoji/{slug}/",
            license="CC BY-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            attribution="OpenMoji contributors",
        )

    def twemoji(self, emoji: str) -> StickerCandidate:
        emoji = emoji.strip()
        if not emoji:
            raise ValueError("Twemoji 字符不能为空")
        codepoints = [
            f"{ord(character):x}"
            for character in emoji
            if ord(character) != 0xFE0F
        ]
        if not codepoints:
            raise ValueError("Twemoji 字符不包含有效码点")
        code = "-".join(codepoints)
        return StickerCandidate(
            provider="twemoji",
            identifier=f"twemoji:{code}",
            title=emoji,
            download_url=(
                "https://cdn.jsdelivr.net/gh/jdecked/"
                f"twemoji@v{TWEMOJI_VERSION}/assets/svg/{code}.svg"
            ),
            source_url=f"https://github.com/jdecked/twemoji/tree/v{TWEMOJI_VERSION}",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution="Twemoji contributors",
        )

    def import_candidate(
        self, candidate: StickerCandidate, *, name: str, use: str
    ) -> Path:
        name = name.strip()
        use = use.strip()
        if not name or not use:
            raise ValueError("贴图名称和用途不能为空")
        self._validate_candidate_url(candidate)
        try:
            response = self.http_get(candidate.download_url, timeout=20)
            response.raise_for_status()
            payload = bytes(response.content)
        except (requests.RequestException, AttributeError, TypeError) as error:
            raise RuntimeError(f"贴图下载失败：{error}") from error
        self._validate_svg(payload)

        slug = candidate.identifier.replace(":", "-")
        relative = Path("external") / f"{slug}.svg"
        destination = self.sticker_dir / relative
        digest = hashlib.sha256(payload).hexdigest()

        with _MANIFEST_LOCK:
            manifest = self._load_manifest()
            existing = next(
                (
                    item
                    for item in manifest["stickers"]
                    if item.get("provider") == candidate.provider
                    and item.get("source_id") == candidate.identifier
                ),
                None,
            )
            if existing:
                existing_path = self.sticker_dir / existing["file"]
                if existing_path.is_file():
                    return existing_path
            _atomic_write(destination, payload)
            if existing is None:
                manifest["stickers"].append(
                    {
                        "key": f"online-{slug}",
                        "name": name,
                        "file": relative.as_posix(),
                        "use": use,
                        "provider": candidate.provider,
                        "source_id": candidate.identifier,
                        "source_url": candidate.source_url,
                        "license": candidate.license,
                        "license_url": candidate.license_url,
                        "attribution": candidate.attribution,
                        "sha256": digest,
                        "external": True,
                    }
                )
            else:
                existing["sha256"] = digest
                existing["file"] = relative.as_posix()
            serialized = (
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            _atomic_write(self.manifest_path, serialized)
        return destination

    def _load_manifest(self) -> dict:
        if self.manifest_path.is_file():
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        else:
            data = {"_doc": "贴纸库", "stickers": []}
        if not isinstance(data.get("stickers"), list):
            raise ValueError("贴纸清单 stickers 必须是数组")
        return data

    @staticmethod
    def _validate_candidate_url(candidate: StickerCandidate) -> None:
        expected_hosts = {
            "openmoji": "api.iconify.design",
            "twemoji": "cdn.jsdelivr.net",
        }
        expected_host = expected_hosts.get(candidate.provider)
        actual = urlparse(candidate.download_url)
        if (
            not expected_host
            or actual.scheme != "https"
            or actual.hostname != expected_host
            or not actual.path.endswith(".svg")
        ):
            raise ValueError("贴图来源不在允许列表")

    @staticmethod
    def _validate_svg(payload: bytes) -> None:
        if not payload or len(payload) > MAX_ASSET_BYTES:
            raise ValueError("贴图为空或超过 2MB")
        lowered = payload.lower()
        if (
            b"<svg" not in lowered
            or b"<script" in lowered
            or b"javascript:" in lowered
            or re.search(br"\son[a-z]+\s*=", lowered)
        ):
            raise ValueError("贴图包含不安全 SVG 内容")
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise ValueError("贴图不是有效 SVG") from error
        if not root.tag.endswith("svg"):
            raise ValueError("贴图不是有效 SVG")
