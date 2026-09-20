"""Browser-level zero-occlusion probe for rendered person pixels."""

from __future__ import annotations

import contextlib
import io
import json
import threading
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageChops

from .renderer.subject_meta import SubjectMetadataError, validate_subject_metadata


class SubjectOcclusionError(RuntimeError):
    """The probe could not prove that every person pixel is unobstructed."""


@dataclass(frozen=True)
class SubjectProbeReport:
    checked_images: int
    checked_points: int
    occlusions: list[dict[str, object]]


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


_PROBE_JS = r"""
({metadata}) => {
  const findings = [];
  let checked = 0;
  const cleanColor = value => !value || value === 'transparent' || value === 'rgba(0, 0, 0, 0)';
  const pseudoDirty = (node, which) => {
    const s = getComputedStyle(node, which);
    const content = s.content;
    return s.display !== 'none' && (
      (content && content !== 'none' && content !== 'normal' && content !== '""') ||
      !cleanColor(s.backgroundColor) || s.backgroundImage !== 'none' ||
      (s.backdropFilter && s.backdropFilter !== 'none')
    );
  };
  const ownText = node => Array.from(node.childNodes).some(
    child => child.nodeType === Node.TEXT_NODE && child.textContent.trim()
  );
  const dirty = (node, image) => {
    if (!(node instanceof Element) || node === image) return false;
    if (pseudoDirty(node, '::before') || pseudoDirty(node, '::after')) return true;
    if (node.dataset && node.dataset.occlusion) return true;
    if (node.tagName === 'IMG' && node !== image) return true;
    const s = getComputedStyle(node);
    const painted = !cleanColor(s.backgroundColor) || s.backgroundImage !== 'none' ||
      (s.backdropFilter && s.backdropFilter !== 'none');
    if (ownText(node) || painted) return true;
    return false;
  };
  const transformPoint = (image, px, py) => {
    const style = getComputedStyle(image);
    const boxW = image.offsetWidth, boxH = image.offsetHeight;
    const nw = image.naturalWidth, nh = image.naturalHeight;
    if (!boxW || !boxH || !nw || !nh) throw new Error('人物图片尺寸不可用');
    let drawW = boxW, drawH = boxH, padX = 0, padY = 0;
    if (style.objectFit === 'contain') {
      const scale = Math.min(boxW / nw, boxH / nh);
      drawW = nw * scale; drawH = nh * scale;
      padX = (boxW - drawW) / 2; padY = (boxH - drawH) / 2;
    }
    const localX = padX + drawW * px / 100;
    const localY = padY + drawH * py / 100;
    const matrix = style.transform === 'none' ? new DOMMatrix() : new DOMMatrix(style.transform);
    const origin = style.transformOrigin.split(' ').map(parseFloat);
    const corners = [[0,0],[boxW,0],[0,boxH],[boxW,boxH]].map(([x,y]) => {
      const p = new DOMPoint(x-origin[0], y-origin[1]).matrixTransform(matrix);
      return [p.x+origin[0], p.y+origin[1]];
    });
    const minX = Math.min(...corners.map(p => p[0]));
    const minY = Math.min(...corners.map(p => p[1]));
    const p = new DOMPoint(localX-origin[0], localY-origin[1]).matrixTransform(matrix);
    const rect = image.getBoundingClientRect();
    return [rect.left + p.x + origin[0] - minX, rect.top + p.y + origin[1] - minY];
  };
  for (const image of document.querySelectorAll('img[data-subject="people"]')) {
    const key = image.dataset.subjectMeta;
    const item = metadata[key];
    if (!item) throw new Error(`人物图片缺少已验证 sidecar: ${image.src}`);
    for (const [px, py] of item.sample_points) {
      let [x, y] = transformPoint(image, px, py);
      if (x < 0 || x >= window.innerWidth || y < 0 || y >= window.innerHeight) {
        window.scrollTo({
          left: Math.max(0, window.scrollX + x - window.innerWidth / 2),
          top: Math.max(0, window.scrollY + y - window.innerHeight / 2),
          behavior: 'instant'
        });
        [x, y] = transformPoint(image, px, py);
      }
      checked += 1;
      const stack = document.elementsFromPoint(x, y);
      let blocked = null;
      for (const node of stack) {
        if (node === image) break;
        if (dirty(node, image)) { blocked = node; break; }
      }
      if (blocked) findings.push({
        image: image.getAttribute('src'), x: Math.round(x*100)/100, y: Math.round(y*100)/100,
        blocker: blocked.tagName.toLowerCase(),
        blocker_class: blocked.className || '',
        blocker_kind: blocked.dataset ? blocked.dataset.occlusion || '' : ''
      });
    }
  }
  window.scrollTo(0, 0);
  return {
    checked_images: document.querySelectorAll('img[data-subject="people"]').length,
    checked_points: checked, occlusions: findings,
    mapped_points: Array.from(document.querySelectorAll('img[data-subject="people"]')).flatMap(image => {
      const item = metadata[image.dataset.subjectMeta];
      return item.sample_points.map(([px,py]) => transformPoint(image, px, py));
    }),
    subjects: Array.from(document.querySelectorAll('img[data-subject="people"]')).map(image => ({
      meta: image.dataset.subjectMeta,
      corners: [transformPoint(image, 0, 0), transformPoint(image, 100, 0), transformPoint(image, 0, 100)]
    }))
  };
}
"""

_HIDE_OCCLUDERS_JS = r"""
() => {
  const people = Array.from(document.querySelectorAll('img[data-subject="people"]'));
  for (const node of document.querySelectorAll('body *')) {
    if (people.some(image => node === image || node.contains(image))) continue;
    // visibility keeps layout stable. Hide every non-ancestor instead of using
    // DOM bounds: box-shadow/filter/clip-path painting can exceed those bounds.
    node.style.visibility = 'hidden';
  }
}
"""


def _metadata_for_html(html_path: Path) -> dict[str, dict[str, object]]:
    from .ribao_gate import _read_html

    _content, facts = _read_html(html_path)
    metadata: dict[str, dict[str, object]] = {}
    for person in facts.people_images:
        src = str(person.get("src") or "")
        meta = str(person.get("meta") or "")
        zone = str(person.get("zone") or "")
        if not meta:
            raise SubjectOcclusionError(f"人物图片缺少 data-subject-meta：{src}")
        rendered = (html_path.parent / src).resolve()
        sidecar = (html_path.parent / meta).resolve()
        try:
            payload = validate_subject_metadata(rendered, sidecar, zone)
        except SubjectMetadataError as exc:
            raise SubjectOcclusionError(str(exc)) from exc
        metadata[meta] = payload
    return metadata


def _protected_raster_mask(
    html_path: Path,
    metadata: dict[str, dict[str, object]],
    subjects: list[dict[str, object]],
    size: tuple[int, int],
    device_scale_factor: int = 1,
):
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise SubjectOcclusionError("缺少 OpenCV，无法执行人物光栅差分") from exc

    protected = np.zeros((size[1], size[0]), dtype=np.uint8)
    for subject in subjects:
        meta_key = str(subject.get("meta") or "")
        payload = metadata.get(meta_key)
        if payload is None:
            raise SubjectOcclusionError(f"光栅差分缺少人物 sidecar：{meta_key}")
        sidecar_parent = (html_path.parent / meta_key).resolve().parent
        rendered = sidecar_parent / str(payload.get("rendered") or "")
        mask_path = sidecar_parent / str(payload.get("mask") or "")
        if not rendered.is_file() or not mask_path.is_file():
            raise SubjectOcclusionError("光栅差分人物图或 alpha mask 不存在")
        with Image.open(rendered) as opened:
            rendered_size = opened.size
        with Image.open(mask_path) as opened:
            alpha = opened.convert("RGBA").getchannel("A")
        canvas = Image.new("L", rendered_size, 0)
        canvas.paste(
            alpha,
            ((rendered_size[0] - alpha.width) // 2, (rendered_size[1] - alpha.height) // 2),
        )
        source_points = np.float32(
            [[0, 0], [rendered_size[0], 0], [0, rendered_size[1]]]
        )
        try:
            destination_points = np.float32(subject["corners"]) * device_scale_factor
        except (KeyError, TypeError, ValueError) as exc:
            raise SubjectOcclusionError("光栅差分人物坐标无效") from exc
        transform = cv2.getAffineTransform(source_points, destination_points)
        warped = cv2.warpAffine(
            np.asarray(canvas), transform, size, flags=cv2.INTER_NEAREST, borderValue=0
        )
        protected = np.maximum(protected, (warped >= 200).astype(np.uint8))
    return protected


def probe_subject_occlusion(
    html_path: Path, device_scale_factor: int = 1
) -> SubjectProbeReport:
    html_path = Path(html_path).resolve()
    if device_scale_factor not in {1, 2}:
        raise SubjectOcclusionError("人物像素探针倍率仅支持 1 或 2")
    metadata = _metadata_for_html(html_path)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SubjectOcclusionError("缺少 Playwright，无法执行人物像素探针") from exc
    try:
        with _serve(html_path.parent) as base_url, sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": 1080, "height": 1200},
                    device_scale_factor=device_scale_factor,
                )
                page.goto(f"{base_url}/{html_path.name}", wait_until="networkidle")
                page.add_style_tag(content=(
                    "*{pointer-events:auto !important}"
                    "[data-export-ignore]{display:none!important}"
                ))
                page.wait_for_function(
                    "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)"
                )
                document_height = int(
                    page.evaluate(
                        "Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
                    )
                )
                if document_height <= 0:
                    raise SubjectOcclusionError("人物像素探针无法取得页面高度")
                page.set_viewport_size({"width": 1080, "height": document_height})
                result = page.evaluate(_PROBE_JS, {"metadata": metadata})
                original_png = page.screenshot(full_page=True)
                page.add_style_tag(content="body *::before,body *::after{content:none!important;background:none!important;backdrop-filter:none!important;box-shadow:none!important}")
                page.evaluate(_HIDE_OCCLUDERS_JS)
                clean_png = page.screenshot(full_page=True)
            finally:
                browser.close()
    except SubjectOcclusionError:
        raise
    except Exception as exc:
        raise SubjectOcclusionError(f"人物像素探针运行失败：{exc}") from exc
    report = SubjectProbeReport(
        int(result["checked_images"]),
        int(result["checked_points"]),
        list(result["occlusions"]),
    )
    if report.checked_images and report.checked_points == 0:
        raise SubjectOcclusionError("人物像素探针没有得到有效采样点")
    if report.occlusions:
        first = report.occlusions[0]
        raise SubjectOcclusionError(
            f"人物主体被遮挡：{first['image']} @ ({first['x']}, {first['y']}) "
            f"被 {first['blocker']} 覆盖，共 {len(report.occlusions)} 个命中点"
        )
    with Image.open(io.BytesIO(original_png)).convert("RGB") as original, Image.open(
        io.BytesIO(clean_png)
    ).convert("RGB") as clean:
        difference = ImageChops.difference(original, clean)
        try:
            import numpy as np
        except ImportError as exc:
            raise SubjectOcclusionError("缺少 NumPy，无法执行人物光栅差分") from exc
        protected = _protected_raster_mask(
            html_path, metadata, list(result.get("subjects") or []), difference.size,
            device_scale_factor,
        )
        difference_pixels = np.asarray(difference)
        changed = difference_pixels.max(axis=2) > 18
        protected_count = int(protected.sum())
        raster_hits = int((changed & protected.astype(bool)).sum())
    if report.checked_images and protected_count == 0:
        raise SubjectOcclusionError("人物光栅差分没有得到有效主体像素")
    if protected_count and raster_hits / protected_count > 0.005:
        raise SubjectOcclusionError(
            f"人物主体被遮挡：光栅差分命中 {raster_hits}/{protected_count} 个主体像素"
        )
    return report
