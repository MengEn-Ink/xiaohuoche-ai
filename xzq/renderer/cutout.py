"""人物抠图：rembg（u2net）非生成式分割，只出遮罩、像素来自原图。缓存到资产目录；rembg 没装返回 None。"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .faces import faces
from .subject_meta import SubjectMetadataError, write_subject_metadata


INK = (8, 8, 8, 255)
WHITE = (255, 255, 255, 255)


@dataclass(frozen=True)
class SubjectAsset:
    rendered: Path
    sidecar: Path
    zone: str
    mode: str


def _cache_path(src: Path, asset_dir: Path) -> Path:
    # stem 相同但扩展名或目录不同也不能共享抠图缓存。
    fingerprint = hashlib.sha256(
        str(src.resolve()).encode("utf-8") + src.read_bytes()
    ).hexdigest()[:12]
    return asset_dir / f"{src.stem}-{fingerprint}-cut.png"


def cutout(src: Path, asset_dir: Path) -> Path | None:
    asset_dir.mkdir(parents=True, exist_ok=True)
    try:
        dst = _cache_path(Path(src), asset_dir)
    except OSError:
        return None
    if dst.exists():
        return dst
    try:
        from PIL import Image

        with Image.open(src) as original:
            rgba = original.convert("RGBA")
            alpha = rgba.getchannel("A")
            if alpha.getextrema()[0] < 255:
                bbox = alpha.getbbox()
                if not bbox:
                    return None
                rgba.crop(bbox).save(dst)
                return dst
    except Exception:
        return None
    try:
        from rembg import remove
    except ImportError:
        return None
    try:
        out = remove(rgba).convert("RGBA")
        bbox = out.getchannel(
            "A"
        ).getbbox()  # 只看 alpha；透明像素残留 RGB 不能算有效主体
        if not bbox:
            return None
        out.crop(bbox).save(dst)
        return dst
    except Exception:
        return None


def outlined_cutout(
    src: Path, asset_dir: Path, *, white_px: int = 10, black_px: int = 4
) -> Path | None:
    """Give a transparent subject the white-and-black sticker contour from 605.

    Only the alpha mask is expanded; the subject RGB pixels remain unchanged.
    """
    if white_px < 0 or black_px < 0:
        raise ValueError("outline widths must be non-negative")
    source = cutout(Path(src), asset_dir)
    if source is None:
        return None
    return _outline_existing(source, Path(src), asset_dir, white_px, black_px)


def _outline_existing(
    source: Path, original: Path, asset_dir: Path, white_px: int, black_px: int
) -> Path | None:
    try:
        from PIL import Image, ImageFilter

        with Image.open(source) as opened:
            subject = opened.convert("RGBA")
        pad = white_px + black_px
        canvas_size = (subject.width + pad * 2, subject.height + pad * 2)
        alpha = Image.new("L", canvas_size, 0)
        alpha.paste(subject.getchannel("A"), (pad, pad))

        def expanded(mask, radius: int):
            if radius <= 0:
                return mask
            return mask.filter(ImageFilter.MaxFilter(radius * 2 + 1))

        white_mask = expanded(alpha, white_px)
        black_mask = expanded(alpha, pad)
        result = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        result.paste(INK, (0, 0), black_mask)
        result.paste(WHITE, (0, 0), white_mask)
        result.alpha_composite(subject, (pad, pad))
        fingerprint = hashlib.sha256(
            source.read_bytes() + f"outline:{white_px}:{black_px}".encode()
        ).hexdigest()[:12]
        dst = asset_dir / f"{Path(original).stem}-{fingerprint}-outlined.png"
        result.save(dst)
        return dst
    except Exception:
        return None


def _zone_text(zone: object) -> str:
    values = list(zone) if isinstance(zone, list) else [0, 0, 100, 100]
    return ",".join(f"{float(value):g}" for value in values)


def prepare_subject_asset(src: Path, asset_dir: Path) -> SubjectAsset:
    """Prefer a complete non-generative cutout, else protect the full photo."""
    src = Path(src)
    asset_dir = Path(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    source_faces = faces(str(src))
    if source_faces is None:
        raise SubjectMetadataError("YuNet 无法检测源图，不能证明人物完整")
    separated = cutout(src, asset_dir)
    reason = "cutout-failed"
    if separated is not None:
        rendered_faces = faces(str(separated))
        if rendered_faces is None:
            raise SubjectMetadataError("YuNet 无法检测抠图，不能证明人物完整")
        if len(rendered_faces) >= len(source_faces):
            outlined = _outline_existing(separated, src, asset_dir, 10, 4)
            if outlined is not None:
                sidecar = write_subject_metadata(
                    src, outlined, mask_path=separated, mode="cutout",
                    source_face_count=len(source_faces),
                    rendered_face_count=len(rendered_faces),
                )
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
                return SubjectAsset(
                    outlined, sidecar, _zone_text(payload["subject_zone"]), "cutout"
                )
        else:
            reason = "cutout-lost-faces"
    fallback = asset_dir / src.name
    if fallback.resolve() != src.resolve():
        shutil.copy2(src, fallback)
    sidecar = write_subject_metadata(
        src, fallback, mask_path=fallback, mode="full-image-fallback",
        source_face_count=len(source_faces), rendered_face_count=len(source_faces),
        fallback_reason=reason,
    )
    return SubjectAsset(
        fallback, sidecar, "0,0,100,100", "full-image-fallback"
    )


def circular_avatar(
    src: Path,
    asset_dir: Path,
    *,
    size: int = 320,
    white_px: int = 12,
    black_px: int = 5,
) -> Path | None:
    """Center-crop a real image into a 605-style round avatar with two rings."""
    if size <= 0 or white_px < 0 or black_px < 0:
        raise ValueError("avatar dimensions must be positive")
    try:
        from PIL import Image, ImageDraw, ImageOps

        asset_dir.mkdir(parents=True, exist_ok=True)
        ring = white_px + black_px
        inner = size - ring * 2
        if inner <= 0:
            raise ValueError("avatar rings leave no room for the image")
        with Image.open(src) as opened:
            portrait = ImageOps.fit(opened.convert("RGB"), (inner, inner))
        result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(result)
        draw.ellipse((0, 0, size - 1, size - 1), fill=INK)
        draw.ellipse(
            (black_px, black_px, size - black_px - 1, size - black_px - 1),
            fill=WHITE,
        )
        mask = Image.new("L", (inner, inner), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)
        result.paste(portrait, (ring, ring), mask)
        fingerprint = hashlib.sha256(
            Path(src).read_bytes()
            + f"avatar:{size}:{white_px}:{black_px}".encode()
        ).hexdigest()[:12]
        dst = asset_dir / f"{Path(src).stem}-{fingerprint}-avatar.png"
        result.save(dst)
        return dst
    except ValueError:
        raise
    except Exception:
        return None
