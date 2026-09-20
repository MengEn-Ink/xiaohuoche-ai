from pathlib import Path

import pytest
from PIL import Image

from xzq.copy_probe import CopyOcclusionError, probe_copy_occlusion


def _fixture(tmp_path: Path, sticker_left: int) -> Path:
    sticker = tmp_path / "sticker.png"
    Image.new("RGBA", (180, 180), (220, 20, 20, 255)).save(sticker)
    html = tmp_path / "index.html"
    html.write_text(
        "<style>html,body{margin:0;width:1080px;overflow:hidden}.stage{position:relative;width:1080px;height:600px;background:#a6dbf5}"
        ".copy{position:absolute;left:80px;top:120px;font:900 96px sans-serif;color:#fff;"
        "-webkit-text-stroke:7px #000}.stk{position:absolute;top:100px;width:180px;height:180px}</style>"
        f"<div class='stage'><div class='copy' data-occlusion='copy'>妙峰山科技</div>"
        f"<img class='stk' style='left:{sticker_left}px' src='{sticker.name}'></div>",
        encoding="utf-8",
    )
    return html


def _ordinary_block_fixture(tmp_path: Path) -> Path:
    html = tmp_path / "ordinary-block.html"
    html.write_text(
        "<style>html,body{margin:0;width:1080px;overflow:hidden}.stage{position:relative;width:1080px;height:600px;background:#a6dbf5}"
        ".copy{position:absolute;left:80px;top:120px;width:940px;font:900 96px sans-serif;color:#fff;"
        "-webkit-text-stroke:7px #000}.blocker{position:absolute;z-index:9;left:110px;top:130px;"
        "width:360px;height:120px;background:#e11}</style>"
        "<div class='stage'><div class='copy' data-occlusion='copy'>妙峰山科技</div>"
        "<div class='blocker'></div></div>",
        encoding="utf-8",
    )
    return html


def test_copy_probe_allows_sticker_outside_copy(tmp_path: Path):
    report = probe_copy_occlusion(_fixture(tmp_path, 900))
    assert report.copy_pixels > 0
    assert report.overlap_pixels == 0


def test_copy_probe_allows_clean_copy_at_retina_scale(tmp_path: Path):
    report = probe_copy_occlusion(_fixture(tmp_path, 900), device_scale_factor=2)
    assert report.copy_pixels > 0
    assert report.overlap_pixels == 0


def test_copy_probe_rejects_sticker_covering_copy(tmp_path: Path):
    with pytest.raises(CopyOcclusionError, match="贴纸遮挡大字"):
        probe_copy_occlusion(_fixture(tmp_path, 110))


def test_copy_probe_rejects_ordinary_block_covering_copy(tmp_path: Path):
    with pytest.raises(CopyOcclusionError, match="遮挡大字"):
        probe_copy_occlusion(_ordinary_block_fixture(tmp_path))


def test_copy_probe_rejects_tiny_image_overlap_below_global_threshold(tmp_path: Path):
    image = tmp_path / "tiny.png"
    Image.new("RGBA", (12, 12), (220, 20, 20, 255)).save(image)
    html = tmp_path / "tiny-image.html"
    html.write_text(
        "<style>html,body{margin:0;width:1080px;overflow:hidden}.stage{position:relative;width:1080px;height:600px;background:#a6dbf5}"
        ".copy{position:absolute;left:80px;top:120px;width:940px;font:900 96px sans-serif;color:#fff;"
        "-webkit-text-stroke:7px #000}.tiny{position:absolute;z-index:9;left:91px;top:145px;"
        "width:12px;height:12px}</style>"
        "<div class='stage'><div class='copy' data-occlusion='copy'>妙峰山科技</div>"
        f"<img class='tiny' src='{image.name}'></div>",
        encoding="utf-8",
    )

    with pytest.raises(CopyOcclusionError, match="图片遮挡大字"):
        probe_copy_occlusion(html)
