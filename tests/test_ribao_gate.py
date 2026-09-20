from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from xzq.ribao_gate import GateError, validate_rendered_ribao, validate_ribao_case
from xzq.renderer.subject_meta import write_subject_metadata
from xzq.subject_probe import SubjectOcclusionError


DATE = "20260908"
TITLE = "0908，小火车日报｜密云收官爆金币"


@pytest.fixture(autouse=True)
def _stub_browser_probe(monkeypatch):
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_subject_occlusion",
        lambda path, device_scale_factor=1: None,
    )
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_copy_occlusion",
        lambda path, device_scale_factor=1: None,
    )
    monkeypatch.setattr("xzq.ribao_gate.probe_content_density", lambda path: None, raising=False)


def _write_png(path: Path, size: tuple[int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "#00a8ff").save(path)


def _write_delivery_manifest(case: Path, count: int = 1) -> None:
    groups = {}
    for label, size in (("1080", (1080, 1000)), ("2160", (2160, 2000))):
        entries = []
        for index in range(1, count + 1):
            path = case / "slices" / label / f"{index:02d}.png"
            _write_png(path, size)
            entries.append({
                "label": f"第 {index} 屏",
                "path": f"slices/{label}/{index:02d}.png",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        groups[label] = entries
    (case / "result-manifest.json").write_text(
        json.dumps({
            "outputs": {
                "slices": groups["1080"],
                "retinaSlices": groups["2160"],
            }
        }),
        encoding="utf-8",
    )


def _build_case(root: Path) -> Path:
    case = root / "docs" / "ribao-html" / "0908"
    assets = case / "assets"
    assets.mkdir(parents=True)
    for name, color in zip(
        ("body.png", "group.png", "ride.png", "kom.png"),
        ("#00a8ff", "#ff2b00", "#fff200", "#39ff14"),
    ):
        path = assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), color).save(path)
        write_subject_metadata(
            path, path, mask_path=path,
            mode="full-image-fallback", source_face_count=0, rendered_face_count=0,
            fallback_reason="fixture",
        )

    (case / "index.html").write_text(
        f'<html><head><title>{TITLE}</title></head><body><main class="page">'
        '<section data-density="compact"><h1>小火车日报</h1>'
        '<figure data-layout="subject-safe" data-overlay="forbid">'
        '<img src="./assets/body.png" data-subject="people" '
        'data-subject-zone="0,0,100,100" data-subject-meta="./assets/body.png.subject.json" /></figure>'
        '<p class="copy" data-copy-flow="horizontal">密云收官，大家玩得开心。</p>'
        '</section><footer data-platform-safe-zone="wechat-watermark-right-bottom">'
        'XZQ · 辛庄桥小火车</footer></main></body></html>',
        encoding="utf-8",
    )
    (case / "cover.html").write_text(
        f'<html><head><title>{TITLE}</title><style>'
        '.cover { width: 1080px; height: 460px; }</style></head><body>'
        '<main class="cover" data-density="compact">'
        '<div data-layout="subject-safe" data-overlay="forbid">'
        '<img src="./assets/group.png" data-subject="people" '
        'data-subject-zone="0,0,100,100" data-subject-meta="./assets/group.png.subject.json" /></div>'
        '<div data-layout="subject-safe" data-overlay="forbid">'
        '<img src="./assets/ride.png" data-subject="people" '
        'data-subject-zone="0,0,100,100" data-subject-meta="./assets/ride.png.subject.json" /></div>'
        '<div data-layout="subject-safe" data-overlay="forbid">'
        '<img src="./assets/kom.png" data-subject="people" '
        'data-subject-zone="0,0,100,100" data-subject-meta="./assets/kom.png.subject.json" /></div>'
        '<p class="digest" data-copy-flow="horizontal">密云收官爆金币</p>'
        '</main></body></html>',
        encoding="utf-8",
    )
    (case / "title.txt").write_text(TITLE + "\n", encoding="utf-8")

    few_shot = root / "style" / "few_shot"
    few_shot.mkdir(parents=True)
    (few_shot / f"{DATE}-ribao.input.md").write_text("密云素材", encoding="utf-8")
    (few_shot / f"{DATE}-ribao.output.json").write_text(
        json.dumps(
            {
                "title": TITLE,
                "screens": [
                    {"role": "cover", "text": ["密云收官"], "visual": "群像"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exports = case / "exports"
    _write_png(exports / "0908-xzq-cover-full.png", (1080, 460))
    _write_png(exports / "0908-xzq-ribao-full.png", (1080, 1800))
    _write_png(exports / "0908-xzq-ribao-01.png", (1080, 900))
    return case


def test_validate_ribao_case_passes_complete_artifacts(tmp_path: Path) -> None:
    _build_case(tmp_path)

    report = validate_ribao_case(tmp_path, DATE)

    assert report.checks == [
        "正文与封面 HTML 存在",
        f"公众号标题文件与 HTML 标题一致：{TITLE}",
        "正文与封面图片引用存在、页内不重复，封面画布为 1080×460",
        "正文与封面全篇图片源身份唯一：4 张",
        "人物主体安全区、图文叠层约定、紧凑密度与公众号水印安全区有效",
        "few-shot 输入输出配对且结构有效",
        "封面、整页和 1 张正文分屏 PNG 尺寸有效",
    ]


def test_validate_ribao_case_accepts_dual_png_slice_delivery(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    _write_delivery_manifest(case)

    report = validate_ribao_case(tmp_path, DATE)

    assert "1080/2160 双分屏 PNG、逐张 SHA 与数量一致" in report.checks


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-retina", "缺少 Retina 分屏"),
        ("wrong-size", "尺寸错误"),
        ("wrong-sha", "SHA256 不一致"),
    ],
)
def test_validate_ribao_case_rejects_invalid_dual_slice_delivery(
    tmp_path: Path, mutation: str, message: str
) -> None:
    case = _build_case(tmp_path)
    _write_delivery_manifest(case)
    manifest_path = case / "result-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retina = case / "slices" / "2160" / "01.png"
    if mutation == "missing-retina":
        retina.unlink()
    elif mutation == "wrong-size":
        _write_png(retina, (1080, 1000))
    else:
        manifest["outputs"]["retinaSlices"][0]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GateError, match=message):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_title_without_hook(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8").replace(TITLE, "0908，小火车日报。"),
        encoding="utf-8",
    )
    (case / "title.txt").write_text("0908，小火车日报。\n", encoding="utf-8")

    with pytest.raises(GateError, match="标题必须符合"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_missing_title_file(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    (case / "title.txt").unlink()

    with pytest.raises(GateError, match="缺少公众号标题文件"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_mismatched_title_file(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    (case / "title.txt").write_text("0908，小火车日报｜别的标题\n", encoding="utf-8")

    with pytest.raises(GateError, match="公众号标题文件与 HTML 标题不一致"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_incomplete_cover(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    cover = case / "cover.html"
    content = cover.read_text(encoding="utf-8").replace(
        '<img src="./assets/kom.png" data-subject="people" '
        'data-subject-zone="0,0,100,100" data-subject-meta="./assets/kom.png.subject.json" />',
        "",
    )
    cover.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="至少需要 3 张"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_renamed_duplicate_across_body_and_cover(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)
    duplicate = case / "assets" / "body-copy.png"
    duplicate.write_bytes((case / "assets" / "body.png").read_bytes())
    cover = case / "cover.html"
    content = cover.read_text(encoding="utf-8")
    content = content.replace(
        '<img src="./assets/group.png" data-subject="people" '
        'data-subject-zone="0,0,100,100" data-subject-meta="./assets/group.png.subject.json" />',
        '<img src="./assets/body-copy.png" data-subject="none" '
        'data-subject-zone="0,0,100,100" />',
    )
    cover.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="同源图片重复"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_invalid_few_shot(tmp_path: Path) -> None:
    _build_case(tmp_path)
    output = tmp_path / "style" / "few_shot" / f"{DATE}-ribao.output.json"
    output.write_text("{invalid", encoding="utf-8")

    with pytest.raises(GateError, match="JSON 无法解析"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_ignores_script_template_placeholders(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        "</body>", "<script>const value = `${e}`;</script></body>"
    )
    index.write_text(content, encoding="utf-8")

    validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_wrong_cover_size(tmp_path: Path) -> None:
    _build_case(tmp_path)
    cover = (
        tmp_path
        / "docs"
        / "ribao-html"
        / "0908"
        / "exports"
        / "0908-xzq-cover-full.png"
    )
    _write_png(cover, (1080, 500))

    with pytest.raises(GateError, match="尺寸错误"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_people_image_without_safe_contract(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        ' data-subject-zone="0,0,100,100"', "", 1
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="人物图片必须声明"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_people_image_without_sidecar(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        ' data-subject-meta="./assets/body.png.subject.json"', "", 1
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="缺少 data-subject-meta"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_explicit_archive_skips_only_subject_gate(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    (case / ".ribao-archive").write_text(
        "Legacy artifact frozen before subject-sidecar enforcement.\n",
        encoding="utf-8",
    )
    for name in ("index.html", "cover.html"):
        page = case / name
        content = page.read_text(encoding="utf-8")
        content = __import__("re").sub(r' data-subject-meta="[^"]+"', "", content)
        page.write_text(content, encoding="utf-8")
    (case / "title.txt").unlink()
    few_shot = tmp_path / "style" / "few_shot"
    for suffix in ("input.md", "output.json"):
        (few_shot / f"{DATE}-ribao.{suffix}").rename(
            few_shot / f"_{DATE}-ribao.{suffix}"
        )

    report = validate_ribao_case(tmp_path, DATE)

    assert "归档期豁免人物 sidecar 与像素探针门禁" in report.checks


def test_validate_ribao_case_runs_browser_probe_for_body_and_cover(tmp_path, monkeypatch):
    case = _build_case(tmp_path)
    called = []
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_subject_occlusion",
        lambda path, device_scale_factor=1: called.append(
            (Path(path).name, device_scale_factor)
        ),
    )

    validate_ribao_case(tmp_path, DATE)

    assert called == [
        ("index.html", 1), ("index.html", 2),
        ("cover.html", 1), ("cover.html", 2),
    ]


def test_validate_ribao_case_runs_copy_probe_for_body_and_cover(tmp_path, monkeypatch):
    case = _build_case(tmp_path)
    called = []
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_subject_occlusion",
        lambda path, device_scale_factor=1: None,
    )
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_copy_occlusion",
        lambda path, device_scale_factor=1: called.append(
            (Path(path).name, device_scale_factor)
        ),
        raising=False,
    )

    validate_ribao_case(tmp_path, DATE)

    assert called == [
        ("index.html", 1), ("index.html", 2),
        ("cover.html", 1), ("cover.html", 2),
    ]


def test_validate_ribao_case_runs_density_probe_for_body(tmp_path, monkeypatch):
    _build_case(tmp_path)
    called = []
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_content_density",
        lambda path: called.append(Path(path).name),
        raising=False,
    )

    validate_ribao_case(tmp_path, DATE)

    assert called == ["index.html"]


def test_validate_ribao_case_rejects_overlapping_subject_and_copy_zones(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        'data-overlay="forbid"',
        'data-overlay="zones" data-copy-zone="20,20,50,50"',
        1,
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="文字安全区与人物主体区重叠"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_story_without_compact_density(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        ' data-density="compact"', "", 1
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="内容区必须声明 data-density=compact"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_fragmented_copy(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        "密云收官，大家玩得开心。", "密云<br />收官<br />大家<br />开心"
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="短句被强制拆行"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_long_vertical_copy(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        'data-copy-flow="horizontal">密云收官，大家玩得开心。',
        'data-copy-flow="vertical">密云收官，大家玩得开心。',
        1,
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="字多文案必须横向成行展示"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_accepts_short_vertical_copy(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        'data-copy-flow="horizontal">密云收官，大家玩得开心。',
        'data-copy-flow="vertical">爆金币',
        1,
    )
    index.write_text(content, encoding="utf-8")

    validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_image_without_subject_classification(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    cover = case / "cover.html"
    content = cover.read_text(encoding="utf-8").replace(
        ' data-subject="people"', "", 1
    )
    cover.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="图片必须声明 data-subject"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_rendered_ribao_runs_all_build_probes(tmp_path: Path, monkeypatch) -> None:
    html = tmp_path / "0912-ribao.html"
    html.write_text("<html></html>", encoding="utf-8")
    _write_png(tmp_path / "0912-ribao.png", (1080, 1200))
    _write_png(tmp_path / "0912-ribao-cover.jpg", (1080, 460))
    called = []
    monkeypatch.setattr(
        "xzq.ribao_gate.validate_image_identities",
        lambda pages: called.append(("identity", [Path(p).name for p in pages])),
    )
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_subject_occlusion",
        lambda path, device_scale_factor=1: called.append(
            ("subject", Path(path).name, device_scale_factor)
        ),
    )
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_copy_occlusion",
        lambda path, device_scale_factor=1: called.append(
            ("copy", Path(path).name, device_scale_factor)
        ),
    )
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_content_density",
        lambda path: called.append(("density", Path(path).name)),
    )

    report = validate_rendered_ribao(html, tmp_path)

    assert called == [
        ("identity", ["0912-ribao.html"]),
        ("subject", "0912-ribao.html", 1),
        ("subject", "0912-ribao.html", 2),
        ("copy", "0912-ribao.html", 1),
        ("copy", "0912-ribao.html", 2),
        ("density", "0912-ribao.html"),
    ]
    assert report.checks[-1] == "build 产物五门禁全部通过"


def test_validate_rendered_ribao_fails_when_probe_fails(tmp_path: Path, monkeypatch) -> None:
    html = tmp_path / "0912-ribao.html"
    html.write_text("<html></html>", encoding="utf-8")
    _write_png(tmp_path / "0912-ribao.png", (1080, 1200))
    _write_png(tmp_path / "0912-ribao-cover.jpg", (1080, 460))
    monkeypatch.setattr(
        "xzq.ribao_gate.validate_image_identities", lambda pages: None
    )
    monkeypatch.setattr(
        "xzq.ribao_gate.probe_subject_occlusion",
        lambda path, device_scale_factor=1: (_ for _ in ()).throw(
            SubjectOcclusionError("反例遮挡")
        ),
    )

    with pytest.raises(GateError, match="反例遮挡"):
        validate_rendered_ribao(html, tmp_path)


def test_validate_ribao_case_accepts_non_overlapping_copy_zone(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    group = case / "assets" / "body.png"
    person = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
    person.paste(Image.new("RGBA", (320, 160), (0, 168, 255, 255)), (0, 0))
    person.save(group)
    write_subject_metadata(
        group, group, mask_path=group, mode="cutout",
        source_face_count=0, rendered_face_count=0,
    )
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        'data-subject-zone="0,0,100,100"',
        'data-subject-zone="0,0,100,88.8889"',
        1,
    ).replace(
        'data-overlay="forbid"',
        'data-overlay="zones" data-copy-zone="0,92,100,8"',
        1,
    )
    index.write_text(content, encoding="utf-8")
    cover = case / "cover.html"
    cover.write_text(
        cover.read_text(encoding="utf-8").replace(
            'src="./assets/group.png" data-subject="people" data-subject-zone="0,0,100,100"',
            'src="./assets/group.png" data-subject="people" data-subject-zone="0,0,100,100"',
        ),
        encoding="utf-8",
    )

    validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_people_image_outside_safe_layout(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        '<figure data-layout="subject-safe" data-overlay="forbid">', "<figure>", 1
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="人物图片必须声明主体区并放入"):
        validate_ribao_case(tmp_path, DATE)


@pytest.mark.parametrize(
    ("zone", "message"),
    [
        ("a,8,84,84", "坐标必须是数字"),
        ("8,8,84", "四个值"),
        ("20,20,90,90", "0–100"),
    ],
)
def test_validate_ribao_case_rejects_invalid_subject_zone(
    tmp_path: Path, zone: str, message: str
) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace("0,0,100,100", zone, 1)
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match=message):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_unknown_overlay_policy(tmp_path: Path) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        'data-overlay="forbid"', 'data-overlay="auto"', 1
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="必须声明 data-overlay=forbid 或 zones"):
        validate_ribao_case(tmp_path, DATE)


def test_validate_ribao_case_rejects_missing_wechat_watermark_safe_zone(
    tmp_path: Path,
) -> None:
    case = _build_case(tmp_path)
    index = case / "index.html"
    content = index.read_text(encoding="utf-8").replace(
        ' data-platform-safe-zone="wechat-watermark-right-bottom"', ""
    )
    index.write_text(content, encoding="utf-8")

    with pytest.raises(GateError, match="公众号右下角自动水印"):
        validate_ribao_case(tmp_path, DATE)
