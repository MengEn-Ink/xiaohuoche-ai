"""素材目录轮询监听：只写待处理清单，不覆盖已审稿正文。"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from ..collector.inbox import list_inbox_images
from ..collector.transcribe import transcribe_materials
from ..llm import LLMClient
from ..models import Material
from .revision import atomic_write_json, utc_now


def directory_fingerprint(directory: Path) -> str:
    digest = hashlib.sha256()
    if not directory.exists():
        return digest.hexdigest()
    for path in sorted(item for item in directory.iterdir() if item.is_file()):
        stat = path.stat()
        digest.update(path.name.encode())
        digest.update(str(stat.st_size).encode())
        digest.update(str(stat.st_mtime_ns).encode())
    return digest.hexdigest()


def scan_pending_materials(
    inbox_dir: Path,
    destination: Path,
    known: list[Material],
    llm: LLMClient,
) -> dict[str, object]:
    incoming = list_inbox_images(inbox_dir)
    known_keys = {
        str(item.meta.get("content_sha256") or item.source or f"{item.kind}:{item.text}")
        for item in known
    }
    pending = [
        item for item in incoming
        if str(item.meta.get("content_sha256") or item.source or f"{item.kind}:{item.text}") not in known_keys
    ]
    if pending:
        transcribe_materials(pending, llm)
    payload: dict[str, object] = {
        "updatedAt": utc_now(),
        "inboxFingerprint": directory_fingerprint(inbox_dir),
        "count": len(pending),
        "materials": [asdict(item) for item in pending],
        "notice": "新素材仅进入待处理清单，确认重新生成前不会覆盖已审稿正文",
    }
    atomic_write_json(destination, payload)
    return payload


def watch_materials(
    inbox_dir: Path,
    destination: Path,
    known: list[Material],
    llm: LLMClient,
    interval: float = 1.0,
    once: bool = False,
    notify: Callable[[dict[str, object]], None] | None = None,
) -> None:
    previous = ""
    while True:
        fingerprint = directory_fingerprint(inbox_dir)
        if fingerprint != previous:
            # 连续两次一致才读取，规避仍在写入的大文件。
            time.sleep(min(interval, 0.5))
            if fingerprint == directory_fingerprint(inbox_dir):
                payload = scan_pending_materials(inbox_dir, destination, known, llm)
                previous = fingerprint
                if notify:
                    notify(payload)
        if once:
            return
        time.sleep(interval)
