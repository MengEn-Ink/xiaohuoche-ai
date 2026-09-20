"""素材筐监听：扫描 inbox 目录里的图片（群照片 / 群聊截图）。

合规约束：个人微信无官方群消息接口，群聊靠小编/群友**手动截图投喂**到这个目录，
不挂任何逆向机器人。程序只负责发现新文件，交给 transcribe 做 OCR。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import Material

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# 手机端随手写的粗糙初稿（纯文本/markdown），AI 以此为骨架扩写润色
DRAFT_SUFFIXES = {".txt", ".md"}


def list_inbox_images(inbox_dir: str | Path) -> list[Material]:
    """列出素材筐里的图片（群照片/群聊截图）和文本初稿，按文件名排序。

    - 图片：截图/照片先标 kind，OCR 后再细化。
    - 文本（.txt/.md）：视为小编在手机上随手写的初稿，kind="draft"，内容直接读入。
    """
    base = Path(inbox_dir)
    if not base.is_dir():
        return []
    materials: list[Material] = []
    image_fingerprints: set[str] = set()
    for p in sorted(base.iterdir()):
        if p.is_dir():
            continue
        suffix = p.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            fingerprint = hashlib.sha256(p.read_bytes()).hexdigest()
            # 文件名不参与图片身份，同批复制或改名后仍只进入台账一次。
            if fingerprint in image_fingerprints:
                continue
            image_fingerprints.add(fingerprint)
            kind = "screenshot" if _looks_like_screenshot(p.name) else "photo"
            materials.append(
                Material(
                    kind=kind,
                    source=str(p),
                    meta={
                        "filename": p.name,
                        "content_sha256": fingerprint,
                        "used_fingerprint": fingerprint,
                    },
                )
            )
        elif suffix in DRAFT_SUFFIXES:
            materials.append(
                Material(
                    kind="draft",
                    text=p.read_text(encoding="utf-8", errors="ignore").strip(),
                    source=str(p),
                    meta={"filename": p.name},
                )
            )
    return materials


def _looks_like_screenshot(name: str) -> bool:
    keywords = ("聊天", "群", "chat", "对话", "strava", "微信", "截图", "screen")
    low = name.lower()
    return any(k.lower() in low for k in keywords)
