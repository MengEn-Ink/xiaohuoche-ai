"""Browser raster proof that no painted layer covers protected display copy."""

from __future__ import annotations

import contextlib
import io
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops


class CopyOcclusionError(RuntimeError):
    """The renderer could not prove that display copy is unobstructed."""


@dataclass(frozen=True)
class CopyProbeReport:
    copy_pixels: int
    sticker_pixels: int
    overlap_pixels: int
    sticker_overlap_pixels: int = 0
    image_overlap_pixels: int = 0


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@contextlib.contextmanager
def _serve(directory: Path):
    handler = partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _changed(left: bytes, right: bytes):
    try:
        import numpy as np
    except ImportError as exc:
        raise CopyOcclusionError("缺少 NumPy，无法执行文字光栅差分") from exc
    with Image.open(io.BytesIO(left)).convert("RGB") as a, Image.open(
        io.BytesIO(right)
    ).convert("RGB") as b:
        if a.size != b.size:
            raise CopyOcclusionError("文字光栅差分截图尺寸不一致")
        return np.asarray(ImageChops.difference(a, b)).max(axis=2) > 18


_KEEP_COPY_ONLY_JS = r"""
() => {
  const copy = Array.from(document.querySelectorAll('[data-occlusion="copy"]'));
  if (!copy.length) throw new Error('页面缺少受保护文字节点');
  const keep = new Set();
  for (const node of copy) {
    for (let current = node; current; current = current.parentElement) keep.add(current);
    for (const descendant of node.querySelectorAll('*')) keep.add(descendant);
  }
  for (const node of document.querySelectorAll('body *')) {
    if (!keep.has(node)) node.style.visibility = 'hidden';
  }
}
"""


def probe_copy_occlusion(
    html_path: Path, device_scale_factor: int = 1
) -> CopyProbeReport:
    """Compare the rendered page with a copy-only baseline at 1080 CSS px."""
    html_path = Path(html_path).resolve()
    if device_scale_factor not in {1, 2}:
        raise CopyOcclusionError("文字像素探针倍率仅支持 1 或 2")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CopyOcclusionError("缺少 Playwright，无法执行文字像素探针") from exc
    try:
        with _serve(html_path.parent) as base_url, sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": 1080, "height": 1200},
                    device_scale_factor=device_scale_factor,
                )
                page.goto(f"{base_url}/{html_path.name}", wait_until="networkidle")
                page.add_style_tag(
                    content=(
                        "*,*::before,*::after{animation:none!important;transition:none!important}"
                        "[data-export-ignore]{display:none!important}"
                    )
                )
                page.wait_for_function(
                    "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
                )
                height = int(page.evaluate(
                    "Math.max(document.documentElement.scrollHeight,document.body.scrollHeight)"
                ))
                if height <= 0:
                    raise CopyOcclusionError("文字像素探针无法取得页面高度")
                page.set_viewport_size({"width": 1080, "height": height})
                full = page.screenshot(full_page=True)
                page.add_style_tag(
                    content=".stk,.sticker,[data-occlusion='sticker']{visibility:hidden!important}"
                )
                no_stickers = page.screenshot(full_page=True)
                page.add_style_tag(
                    content=(
                        "img:not(.stk):not(.sticker):not([data-occlusion='sticker'])"
                        "{visibility:hidden!important}"
                    )
                )
                no_images = page.screenshot(full_page=True)
                # Build the authoritative clean baseline independently from the
                # sticker classes. Keeping only copy nodes and their ancestor
                # chain preserves layout while removing every sibling painter.
                page.evaluate(_KEEP_COPY_ONLY_JS)
                page.add_style_tag(
                    content=(
                        "body *::before,body *::after{content:none!important;"
                        "background:none!important;backdrop-filter:none!important;"
                        "box-shadow:none!important}"
                        "body *{box-shadow:none!important}"
                    )
                )
                clean_copy = page.screenshot(full_page=True)
                page.add_style_tag(
                    content="[data-occlusion='copy']{visibility:hidden!important}"
                )
                clean_without_copy = page.screenshot(full_page=True)
            finally:
                browser.close()
    except CopyOcclusionError:
        raise
    except Exception as exc:
        raise CopyOcclusionError(f"文字像素探针运行失败：{exc}") from exc

    copy_mask = _changed(clean_copy, clean_without_copy)
    sticker_mask = _changed(full, no_stickers)
    image_mask = _changed(no_stickers, no_images)
    all_occluders = _changed(full, clean_copy)
    copy_pixels = int(copy_mask.sum())
    sticker_pixels = int(sticker_mask.sum())
    sticker_overlap = int((copy_mask & sticker_mask).sum())
    image_overlap = int((copy_mask & image_mask).sum())
    overlap_pixels = int((copy_mask & all_occluders).sum())
    if copy_pixels == 0:
        raise CopyOcclusionError("文字光栅差分没有得到有效文字墨迹")
    if sticker_overlap:
        raise CopyOcclusionError(
            f"贴纸遮挡大字：命中 {sticker_overlap}/{copy_pixels} 个文字像素"
        )
    if image_overlap:
        raise CopyOcclusionError(
            f"图片遮挡大字：命中 {image_overlap}/{copy_pixels} 个文字像素"
        )
    if overlap_pixels / copy_pixels > 0.005:
        raise CopyOcclusionError(
            f"任意绘制层遮挡大字：命中 {overlap_pixels}/{copy_pixels} 个文字像素"
        )
    return CopyProbeReport(
        copy_pixels, sticker_pixels, overlap_pixels, sticker_overlap, image_overlap
    )
