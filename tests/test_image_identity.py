from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from xzq.image_identity import ImageIdentityError, validate_image_identities
from xzq.renderer.subject_meta import write_subject_metadata


def _png(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), color).save(path)


def test_rejects_same_subject_source_across_body_and_cover(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    body_rendered = tmp_path / "body.png"
    cover_rendered = tmp_path / "cover.png"
    mask = tmp_path / "mask.png"
    _png(source, "red")
    _png(body_rendered, "red")
    _png(cover_rendered, "red")
    _png(mask, "white")
    body_meta = write_subject_metadata(
        source, body_rendered, mask_path=mask, mode="cutout",
        source_face_count=0, rendered_face_count=0,
    )
    cover_meta = write_subject_metadata(
        source, cover_rendered, mask_path=mask, mode="cutout",
        source_face_count=0, rendered_face_count=0,
    )
    zone = "0,0,100,100"
    body = tmp_path / "index.html"
    cover = tmp_path / "cover.html"
    body.write_text(
        f'<img src="{body_rendered.name}" data-subject-meta="{body_meta.name}" '
        f'data-subject-zone="{zone}">', encoding="utf-8",
    )
    cover.write_text(
        f'<img src="{cover_rendered.name}" data-subject-meta="{cover_meta.name}" '
        f'data-subject-zone="{zone}">', encoding="utf-8",
    )

    with pytest.raises(ImageIdentityError, match="同源图片重复"):
        validate_image_identities([body, cover])


def test_rejects_byte_identical_images_with_different_names(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "renamed.png"
    _png(first, "blue")
    second.write_bytes(first.read_bytes())
    body = tmp_path / "index.html"
    cover = tmp_path / "cover.html"
    body.write_text(f'<img src="{first.name}">', encoding="utf-8")
    cover.write_text(f'<img src="{second.name}">', encoding="utf-8")

    with pytest.raises(ImageIdentityError, match="同源图片重复"):
        validate_image_identities([body, cover])


def test_accepts_unique_sources_and_reports_count(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _png(first, "blue")
    _png(second, "green")
    page = tmp_path / "index.html"
    page.write_text(
        f'<img src="{first.name}"><img src="{second.name}">', encoding="utf-8",
    )

    report = validate_image_identities([page])

    assert report.image_count == 2
    assert report.identity_count == 2


def test_rejects_external_image_that_cannot_be_hashed(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text(
        '<img src="https://example.com/remote.png">', encoding="utf-8"
    )

    with pytest.raises(ImageIdentityError, match="必须是可哈希的本地文件"):
        validate_image_identities([page])


def test_rejects_subject_without_sidecar_file(tmp_path: Path) -> None:
    rendered = tmp_path / "person.png"
    _png(rendered, "red")
    page = tmp_path / "index.html"
    page.write_text(
        f'<img src="{rendered.name}" data-subject-meta="missing.subject.json">',
        encoding="utf-8",
    )

    with pytest.raises(ImageIdentityError, match="人物 sidecar 不存在"):
        validate_image_identities([page])


def test_rejects_subject_when_source_bytes_change(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    rendered = tmp_path / "person.png"
    mask = tmp_path / "mask.png"
    _png(source, "red")
    _png(rendered, "red")
    _png(mask, "white")
    meta = write_subject_metadata(
        source, rendered, mask_path=mask, mode="cutout",
        source_face_count=0, rendered_face_count=0,
    )
    page = tmp_path / "index.html"
    page.write_text(
        f'<img src="{rendered.name}" data-subject-meta="{meta.name}">',
        encoding="utf-8",
    )
    _png(source, "blue")

    with pytest.raises(ImageIdentityError, match="人物源图哈希不一致"):
        validate_image_identities([page])
