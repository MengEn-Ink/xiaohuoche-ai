"""Browser raster gate for real-content density in raw日报 sections."""

from __future__ import annotations

import contextlib
import io
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops

MIN_NON_SCREENSHOT_FILL = 0.35
MAX_DOCUMENT_HEIGHT = 7400
MIN_PEOPLE_WIDTH = 432
MIN_PEOPLE_HEIGHT_RATIO = 0.55
MIN_STACKED_SHOT_WIDTH = 810
MIN_FLOW_EXTRA_WIDTH = 480


class DensityProbeError(RuntimeError):
    """Rendered content density could not be proved or is below the contract."""


@dataclass(frozen=True)
class SectionDensity:
    index: int
    classes: str
    top: int
    height: int
    fill_ratio: float
    is_screenshot: bool


@dataclass(frozen=True)
class RenderedImage:
    section: int
    kind: str
    source: str
    width: float
    height: float
    section_height: float


@dataclass(frozen=True)
class DensityProbeReport:
    total_height: int
    sections: list[SectionDensity]
    images: list[RenderedImage] = None

    def __post_init__(self) -> None:
        if self.images is None:
            object.__setattr__(self, "images", [])


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


def _measure(full: bytes, baseline: bytes, geometry: list[dict[str, object]]):
    try:
        import numpy as np
    except ImportError as exc:
        raise DensityProbeError("缺少 NumPy，无法执行信息密度光栅差分") from exc
    with Image.open(io.BytesIO(full)).convert("RGB") as normal, Image.open(
        io.BytesIO(baseline)
    ).convert("RGB") as empty:
        if normal.size != empty.size:
            raise DensityProbeError("密度探针截图尺寸不一致")
        changed = np.asarray(ImageChops.difference(normal, empty)).max(axis=2) > 18
        sections = []
        for raw in geometry:
            top = max(0, int(round(float(raw["top"]))))
            height = max(0, int(round(float(raw["height"]))))
            bottom = min(changed.shape[0], top + height)
            if height <= 0 or bottom <= top:
                raise DensityProbeError(f"第 {int(raw['index'])} 屏几何尺寸无效")
            crop = changed[top:bottom, :1080]
            ratio = float(crop.mean()) if crop.size else 0.0
            sections.append(
                SectionDensity(
                    int(raw["index"]), str(raw["classes"]), top, bottom - top,
                    ratio, bool(raw["is_screenshot"]),
                )
            )
        return sections


def probe_content_density(html_path: Path) -> DensityProbeReport:
    """Measure true copy/image/sticker pixels per section at 1080 CSS pixels."""
    html_path = Path(html_path).resolve()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DensityProbeError("缺少 Playwright，无法执行信息密度探针") from exc
    try:
        with _serve(html_path.parent) as base_url, sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1080, "height": 1200})
                page.goto(f"{base_url}/{html_path.name}", wait_until="networkidle")
                page.add_style_tag(content=(
                    "*,*::before,*::after{animation:none!important;transition:none!important}"
                    "[data-export-ignore]{display:none!important}"
                ))
                page.wait_for_function(
                    "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
                )
                total_height = int(page.evaluate(
                    "Math.ceil(Math.max(...Array.from(document.querySelectorAll('section.scr')).map("
                    "node => node.getBoundingClientRect().bottom + scrollY)))"
                ))
                if total_height <= 0:
                    raise DensityProbeError("信息密度探针无法取得页面高度")
                page.set_viewport_size({"width": 1080, "height": total_height})
                geometry = page.evaluate("""() => Array.from(document.querySelectorAll('section.scr')).map((node, i) => {
                    const rect = node.getBoundingClientRect();
                    const images = Array.from(node.querySelectorAll('img')).map(img => {
                      const box = img.getBoundingClientRect();
                      let kind = null;
                      if (img.matches('.cut,[data-subject="people"]')) kind = 'people';
                      else if (img.matches('.shot.stacked')) kind = 'stacked-shot';
                      else if (img.matches('.flow-extra')) kind = 'flow-extra';
                      return kind ? {kind, src:img.getAttribute('src') || '', width:box.width,
                                     height:box.height, sectionHeight:rect.height,
                                     objectFit:getComputedStyle(img).objectFit} : null;
                    }).filter(Boolean);
                    return {index:i+1, classes:node.className, top:rect.top+scrollY, height:rect.height,
                            is_screenshot:node.classList.contains('shot-stack'),
                            images,
                            copy_overflows:Array.from(node.querySelectorAll('[data-occlusion="copy"]')).flatMap(copy => {
                              const box = copy.getBoundingClientRect();
                              const epsilon = 1;
                              return box.left < rect.left-epsilon || box.top < rect.top-epsilon ||
                                     box.right > rect.right+epsilon || box.bottom > rect.bottom+epsilon
                                ? [{text:(copy.textContent || '').trim().slice(0,40), left:box.left, top:box.top,
                                    right:box.right, bottom:box.bottom, sectionBottom:rect.bottom}] : [];
                            })};
                })""")
                if not geometry:
                    raise DensityProbeError("信息密度探针未找到 section.scr")
                overflow = next(
                    (item for section in geometry for item in section["copy_overflows"]),
                    None,
                )
                if overflow:
                    raise DensityProbeError(
                        f"文字超出所属屏：{overflow['text']}（bottom={overflow['bottom']:.1f} > "
                        f"section bottom={overflow['sectionBottom']:.1f}）"
                    )
                for section in geometry:
                    for rendered in section["images"]:
                        width = float(rendered["width"])
                        height = float(rendered["height"])
                        section_height = float(rendered["sectionHeight"])
                        src = rendered["src"] or "<missing-src>"
                        if rendered["kind"] == "people" and (
                            width < MIN_PEOPLE_WIDTH
                            or height < section_height * MIN_PEOPLE_HEIGHT_RATIO
                        ):
                            raise DensityProbeError(
                                f"人物素材渲染过小：{src} {width:.0f}×{height:.0f}px，"
                                f"要求宽≥{MIN_PEOPLE_WIDTH}px且高≥本屏{MIN_PEOPLE_HEIGHT_RATIO:.0%}"
                            )
                        if rendered["kind"] == "stacked-shot" and width < MIN_STACKED_SHOT_WIDTH:
                            raise DensityProbeError(
                                f"聊天截图渲染过小：{src} 宽{width:.0f}px < {MIN_STACKED_SHOT_WIDTH}px"
                            )
                        if rendered["kind"] == "flow-extra" and width < MIN_FLOW_EXTRA_WIDTH:
                            raise DensityProbeError(
                                f"证据图渲染过小：{src} 宽{width:.0f}px < {MIN_FLOW_EXTRA_WIDTH}px"
                            )
                full = page.screenshot(full_page=True)
                page.add_style_tag(content=(
                    "[data-occlusion='copy'],img,.stk,.sticker,.sticker-row,.sig"
                    "{visibility:hidden!important}"
                ))
                baseline = page.screenshot(full_page=True)
            finally:
                browser.close()
    except DensityProbeError:
        raise
    except Exception as exc:
        raise DensityProbeError(f"信息密度探针运行失败：{exc}") from exc

    sections = _measure(full, baseline, geometry)
    rendered_images = [
        RenderedImage(
            int(section["index"]), str(item["kind"]), str(item["src"]),
            float(item["width"]), float(item["height"]), float(item["sectionHeight"]),
        )
        for section in geometry for item in section["images"]
    ]
    if total_height > MAX_DOCUMENT_HEIGHT:
        raise DensityProbeError(
            f"日报总高超过 {MAX_DOCUMENT_HEIGHT}px：{total_height}px"
        )
    sparse = [
        section for section in sections
        if not section.is_screenshot and section.fill_ratio < MIN_NON_SCREENSHOT_FILL
    ]
    if sparse:
        first = sparse[0]
        raise DensityProbeError(
            f"第 {first.index} 屏信息填充率不足：{first.fill_ratio:.2%} < "
            f"{MIN_NON_SCREENSHOT_FILL:.0%}"
        )
    return DensityProbeReport(total_height, sections, rendered_images)
