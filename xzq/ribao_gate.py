"""小火车日报发布前门禁。

把分散的标题、图片、few-shot、封面和导出产物检查收拢成一次离线验证，
避免发布前靠人工逐项记忆。
"""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from .renderer.subject_meta import SubjectMetadataError, validate_subject_metadata
from .copy_probe import CopyOcclusionError, probe_copy_occlusion
from .image_identity import ImageIdentityError, validate_image_identities
from .density_probe import DensityProbeError, probe_content_density
from .subject_probe import SubjectOcclusionError, probe_subject_occlusion

TITLE_PATTERN = re.compile(r"^(?P<md>\d{4})，小火车日报｜(?P<hook>[^｜]{4,})$")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
FORBIDDEN_COPY = ("第一张", "第二张", "骑就完了", "XHC")


class GateError(RuntimeError):
    """发布前门禁失败。"""


class _HTMLFacts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.images: list[str] = []
        self.image_subjects: list[tuple[str, str | None]] = []
        self.density_targets: list[tuple[str, str | None]] = []
        self.safe_layouts: list[dict[str, object]] = []
        self.people_images: list[dict[str, str | None]] = []
        self.horizontal_copy_breaks: list[int] = []
        self.copy_flows: list[dict[str, object]] = []
        self.platform_safe_zones: list[str] = []
        self._in_title = False
        self._stack: list[tuple[str, int | None, int | None, int | None]] = []
        self._active_safe_layouts: list[int] = []
        self._active_horizontal_copy: list[int] = []
        self._active_copy_flows: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "img" and values.get("src"):
            source = values["src"] or ""
            self.images.append(source)
            self.image_subjects.append((source, values.get("data-subject")))

        classes = set((values.get("class") or "").split())
        if values.get("data-platform-safe-zone"):
            self.platform_safe_zones.append(values["data-platform-safe-zone"] or "")
        if tag == "section" or (tag == "main" and "cover" in classes):
            self.density_targets.append((tag, values.get("data-density")))

        safe_index: int | None = None
        if values.get("data-layout") == "subject-safe":
            safe_index = len(self.safe_layouts)
            self.safe_layouts.append(
                {
                    "overlay": values.get("data-overlay"),
                    "copy_zone": values.get("data-copy-zone"),
                    "people": [],
                }
            )
            self._active_safe_layouts.append(safe_index)

        if tag == "img" and values.get("data-subject") == "people":
            person = {
                "src": values.get("src"),
                "zone": values.get("data-subject-zone"),
                "meta": values.get("data-subject-meta"),
                "in_safe_layout": "yes" if self._active_safe_layouts else None,
            }
            self.people_images.append(person)
            if self._active_safe_layouts:
                people = self.safe_layouts[self._active_safe_layouts[-1]]["people"]
                assert isinstance(people, list)
                people.append(person)

        copy_index: int | None = None
        flow_index: int | None = None
        copy_flow = values.get("data-copy-flow")
        if copy_flow:
            flow_index = len(self.copy_flows)
            self.copy_flows.append({"flow": copy_flow, "text": ""})
            self._active_copy_flows.append(flow_index)
        if copy_flow == "horizontal":
            copy_index = len(self.horizontal_copy_breaks)
            self.horizontal_copy_breaks.append(0)
            self._active_horizontal_copy.append(copy_index)
        if tag == "br":
            for index in self._active_horizontal_copy:
                self.horizontal_copy_breaks[index] += 1

        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self._stack.append((tag, safe_index, copy_index, flow_index))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        for index in range(len(self._stack) - 1, -1, -1):
            open_tag, safe_index, copy_index, flow_index = self._stack[index]
            if open_tag != tag:
                continue
            del self._stack[index:]
            if safe_index is not None and safe_index in self._active_safe_layouts:
                self._active_safe_layouts.remove(safe_index)
            if copy_index is not None and copy_index in self._active_horizontal_copy:
                self._active_horizontal_copy.remove(copy_index)
            if flow_index is not None and flow_index in self._active_copy_flows:
                self._active_copy_flows.remove(flow_index)
            break

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        for index in self._active_copy_flows:
            current = self.copy_flows[index]
            current["text"] = str(current["text"]) + data


@dataclass
class GateReport:
    checks: list[str] = field(default_factory=list)

    def pass_check(self, message: str) -> None:
        self.checks.append(message)


def _read_html(path: Path) -> tuple[str, _HTMLFacts]:
    if not path.is_file():
        raise GateError(f"缺少文件：{path}")
    content = path.read_text(encoding="utf-8")
    facts = _HTMLFacts()
    facts.feed(content)
    return content, facts


def _validate_forbidden(content: str, label: str) -> None:
    found = [word for word in FORBIDDEN_COPY if word.lower() in content.lower()]
    if found:
        raise GateError(f"{label} 含禁用文案：{', '.join(found)}")


def _validate_facts(content: str, manifest_path: Path | None) -> None:
    """只校验显式占位符和 manifest 原文，不猜测不稳定的业务数值语义。"""
    visible_content = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", "", content, flags=re.DOTALL | re.IGNORECASE
    )
    placeholders = re.findall(
        r"\{\{[^{}]+\}\}|\$\{[^{}]+\}|\[待[^\]]*\]", visible_content
    )
    if placeholders:
        raise GateError(f"正文仍含未替换占位符：{placeholders[0]}")
    if manifest_path is None or not manifest_path.is_file():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError(f"facts manifest JSON 无法解析：{exc}") from exc
    required = payload.get("required_text")
    if not isinstance(required, list) or not required or not all(
        isinstance(item, str) and item for item in required
    ):
        raise GateError("facts manifest 的 required_text 必须是非空原文数组")
    # 去标签并压平空白，避免排版标签把必含原文拆开造成误报。
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", visible_content)).strip()
    missing = [item for item in required if item not in visible]
    if missing:
        raise GateError(f"正文遗漏 facts manifest 必含原文：{', '.join(missing)}")


def _validate_images(html_path: Path, sources: list[str], minimum: int) -> None:
    local_sources = [
        source
        for source in sources
        if urlparse(source).scheme not in {"http", "https", "data"}
    ]
    if len(local_sources) < minimum:
        raise GateError(f"{html_path.name} 至少需要 {minimum} 张本地图片")
    if len(local_sources) != len(set(local_sources)):
        raise GateError(f"{html_path.name} 存在重复图片引用")
    missing = [
        source
        for source in local_sources
        if not (html_path.parent / unquote(urlparse(source).path)).resolve().is_file()
    ]
    if missing:
        raise GateError(f"{html_path.name} 存在缺失图片：{', '.join(missing)}")


def _parse_zone(raw: object, label: str) -> tuple[float, float, float, float]:
    if not isinstance(raw, str):
        raise GateError(f"{label} 必须声明 x,y,w,h 百分比坐标")
    try:
        values = tuple(float(part.strip()) for part in raw.split(","))
    except ValueError as exc:
        raise GateError(f"{label} 坐标必须是数字：{raw}") from exc
    if len(values) != 4:
        raise GateError(f"{label} 必须声明 x,y,w,h 四个值")
    x, y, width, height = values
    if width <= 0 or height <= 0 or min(values) < 0 or x + width > 100 or y + height > 100:
        raise GateError(f"{label} 必须落在 0–100 的画面百分比内：{raw}")
    return x, y, width, height


def _zones_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _validate_visual_contract(facts: _HTMLFacts, label: str) -> None:
    if not facts.density_targets:
        raise GateError(f"{label} 缺少可检查的内容区")
    missing_density = [tag for tag, density in facts.density_targets if density != "compact"]
    if missing_density:
        raise GateError(f"{label} 内容区必须声明 data-density=compact，禁止大面积留白")

    invalid_subjects = [
        source for source, subject in facts.image_subjects if subject not in {"people", "none"}
    ]
    if invalid_subjects:
        raise GateError(
            f"{label} 图片必须声明 data-subject=people 或 none：{', '.join(invalid_subjects)}"
        )

    for person in facts.people_images:
        source = person.get("src") or "未命名图片"
        if not person.get("zone") or not person.get("in_safe_layout"):
            raise GateError(f"{label} 人物图片必须声明主体区并放入 subject-safe 容器：{source}")
        _parse_zone(person["zone"], f"{source} 人物主体区")

    for layout in facts.safe_layouts:
        people = layout["people"]
        if not isinstance(people, list) or not people:
            raise GateError(f"{label} subject-safe 容器缺少人物图片")
        overlay = layout["overlay"]
        if overlay == "forbid":
            continue
        if overlay != "zones":
            raise GateError(f"{label} subject-safe 容器必须声明 data-overlay=forbid 或 zones")
        copy_zone = _parse_zone(layout["copy_zone"], f"{label} 文字安全区")
        for person in people:
            if not isinstance(person, dict):
                continue
            subject_zone = _parse_zone(person.get("zone"), f"{label} 人物主体区")
            if _zones_overlap(copy_zone, subject_zone):
                raise GateError(f"{label} 文字安全区与人物主体区重叠")

    invalid_flows = [
        str(item["flow"])
        for item in facts.copy_flows
        if item["flow"] not in {"horizontal", "vertical"}
    ]
    if invalid_flows:
        raise GateError(f"{label} 文案排版只能声明 horizontal 或 vertical")
    long_vertical = [
        re.sub(r"\s+", "", str(item["text"]))
        for item in facts.copy_flows
        if item["flow"] == "vertical"
        and len(re.sub(r"\s+", "", str(item["text"]))) > 10
    ]
    if long_vertical:
        raise GateError(f"{label} 字多文案必须横向成行展示，禁止纵向列式排版")
    if any(breaks > 1 for breaks in facts.horizontal_copy_breaks):
        raise GateError(f"{label} 横向文案短句被强制拆行，最多允许 1 个 <br>")


def _validate_subject_sidecars(html_path: Path, facts: _HTMLFacts, label: str) -> None:
    for person in facts.people_images:
        source = str(person.get("src") or "")
        meta = str(person.get("meta") or "")
        if not meta:
            raise GateError(f"{label} 人物图片缺少 data-subject-meta：{source}")
        try:
            validate_subject_metadata(
                (html_path.parent / unquote(urlparse(source).path)).resolve(),
                (html_path.parent / unquote(urlparse(meta).path)).resolve(),
                str(person.get("zone") or ""),
            )
        except SubjectMetadataError as exc:
            raise GateError(f"{label} 人物主体 sidecar 无效：{exc}") from exc


def _validate_cover_css(content: str) -> None:
    cover_rule = re.search(r"\.cover\s*\{(?P<body>.*?)\}", content, re.DOTALL)
    if not cover_rule:
        raise GateError("cover.html 缺少 .cover 样式")
    body = cover_rule.group("body")
    width = re.search(r"\bwidth:\s*1080px\s*;", body)
    height = re.search(r"\bheight:\s*460px\s*;", body)
    if not width or not height:
        raise GateError("公众号封面画布必须为 1080×460")


def _read_title(path: Path) -> str:
    if not path.is_file():
        raise GateError(f"缺少公众号标题文件：{path}")
    title = path.read_text(encoding="utf-8").strip()
    if not title:
        raise GateError("公众号标题文件不能为空")
    return title


def _validate_few_shot(path: Path, expected_title: str) -> None:
    if not path.is_file():
        raise GateError(f"缺少 few-shot 输出：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError(f"few-shot JSON 无法解析：{exc}") from exc
    if payload.get("title") != expected_title:
        raise GateError("few-shot 标题与 HTML 标题不一致")
    screens = payload.get("screens")
    if not isinstance(screens, list) or not screens:
        raise GateError("few-shot screens 必须是非空数组")
    for index, screen in enumerate(screens, start=1):
        if not isinstance(screen, dict):
            raise GateError(f"few-shot 第 {index} 屏必须是对象")
        if not screen.get("role") or not screen.get("text") or not screen.get("visual"):
            raise GateError(f"few-shot 第 {index} 屏缺少 role/text/visual")
    _validate_forbidden(json.dumps(payload, ensure_ascii=False), "few-shot")


def _validate_png(path: Path, expected_size: tuple[int, int] | None = None) -> None:
    if not path.is_file():
        raise GateError(f"缺少导出图片：{path}")
    with Image.open(path) as image:
        if expected_size and image.size != expected_size:
            raise GateError(
                f"{path.name} 尺寸错误：期望 {expected_size[0]}×{expected_size[1]}，"
                f"实际 {image.width}×{image.height}"
            )
        if not expected_size and image.width != 1080:
            raise GateError(f"{path.name} 宽度必须为 1080，实际 {image.width}")


def _validate_delivery_slices(target: Path) -> int:
    manifest_path = target / "result-manifest.json"
    if not manifest_path.is_file():
        raise GateError(f"缺少双分屏交付清单：{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateError(f"result-manifest.json 无法解析：{exc}") from exc
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        raise GateError("result-manifest.json 缺少 outputs")
    normal = outputs.get("slices")
    retina = outputs.get("retinaSlices")
    if not isinstance(normal, list) or not normal:
        raise GateError("缺少 1080 分屏清单")
    if not isinstance(retina, list) or not retina:
        raise GateError("缺少 Retina 分屏清单")
    if len(normal) != len(retina):
        raise GateError("1080 与 Retina 分屏数量不一致")

    for label, entries, expected_size in (
        ("1080", normal, (1080, 1000)),
        ("Retina", retina, (2160, 2000)),
    ):
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise GateError(f"{label} 第 {index} 屏清单格式错误")
            relative = entry.get("path")
            expected_sha = entry.get("sha256")
            expected_prefix = "slices/1080/" if label == "1080" else "slices/2160/"
            if not isinstance(relative, str) or not relative.startswith(expected_prefix):
                raise GateError(f"{label} 第 {index} 屏路径必须位于 {expected_prefix}")
            path = (target / relative).resolve()
            if target.resolve() not in path.parents:
                raise GateError(f"{label} 第 {index} 屏路径越界")
            if not path.is_file():
                raise GateError(f"缺少 {label} 分屏：{path}")
            _validate_png(path, expected_size)
            if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                raise GateError(f"{label} 第 {index} 屏缺少有效 SHA256")
            actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_sha != expected_sha:
                raise GateError(f"{label} 第 {index} 屏 SHA256 不一致")
    return len(normal)


def validate_rendered_ribao(html_path: Path, assets_dir: Path) -> GateReport:
    """Run the fail-closed visual gates against one rendered build artifact."""
    html = Path(html_path).resolve()
    output = Path(assets_dir).resolve()
    stem = html.stem
    if html.parent != output:
        raise GateError("--html 必须位于 --assets-dir 内，避免审错产物")
    if not html.is_file():
        raise GateError(f"缺少待检查 HTML：{html}")
    _validate_png(output / f"{stem}.png")
    _validate_png(output / f"{stem}-cover.jpg", (1080, 460))
    try:
        validate_image_identities([html])
        probe_subject_occlusion(html, device_scale_factor=1)
        probe_subject_occlusion(html, device_scale_factor=2)
        probe_copy_occlusion(html, device_scale_factor=1)
        probe_copy_occlusion(html, device_scale_factor=2)
        probe_content_density(html)
    except (ImageIdentityError, SubjectOcclusionError, CopyOcclusionError, DensityProbeError) as exc:
        raise GateError(str(exc)) from exc
    report = GateReport()
    report.pass_check("build 长图与封面尺寸有效")
    report.pass_check("build 产物五门禁全部通过")
    return report


def validate_ribao_case(root: Path, date: str, case_dir: Path | None = None) -> GateReport:
    """验证一期日报的输入、HTML、图片和导出产物。date 使用 YYYYMMDD。"""
    if not re.fullmatch(r"\d{8}", date):
        raise GateError("date 必须为 YYYYMMDD，例如 20260908")
    md = date[4:]
    target = (case_dir or root / "docs" / "ribao-html" / md).resolve()
    report = GateReport()
    archived = (target / ".ribao-archive").is_file()

    index_content, index_facts = _read_html(target / "index.html")
    cover_content, cover_facts = _read_html(target / "cover.html")
    report.pass_check("正文与封面 HTML 存在")

    title_path = target / "title.txt"
    expected_title = (
        index_facts.title.strip()
        if archived and not title_path.is_file()
        else _read_title(title_path)
    )
    title_match = TITLE_PATTERN.fullmatch(expected_title)
    if not title_match or title_match.group("md") != md:
        raise GateError("公众号标题必须符合 MMDD，小火车日报｜当期主梗")
    if index_facts.title.strip() != expected_title:
        raise GateError("公众号标题文件与 HTML 标题不一致")
    if cover_facts.title.strip() != index_facts.title.strip():
        raise GateError("封面标题与正文标题不一致")
    report.pass_check(f"公众号标题文件与 HTML 标题一致：{expected_title}")

    _validate_forbidden(index_content, "正文")
    _validate_forbidden(cover_content, "封面")
    _validate_facts(index_content, target / "facts.json")
    _validate_facts(cover_content, None)
    _validate_images(target / "index.html", index_facts.images, minimum=1)
    _validate_images(target / "cover.html", cover_facts.images, minimum=3)
    _validate_cover_css(cover_content)
    report.pass_check("正文与封面图片引用存在、页内不重复，封面画布为 1080×460")
    if archived:
        report.pass_check("归档期豁免跨正文与封面图片源身份门禁")
    else:
        try:
            identity_report = validate_image_identities(
                [target / "index.html", target / "cover.html"]
            )
        except ImageIdentityError as exc:
            raise GateError(str(exc)) from exc
        report.pass_check(
            f"正文与封面全篇图片源身份唯一：{identity_report.identity_count} 张"
        )

    _validate_visual_contract(index_facts, "正文")
    _validate_visual_contract(cover_facts, "封面")
    if archived:
        report.pass_check("归档期豁免人物 sidecar 与像素探针门禁")
    else:
        _validate_subject_sidecars(target / "index.html", index_facts, "正文")
        _validate_subject_sidecars(target / "cover.html", cover_facts, "封面")
        try:
            for page in (target / "index.html", target / "cover.html"):
                probe_subject_occlusion(page, device_scale_factor=1)
                probe_subject_occlusion(page, device_scale_factor=2)
                probe_copy_occlusion(page, device_scale_factor=1)
                probe_copy_occlusion(page, device_scale_factor=2)
            probe_content_density(target / "index.html")
        except (SubjectOcclusionError, CopyOcclusionError, DensityProbeError) as exc:
            raise GateError(str(exc)) from exc
    if "wechat-watermark-right-bottom" not in index_facts.platform_safe_zones:
        raise GateError("正文必须为公众号右下角自动水印预留非关键内容安全区")
    report.pass_check("人物主体安全区、图文叠层约定、紧凑密度与公众号水印安全区有效")

    few_shot_input = root / "style" / "few_shot" / f"{date}-ribao.input.md"
    few_shot_output = root / "style" / "few_shot" / f"{date}-ribao.output.json"
    if archived and not few_shot_input.is_file():
        few_shot_input = few_shot_input.with_name("_" + few_shot_input.name)
    if archived and not few_shot_output.is_file():
        few_shot_output = few_shot_output.with_name("_" + few_shot_output.name)
    if not few_shot_input.is_file():
        raise GateError(f"缺少 few-shot 输入：{few_shot_input}")
    _validate_few_shot(
        few_shot_output,
        index_facts.title.strip(),
    )
    report.pass_check("few-shot 输入输出配对且结构有效")

    exports = target / "exports"
    _validate_png(exports / f"{md}-xzq-cover-full.png", (1080, 460))
    _validate_png(exports / f"{md}-xzq-ribao-full.png")
    slices = sorted(exports.glob(f"{md}-xzq-ribao-[0-9][0-9].png"))
    if not slices:
        raise GateError("缺少公众号正文分屏 PNG")
    for path in slices:
        _validate_png(path)
    report.pass_check(f"封面、整页和 {len(slices)} 张正文分屏 PNG 尺寸有效")
    if not archived and (
        date >= "20260912"
        or ((target / "result-manifest.json").is_file() and (target / "slices").is_dir())
    ):
        _validate_delivery_slices(target)
        report.pass_check("1080/2160 双分屏 PNG、逐张 SHA 与数量一致")
    return report
