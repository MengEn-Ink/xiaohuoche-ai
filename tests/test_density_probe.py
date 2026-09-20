from pathlib import Path

import pytest
from PIL import Image

from xzq.density_probe import DensityProbeError, probe_content_density


def _fixture(tmp_path: Path, *, dense: bool, height: int = 600) -> Path:
    blocks = "".join(
        f"<div class='copy' data-occlusion='copy' style='top:{top}px'>高密度信息</div>"
        for top in ((20, 140, 260, 380) if dense else (240,))
    )
    html = tmp_path / "index.html"
    html.write_text(
        "<style>html,body{margin:0}.page{width:1080px;background:#a6dbf5}.scr{position:relative;width:1080px}"
        f".copy{{position:absolute;left:40px;width:960px;height:100px;background:#111;color:#fff;font:80px sans-serif}}</style>"
        f"<main class='page'><section class='scr' style='height:{height}px'>{blocks}</section></main>",
        encoding="utf-8",
    )
    return html


def test_density_probe_rejects_sparse_non_screenshot_section(tmp_path: Path):
    with pytest.raises(DensityProbeError, match="信息填充率不足"):
        probe_content_density(_fixture(tmp_path, dense=False))


def test_density_probe_does_not_count_decorative_color_block_as_content(
    tmp_path: Path,
):
    html = _fixture(tmp_path, dense=False)
    html.write_text(
        html.read_text(encoding="utf-8").replace(
            "</section>",
            "<div style='position:absolute;inset:0;background:#ff214f'></div></section>",
        ),
        encoding="utf-8",
    )

    with pytest.raises(DensityProbeError, match="信息填充率不足"):
        probe_content_density(html)


def test_density_probe_accepts_dense_section(tmp_path: Path):
    report = probe_content_density(_fixture(tmp_path, dense=True))
    assert report.total_height == 600
    assert report.sections[0].fill_ratio >= 0.35


def test_density_probe_rejects_document_over_height_limit(tmp_path: Path):
    with pytest.raises(DensityProbeError, match="总高超过 7400px"):
        probe_content_density(_fixture(tmp_path, dense=True, height=7401))


def test_density_probe_skips_shot_stack_ratio_but_keeps_total_height(tmp_path: Path):
    html = _fixture(tmp_path, dense=False)
    html.write_text(
        html.read_text(encoding="utf-8").replace("class='scr'", "class='scr shot-stack'"),
        encoding="utf-8",
    )
    report = probe_content_density(html)
    assert report.sections[0].is_screenshot is True


def test_density_probe_rejects_copy_clipped_by_its_section(tmp_path: Path):
    html = tmp_path / "clipped-copy.html"
    html.write_text(
        "<style>html,body{margin:0}.scr{position:relative;width:1080px;height:250px;overflow:hidden;"
        "background:#a6dbf5}.copy{position:absolute;left:40px;top:180px;width:960px;height:120px;"
        "background:#111;color:#fff;font:80px sans-serif}</style>"
        "<section class='scr'><div class='copy' data-occlusion='copy'>封面导读完整显示</div></section>",
        encoding="utf-8",
    )

    with pytest.raises(DensityProbeError, match="文字超出所属屏"):
        probe_content_density(html)


@pytest.mark.parametrize(
    ("markup", "message"),
    [
        ("<img src='small.png' class='cut' data-subject='people' style='width:120px;height:180px'>", "人物素材渲染过小"),
        ("<img src='small.png' class='shot stacked' style='width:700px;height:300px'>", "聊天截图渲染过小"),
        ("<img src='small.png' class='ph flow-extra' style='width:360px;height:180px'>", "证据图渲染过小"),
    ],
)
def test_density_probe_rejects_undersized_content_images(
    tmp_path: Path, markup: str, message: str
):
    Image.new("RGB", (32, 32), "#ef3340").save(tmp_path / "small.png")
    html = tmp_path / "small-image.html"
    html.write_text(
        "<style>html,body{margin:0}.scr{position:relative;width:1080px;height:600px;"
        "background:#a6dbf5}.copy{font:100px sans-serif;background:#111;color:#fff}</style>"
        f"<section class='scr'><div class='copy' data-occlusion='copy'>信息密度测试</div>{markup}</section>",
        encoding="utf-8",
    )

    with pytest.raises(DensityProbeError, match=message):
        probe_content_density(html)
