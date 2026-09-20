"""Trusted metadata for person-bearing render assets.

The sidecar binds declared subject geometry to immutable image bytes.  Cutout
geometry is derived from the alpha channel; callers cannot invent a smaller box.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from PIL import Image

SCHEMA_VERSION = 1
ALGORITHM_VERSION = "alpha-grid-v1"
ZONE_TOLERANCE_PERCENT = 2.0


class SubjectMetadataError(RuntimeError):
    """Subject metadata is absent, stale, or internally inconsistent."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sidecar_path(rendered: Path) -> Path:
    return Path(str(rendered) + ".subject.json")


def _alpha_geometry(
    mask_path: Path, rendered_size: tuple[int, int]
) -> tuple[list[float], list[list[float]], str]:
    with Image.open(mask_path) as opened:
        alpha = opened.convert("RGBA").getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        raise SubjectMetadataError("人物主体 alpha 为空")
    mask_width, mask_height = alpha.size
    width, height = rendered_size
    offset_x = (width - mask_width) // 2
    offset_y = (height - mask_height) // 2
    left, top, right, bottom = bbox
    zone = [
        round((left + offset_x) * 100 / width, 4),
        round((top + offset_y) * 100 / height, 4),
        round((right - left) * 100 / width, 4),
        round((bottom - top) * 100 / height, 4),
    ]
    # Deterministic 24x24 grid. Each cell contributes its nearest opaque pixel,
    # so transparent padding and holes are never sampled.
    points: list[list[float]] = []
    pixels = alpha.load()
    for row in range(24):
        cell_top = top + (bottom - top) * row // 24
        cell_bottom = top + (bottom - top) * (row + 1) // 24
        for column in range(24):
            cell_left = left + (right - left) * column // 24
            cell_right = left + (right - left) * (column + 1) // 24
            if cell_right <= cell_left or cell_bottom <= cell_top:
                continue
            target_x = (cell_left + cell_right - 1) / 2
            target_y = (cell_top + cell_bottom - 1) / 2
            candidates = [
                (x, y)
                for y in range(cell_top, cell_bottom)
                for x in range(cell_left, cell_right)
                if pixels[x, y] >= 200
            ]
            if not candidates:
                continue
            x, y = min(
                candidates,
                key=lambda point: (point[0] - target_x) ** 2 + (point[1] - target_y) ** 2,
            )
            normalized = [
                round((x + offset_x + 0.5) * 100 / width, 4),
                round((y + offset_y + 0.5) * 100 / height, 4),
            ]
            if normalized not in points:
                points.append(normalized)
    mask_digest = hashlib.sha256(alpha.tobytes()).hexdigest()
    return zone, points, mask_digest


def write_subject_metadata(
    source: Path,
    rendered: Path,
    *,
    mask_path: Path,
    mode: str,
    source_face_count: int,
    rendered_face_count: int,
    fallback_reason: str = "",
) -> Path:
    source = Path(source)
    rendered = Path(rendered)
    if mode not in {"cutout", "full-image-fallback"}:
        raise SubjectMetadataError(f"未知人物渲染模式：{mode}")
    if source_face_count < 0 or rendered_face_count < 0:
        raise SubjectMetadataError("人脸数必须可判定")
    if mode == "cutout" and rendered_face_count < source_face_count:
        raise SubjectMetadataError("抠图后人脸数减少")
    if mode == "cutout":
        with Image.open(rendered) as opened:
            rendered_size = opened.size
        zone, points, mask_digest = _alpha_geometry(Path(mask_path), rendered_size)
    else:
        zone = [0.0, 0.0, 100.0, 100.0]
        points = [
            [round((column + 0.5) * 100 / 24, 4), round((row + 0.5) * 100 / 24, 4)]
            for row in range(24)
            for column in range(24)
        ]
        with Image.open(mask_path) as opened:
            alpha = opened.convert("RGBA").getchannel("A")
        mask_digest = hashlib.sha256(alpha.tobytes()).hexdigest()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "source": os.path.relpath(source.resolve(), rendered.parent.resolve()),
        "source_sha256": _sha256(source),
        "rendered": rendered.name,
        "rendered_sha256": _sha256(rendered),
        "mode": mode,
        "source_face_count": source_face_count,
        "rendered_face_count": rendered_face_count,
        "subject_zone": zone,
        "mask_sha256": mask_digest,
        "mask": os.path.relpath(Path(mask_path).resolve(), rendered.parent.resolve()),
        "sample_points": points,
        "fallback_reason": fallback_reason,
    }
    destination = sidecar_path(rendered)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def validate_subject_metadata(
    rendered: Path, sidecar: Path, declared_zone: str
) -> dict[str, object]:
    rendered = Path(rendered)
    sidecar = Path(sidecar)
    if not sidecar.is_file():
        raise SubjectMetadataError(f"缺少人物主体 sidecar：{sidecar}")
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SubjectMetadataError(f"人物主体 sidecar 无法解析：{sidecar}") from exc
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise SubjectMetadataError("人物主体 sidecar 版本不受支持")
    if payload.get("algorithm_version") != ALGORITHM_VERSION:
        raise SubjectMetadataError("人物主体 sidecar 算法版本不一致")
    if payload.get("rendered_sha256") != _sha256(rendered):
        raise SubjectMetadataError("渲染资产哈希不一致")
    source = (rendered.parent / str(payload.get("source") or "")).resolve()
    if not source.is_file():
        raise SubjectMetadataError(f"人物源图不存在：{source}")
    if payload.get("source_sha256") != _sha256(source):
        raise SubjectMetadataError("源图哈希不一致")
    mask = (rendered.parent / str(payload.get("mask") or "")).resolve()
    if not mask.is_file():
        raise SubjectMetadataError(f"人物主体 mask 不存在：{mask}")
    with Image.open(mask) as opened:
        mask_digest = hashlib.sha256(opened.convert("RGBA").getchannel("A").tobytes()).hexdigest()
    if payload.get("mask_sha256") != mask_digest:
        raise SubjectMetadataError("人物主体 mask 哈希不一致")
    with Image.open(rendered) as opened:
        rendered_size = opened.size
    if payload.get("mode") == "cutout":
        expected_zone, expected_points, _ = _alpha_geometry(mask, rendered_size)
    elif payload.get("mode") == "full-image-fallback":
        expected_zone = [0.0, 0.0, 100.0, 100.0]
        expected_points = [
            [round((column + 0.5) * 100 / 24, 4), round((row + 0.5) * 100 / 24, 4)]
            for row in range(24)
            for column in range(24)
        ]
    else:
        raise SubjectMetadataError("人物主体 sidecar 模式无效")
    if payload.get("subject_zone") != expected_zone or payload.get("sample_points") != expected_points:
        raise SubjectMetadataError("人物主体 sidecar 几何数据不一致")
    try:
        declared = [float(value.strip()) for value in declared_zone.split(",")]
        expected = [float(value) for value in payload["subject_zone"]]
    except (ValueError, TypeError, KeyError) as exc:
        raise SubjectMetadataError("人物主体区或 sidecar 坐标无效") from exc
    if len(declared) != 4 or len(expected) != 4 or any(
        abs(left - right) > ZONE_TOLERANCE_PERCENT
        for left, right in zip(declared, expected)
    ):
        raise SubjectMetadataError("主体区与 sidecar 不一致")
    return payload
