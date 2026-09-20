"""Fail-closed whole-issue identity checks for local image assets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

class ImageIdentityError(RuntimeError):
    """An image identity is unavailable or reused within one issue."""


@dataclass(frozen=True)
class ImageIdentityReport:
    image_count: int
    identity_count: int


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = {key: value or "" for key, value in attrs}
        if values.get("src"):
            self.images.append(values)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _local_path(html_path: Path, reference: str, label: str) -> Path:
    parsed = urlparse(reference)
    if parsed.scheme or parsed.netloc:
        raise ImageIdentityError(f"{label} 必须是可哈希的本地文件：{reference}")
    path = (html_path.parent / unquote(parsed.path)).resolve()
    if not path.is_file():
        raise ImageIdentityError(f"{label} 不存在或无法哈希：{reference}")
    return path


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ImageIdentityError(f"图片无法读取或哈希：{path}") from exc


def _identity(html_path: Path, attrs: dict[str, str]) -> str:
    rendered = _local_path(html_path, attrs["src"], "图片")
    meta_ref = attrs.get("data-subject-meta", "")
    if not meta_ref:
        return _sha256(rendered)
    sidecar = _local_path(html_path, meta_ref, "人物 sidecar")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageIdentityError(f"人物 sidecar 无法解析：{sidecar}") from exc
    if payload.get("rendered_sha256") != _sha256(rendered):
        raise ImageIdentityError(f"人物渲染资产哈希不一致：{rendered}")
    source_ref = payload.get("source")
    if not isinstance(source_ref, str) or not source_ref:
        raise ImageIdentityError(f"人物 sidecar 缺少源图路径：{sidecar}")
    source = (rendered.parent / source_ref).resolve()
    if not source.is_file():
        raise ImageIdentityError(f"人物源图不存在：{source}")
    source_sha256 = payload.get("source_sha256")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ImageIdentityError(f"人物 sidecar 缺少有效 source_sha256：{sidecar}")
    if _sha256(source) != source_sha256:
        raise ImageIdentityError(f"人物源图哈希不一致：{source}")
    return source_sha256


def validate_image_identities(html_paths: list[Path]) -> ImageIdentityReport:
    """Reject a repeated source identity across the union of HTML pages."""
    seen: dict[str, str] = {}
    count = 0
    for raw_path in html_paths:
        html_path = Path(raw_path).resolve()
        if not html_path.is_file():
            raise ImageIdentityError(f"缺少待检查 HTML：{html_path}")
        parser = _ImageParser()
        try:
            parser.feed(html_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ImageIdentityError(f"无法读取待检查 HTML：{html_path}") from exc
        for attrs in parser.images:
            identity = _identity(html_path, attrs)
            location = f"{html_path.name}:{attrs['src']}"
            if identity in seen:
                raise ImageIdentityError(
                    f"同源图片重复：{seen[identity]} 与 {location}"
                )
            seen[identity] = location
            count += 1
    return ImageIdentityReport(image_count=count, identity_count=len(seen))
