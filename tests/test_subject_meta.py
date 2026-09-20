from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from xzq.renderer.subject_meta import (
    SubjectMetadataError,
    validate_subject_metadata,
    write_subject_metadata,
)


def _subject(path: Path, size=(20, 20), box=(4, 3, 16, 18)) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for x in range(box[0], box[2]):
        for y in range(box[1], box[3]):
            image.putpixel((x, y), (30, 80, 130, 255))
    image.save(path)


def test_subject_metadata_uses_alpha_and_ignores_transparent_padding(tmp_path: Path):
    source = tmp_path / "source.png"
    rendered = tmp_path / "person.png"
    _subject(source)
    _subject(rendered)

    sidecar = write_subject_metadata(
        source, rendered, mask_path=rendered, mode="cutout",
        source_face_count=1, rendered_face_count=1,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))

    assert payload["subject_zone"] == [20.0, 15.0, 60.0, 75.0]
    assert payload["source_sha256"] == payload["rendered_sha256"]
    assert payload["sample_points"]
    assert all(20 <= point[0] < 80 and 15 <= point[1] < 90 for point in payload["sample_points"])
    assert all(not (point[0] < 20 or point[1] < 15) for point in payload["sample_points"])


def test_subject_metadata_rejects_tampering_and_zone_drift(tmp_path: Path):
    source = tmp_path / "source.png"
    rendered = tmp_path / "person.png"
    _subject(source)
    _subject(rendered)
    sidecar = write_subject_metadata(
        source, rendered, mask_path=rendered, mode="cutout",
        source_face_count=1, rendered_face_count=1,
    )

    validate_subject_metadata(rendered, sidecar, "21,15,60,75")
    with pytest.raises(SubjectMetadataError, match="主体区与 sidecar 不一致"):
        validate_subject_metadata(rendered, sidecar, "23,15,60,75")

    with Image.open(rendered) as opened:
        changed = opened.convert("RGBA")
    changed.putpixel((0, 0), (255, 0, 0, 255))
    changed.save(rendered)
    with pytest.raises(SubjectMetadataError, match="渲染资产哈希不一致"):
        validate_subject_metadata(rendered, sidecar, "20,15,60,75")


def test_subject_metadata_rejects_source_change_and_algorithm_change(tmp_path: Path):
    source = tmp_path / "source.png"
    rendered = tmp_path / "person.png"
    _subject(source)
    _subject(rendered)
    sidecar = write_subject_metadata(
        source, rendered, mask_path=rendered, mode="cutout",
        source_face_count=1, rendered_face_count=1,
    )
    source.write_bytes(b"changed")
    with pytest.raises(SubjectMetadataError, match="源图哈希不一致"):
        validate_subject_metadata(rendered, sidecar, "20,15,60,75")

    _subject(source)
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["algorithm_version"] = "old"
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SubjectMetadataError, match="算法版本不一致"):
        validate_subject_metadata(rendered, sidecar, "20,15,60,75")


def test_subject_metadata_rejects_rewritten_zone_even_when_html_matches(tmp_path: Path):
    source = tmp_path / "source.png"
    rendered = tmp_path / "person.png"
    _subject(source)
    _subject(rendered)
    sidecar = write_subject_metadata(
        source, rendered, mask_path=rendered, mode="cutout",
        source_face_count=1, rendered_face_count=1,
    )
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["subject_zone"] = [0, 0, 10, 10]
    payload["sample_points"] = [[1, 1]]
    sidecar.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SubjectMetadataError, match="几何数据不一致"):
        validate_subject_metadata(rendered, sidecar, "0,0,10,10")


def test_subject_metadata_rejects_missing_sidecar(tmp_path: Path):
    rendered = tmp_path / "person.png"
    _subject(rendered)
    with pytest.raises(SubjectMetadataError, match="缺少人物主体 sidecar"):
        validate_subject_metadata(rendered, tmp_path / "missing.json", "20,15,60,75")


def test_subject_metadata_rejects_cutout_that_loses_faces(tmp_path: Path):
    source = tmp_path / "source.png"
    rendered = tmp_path / "person.png"
    _subject(source)
    _subject(rendered)
    with pytest.raises(SubjectMetadataError, match="抠图后人脸数减少"):
        write_subject_metadata(
            source, rendered, mask_path=rendered, mode="cutout",
            source_face_count=2, rendered_face_count=1,
        )


def test_full_image_fallback_hashes_alpha_and_samples_a_24_by_24_grid(tmp_path: Path):
    source = tmp_path / "source.jpg"
    rendered = tmp_path / "assets" / "source.jpg"
    rendered.parent.mkdir()
    Image.new("RGB", (20, 30), (30, 80, 130)).save(source)
    rendered.write_bytes(source.read_bytes())

    sidecar = write_subject_metadata(
        source, rendered, mask_path=rendered, mode="full-image-fallback",
        source_face_count=1, rendered_face_count=1, fallback_reason="cutout-failed",
    )
    payload = validate_subject_metadata(rendered, sidecar, "0,0,100,100")

    assert payload["source"] == "../source.jpg"
    assert len(payload["sample_points"]) == 24 * 24
