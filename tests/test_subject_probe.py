from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from xzq.renderer.subject_meta import write_subject_metadata
from xzq.subject_probe import SubjectOcclusionError, probe_subject_occlusion


def _fixture(tmp_path: Path, overlay: str = "") -> Path:
    source = tmp_path / "source.png"
    person = tmp_path / "person.png"
    image = Image.new("RGBA", (120, 180), (0, 0, 0, 0))
    for x in range(20, 100):
        for y in range(10, 175):
            image.putpixel((x, y), (40, 100, 180, 255))
    image.save(source)
    image.save(person)
    meta = write_subject_metadata(
        source, person, mask_path=person, mode="cutout",
        source_face_count=0, rendered_face_count=0,
    )
    html = tmp_path / "index.html"
    html.write_text(
        "<style>html,body{margin:0}.stage{position:relative;width:1080px;height:800px}"
        ".person{position:absolute;left:240px;top:80px;width:400px;height:600px;object-fit:contain}"
        + overlay
        + "</style><div class='stage'><figure><img class='person' data-subject='people' "
        + f"data-subject-zone='16.6667,5.5556,66.6667,91.6667' data-subject-meta='{meta.name}' src='{person.name}'></figure>"
        + ("<div class='cover'>挡住人物</div>" if overlay else "")
        + "</div>",
        encoding="utf-8",
    )
    return html


def test_probe_allows_clean_subject(tmp_path: Path):
    report = probe_subject_occlusion(_fixture(tmp_path))
    assert report.checked_points > 0
    assert report.occlusions == []


def test_probe_allows_clean_subject_at_retina_scale(tmp_path: Path):
    report = probe_subject_occlusion(_fixture(tmp_path), device_scale_factor=2)
    assert report.checked_points > 0
    assert report.occlusions == []


def test_probe_ignores_export_only_review_ui(tmp_path: Path):
    html = _fixture(
        tmp_path,
        ".cover{position:fixed;z-index:99;inset:0;background:white}",
    )
    content = html.read_text(encoding="utf-8").replace(
        "<div class='cover'>挡住人物</div>",
        "<div class='cover' data-export-ignore='review-ui'>审阅工具</div>",
    )
    html.write_text(content, encoding="utf-8")

    report = probe_subject_occlusion(html)

    assert report.occlusions == []


def test_probe_rejects_text_covering_subject(tmp_path: Path):
    html = _fixture(
        tmp_path,
        ".cover{position:absolute;z-index:3;left:300px;top:100px;width:250px;height:500px;background:red}",
    )
    with pytest.raises(SubjectOcclusionError, match="人物主体被遮挡"):
        probe_subject_occlusion(html)


def test_l1_locates_blocker_below_the_initial_viewport(tmp_path: Path):
    html = _fixture(
        tmp_path,
        ".stage{margin-top:2500px}.cover{position:absolute;z-index:3;left:300px;top:100px;width:250px;height:500px;background:red}",
    )

    with pytest.raises(SubjectOcclusionError, match=r"被 div 覆盖"):
        probe_subject_occlusion(html)


def test_probe_fail_closed_when_playwright_import_is_unavailable(tmp_path: Path, monkeypatch):
    import builtins

    html = _fixture(tmp_path)
    original_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(SubjectOcclusionError, match="缺少 Playwright"):
        probe_subject_occlusion(html)


def test_probe_rejects_pointer_events_none_gradient(tmp_path: Path):
    html = _fixture(
        tmp_path,
        ".cover{position:absolute;pointer-events:none;z-index:3;left:300px;top:100px;width:250px;height:500px;background-image:linear-gradient(red,black)}",
    )
    with pytest.raises(SubjectOcclusionError, match="人物主体被遮挡"):
        probe_subject_occlusion(html)


def test_probe_rejects_pseudo_element_cover(tmp_path: Path):
    html = _fixture(
        tmp_path,
        "figure{position:absolute;inset:0}figure::before{content:'水印压脸';position:absolute;z-index:4;left:300px;top:100px;width:250px;height:500px;background:rgba(0,0,0,.55)}",
    )
    with pytest.raises(SubjectOcclusionError, match="人物主体被遮挡"):
        probe_subject_occlusion(html)


def test_probe_rejects_box_shadow_crossing_subject(tmp_path: Path):
    html = _fixture(
        tmp_path,
        ".cover{position:absolute;z-index:3;left:80px;top:200px;width:100px;height:300px;background:red;box-shadow:180px 0 0 0 black}",
    )
    with pytest.raises(SubjectOcclusionError, match="人物主体被遮挡"):
        probe_subject_occlusion(html)


def test_probe_raster_layer_checks_all_alpha_pixels_between_grid_points(tmp_path: Path):
    html = _fixture(
        tmp_path,
        ".cover{position:absolute;z-index:3;left:80px;top:120px;width:4px;height:500px;background:red;box-shadow:235px 0 0 0 black}",
    )
    with pytest.raises(SubjectOcclusionError, match="光栅差分"):
        probe_subject_occlusion(html)
