from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from PIL import Image


SCRIPT = (
    Path(__file__).parents[1]
    / ".trae"
    / "skills"
    / "xiaohuoche-ribao-html"
    / "scripts"
    / "export_html_png.py"
)
spec = importlib.util.spec_from_file_location("export_html_png", SCRIPT)
assert spec and spec.loader
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_slice_ranges_adds_overlap_and_avoids_tiny_tail() -> None:
    ranges = exporter.slice_ranges(total_height=5000, slice_height=2200, overlap=80)

    assert ranges == [(0, 2200), (2120, 4320), (4240, 5000)]
    assert all(bottom > top for top, bottom in ranges)


def test_slice_ranges_can_disable_slicing() -> None:
    assert exporter.slice_ranges(9000, 0, 80) == [(0, 9000)]


def test_slice_ranges_rejects_invalid_overlap() -> None:
    with pytest.raises(ValueError, match="overlap"):
        exporter.slice_ranges(5000, 1000, 1000)


def test_source_url_accepts_local_html(tmp_path: Path) -> None:
    html = tmp_path / "index.html"
    html.write_text("<html></html>", encoding="utf-8")

    assert exporter.source_url(str(html)) == html.resolve().as_uri()


def test_safe_name_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        exporter.safe_name("。。。")


def test_capture_css_hides_review_controls() -> None:
    assert "[data-export-ignore]" in exporter.CAPTURE_CSS


def test_smart_slice_ranges_prefers_section_boundaries() -> None:
    ranges = exporter.smart_slice_ranges(
        total_height=7749,
        slice_height=2200,
        overlap=80,
        boundaries=[1179, 2499, 3560, 4620, 5523, 7053, 7749],
    )

    assert ranges == [(0, 2499), (2419, 4620), (4540, 7053), (6973, 7749)]


def test_export_pngs_can_crop_wechat_cover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_capture(source: str, output: Path, width: int, wait_ms: int) -> list[int]:
        Image.new("RGB", (width, 1200), "#00a8ff").save(output)
        return []

    monkeypatch.setattr(exporter, "capture", fake_capture)
    outputs = exporter.export_pngs(
        source="unused.html",
        output_dir=tmp_path,
        name="wechat-cover",
        width=1080,
        slice_height=0,
        crop_height=460,
    )

    assert outputs == [tmp_path / "wechat-cover-full.png"]
    with Image.open(outputs[0]) as image:
        assert image.size == (1080, 460)


def test_export_retina_sections_writes_only_2160_by_2000_png_slices(
    tmp_path: Path,
) -> None:
    html = tmp_path / "index.html"
    html.write_text(
        """<html><style>html,body{margin:0}.screen{width:1080px;height:1000px}
        .a{background:#a6dbf5}.b{background:#eaff00}</style><body><main>
        <section class=\"screen a\"></section><section class=\"screen b\"></section>
        </main></body></html>""",
        encoding="utf-8",
    )

    outputs = exporter.export_section_pngs(
        source=str(html),
        output_dir=tmp_path / "slices" / "2160",
        width=1080,
        section_height=1000,
        device_scale_factor=2,
    )

    assert [path.name for path in outputs] == ["01.png", "02.png"]
    assert not list((tmp_path / "slices" / "2160").glob("*full*"))
    for path in outputs:
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == (2160, 2000)


def test_parser_exposes_section_only_retina_export() -> None:
    args = exporter.build_parser().parse_args(
        [
            "index.html",
            "--output-dir",
            "slices/2160",
            "--sections-only",
            "--section-height",
            "1000",
            "--device-scale-factor",
            "2",
        ]
    )

    assert args.sections_only is True
    assert args.section_height == 1000
    assert args.device_scale_factor == 2
