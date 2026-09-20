#!/usr/bin/env python3
"""将本地 HTML 或网页导出为 1080px 整页 PNG 与公众号分屏 PNG。"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image


CAPTURE_CSS = """
*, *::before, *::after {
  animation: none !important;
  transition: none !important;
  caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }
.panel { opacity: 1 !important; }
.progress, .top-btn, .lightbox, [data-export-ignore] { display: none !important; }
"""


def source_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "file"}:
        return value
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"找不到 HTML 文件：{path}")
    return path.as_uri()


def safe_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip()).strip("-.")
    if not name:
        raise ValueError("导出名称不能为空")
    return name


def slice_ranges(total_height: int, slice_height: int, overlap: int) -> list[tuple[int, int]]:
    if total_height <= 0:
        raise ValueError("图片高度必须大于 0")
    if slice_height <= 0:
        return [(0, total_height)]
    if overlap < 0 or overlap >= slice_height:
        raise ValueError("overlap 必须大于等于 0 且小于 slice-height")

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total_height:
        end = min(start + slice_height, total_height)
        ranges.append((start, end))
        if end == total_height:
            break
        next_start = end - overlap
        # 避免最后只剩一个很短的碎片；允许末屏略高于目标高度。
        if total_height - next_start < max(320, slice_height // 3):
            ranges[-1] = (start, total_height)
            break
        start = next_start
    return ranges


def smart_slice_ranges(
    total_height: int,
    slice_height: int,
    overlap: int,
    boundaries: list[int],
) -> list[tuple[int, int]]:
    """优先在页面 section 边界切图，找不到合适边界时再按固定高度切。"""
    if slice_height <= 0 or not boundaries:
        return slice_ranges(total_height, slice_height, overlap)
    if overlap < 0 or overlap >= slice_height:
        raise ValueError("overlap 必须大于等于 0 且小于 slice-height")

    points = sorted({point for point in boundaries if 0 < point < total_height})
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total_height:
        if total_height - start <= int(slice_height * 1.2):
            ranges.append((start, total_height))
            break
        minimum = start + int(slice_height * 0.55)
        maximum = start + int(slice_height * 1.2)
        candidates = [point for point in points if minimum <= point <= maximum]
        end = max(candidates) if candidates else min(start + slice_height, total_height)
        ranges.append((start, end))
        start = end - overlap
    return ranges


def _open_page(browser, source: str, width: int, wait_ms: int, device_scale_factor: int):
    page = browser.new_page(
        viewport={"width": width, "height": 1200},
        device_scale_factor=device_scale_factor,
    )
    page.goto(source_url(source), wait_until="domcontentloaded", timeout=60_000)
    page.add_style_tag(content=CAPTURE_CSS)
    page.wait_for_function(
        """() => Array.from(document.images).every(
            image => !image.getAttribute('src') || (image.complete && image.naturalWidth > 0)
        )""",
        timeout=60_000,
    )
    page.evaluate(
        """async () => {
            if (document.fonts && document.fonts.ready) await document.fonts.ready;
            window.scrollTo(0, 0);
        }"""
    )
    if wait_ms:
        page.wait_for_timeout(wait_ms)
    return page


def capture(source: str, output: Path, width: int, wait_ms: int) -> list[int]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 Playwright，请先执行：pip install playwright && playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = _open_page(browser, source, width, wait_ms, device_scale_factor=1)
        boundaries = page.locator("main > section").evaluate_all(
            "elements => elements.map(element => Math.round(element.offsetTop + element.offsetHeight))"
        )
        page.screenshot(path=str(output), full_page=True, animations="disabled")
        browser.close()
        return boundaries


def export_section_pngs(
    source: str,
    output_dir: Path,
    width: int = 1080,
    section_height: int = 1000,
    device_scale_factor: int = 1,
    wait_ms: int = 500,
) -> list[Path]:
    """逐个导出 main 直属 section；不会生成整页图。"""
    if width <= 0 or section_height <= 0:
        raise ValueError("分屏宽高必须大于 0")
    if device_scale_factor not in {1, 2}:
        raise ValueError("device-scale-factor 仅支持 1 或 2")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 Playwright，请先执行：pip install playwright && playwright install chromium"
        ) from exc

    outputs: list[Path] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = _open_page(browser, source, width, wait_ms, device_scale_factor)
        sections = page.locator("main > section")
        count = sections.count()
        if count == 0:
            browser.close()
            raise RuntimeError("页面缺少 main > section，无法导出分屏")
        document_height = int(
            page.evaluate(
                "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
            )
        )
        if document_height <= 0:
            browser.close()
            raise RuntimeError("页面高度不可用，无法导出分屏")
        page.set_viewport_size({"width": width, "height": document_height})
        expected_css = {"width": width, "height": section_height}
        for index in range(count):
            section = sections.nth(index)
            box = section.bounding_box()
            if box is None:
                browser.close()
                raise RuntimeError(f"第 {index + 1} 屏不可见，无法导出")
            actual_css = {"width": round(box["width"]), "height": round(box["height"])}
            if abs(box["width"] - width) > 1 or abs(box["height"] - section_height) > 1:
                browser.close()
                raise RuntimeError(
                    f"第 {index + 1} 屏 CSS 尺寸错误：期望 {width}×{section_height}，"
                    f"实际 {actual_css['width']}×{actual_css['height']}"
                )
            path = output_dir / f"{index + 1:02d}.png"
            # Element screenshots may include a one-pixel transformed edge. Crop the
            # fixed section canvas explicitly so both DPR variants stay exact.
            page.screenshot(
                path=str(path),
                clip={
                    "x": box["x"],
                    "y": box["y"],
                    "width": width,
                    "height": section_height,
                },
                animations="disabled",
            )
            with Image.open(path) as image:
                expected_pixels = (width * device_scale_factor, section_height * device_scale_factor)
                if image.format != "PNG" or image.size != expected_pixels:
                    browser.close()
                    raise RuntimeError(
                        f"{path.name} 输出异常：期望 PNG {expected_pixels[0]}×{expected_pixels[1]}，"
                        f"实际 {image.format} {image.width}×{image.height}"
                    )
            outputs.append(path)
        browser.close()
    return outputs


def export_pngs(
    source: str,
    output_dir: Path,
    name: str,
    width: int = 1080,
    slice_height: int = 2200,
    overlap: int = 80,
    wait_ms: int = 500,
    crop_height: int = 0,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(name)
    full_path = output_dir / f"{stem}-full.png"
    boundaries = capture(source, full_path, width, wait_ms)

    with Image.open(full_path) as image:
        if image.width != width:
            raise RuntimeError(f"导出宽度异常：期望 {width}px，实际 {image.width}px")
        if crop_height > 0:
            if image.height < crop_height:
                raise RuntimeError(
                    f"裁切高度异常：期望至少 {crop_height}px，实际 {image.height}px"
                )
            image.crop((0, 0, image.width, crop_height)).save(
                full_path, format="PNG", optimize=True
            )

    outputs = [full_path]
    if slice_height <= 0:
        return outputs

    with Image.open(full_path) as image:
        for index, (top, bottom) in enumerate(
            smart_slice_ranges(image.height, slice_height, overlap, boundaries), start=1
        ):
            part_path = output_dir / f"{stem}-{index:02d}.png"
            image.crop((0, top, image.width, bottom)).save(part_path, format="PNG", optimize=True)
            outputs.append(part_path)
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="本地 HTML 文件、file:// URL 或 http(s) URL")
    parser.add_argument("--output-dir", default="exports", help="导出目录")
    parser.add_argument("--name", default="xzq-ribao", help="输出文件名前缀")
    parser.add_argument("--width", type=int, default=1080, help="截图宽度，默认 1080")
    parser.add_argument(
        "--slice-height", type=int, default=2200, help="分屏高度；设为 0 仅导出整页图"
    )
    parser.add_argument("--overlap", type=int, default=80, help="相邻分屏重叠高度")
    parser.add_argument(
        "--crop-height",
        type=int,
        default=0,
        help="从顶部裁切整页图到指定高度；公众号封面可设为 460",
    )
    parser.add_argument("--wait-ms", type=int, default=500, help="资源加载后额外等待毫秒数")
    parser.add_argument(
        "--sections-only",
        action="store_true",
        help="逐个 main > section 导出分屏，不生成整页图",
    )
    parser.add_argument(
        "--section-height", type=int, default=1000, help="逐屏 CSS 高度，默认 1000"
    )
    parser.add_argument(
        "--device-scale-factor",
        type=int,
        choices=(1, 2),
        default=1,
        help="逐屏输出倍率；2 输出 2160×2000 Retina PNG",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.width <= 0:
        raise SystemExit("--width 必须大于 0")
    if args.crop_height < 0:
        raise SystemExit("--crop-height 不能小于 0")
    if args.sections_only:
        outputs = export_section_pngs(
            source=args.source,
            output_dir=Path(args.output_dir).expanduser().resolve(),
            width=args.width,
            section_height=args.section_height,
            device_scale_factor=args.device_scale_factor,
            wait_ms=max(0, args.wait_ms),
        )
    else:
        if args.device_scale_factor != 1:
            raise SystemExit("整页导出仅允许 device-scale-factor=1，禁止生成 2x 长图")
        outputs = export_pngs(
            source=args.source,
            output_dir=Path(args.output_dir).expanduser().resolve(),
            name=args.name,
            width=args.width,
            slice_height=args.slice_height,
            overlap=args.overlap,
            wait_ms=max(0, args.wait_ms),
            crop_height=args.crop_height,
        )
    print("导出完成：")
    for path in outputs:
        with Image.open(path) as image:
            print(f"- {path} ({image.width}x{image.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
