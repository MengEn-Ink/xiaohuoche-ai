from __future__ import annotations

import json
import re
from pathlib import Path

from xzq.writer.style import StyleAssets


ROOT = Path(__file__).resolve().parent.parent
CASE_DIR = ROOT / "docs" / "ribao-html" / "0908"
FORBIDDEN_COPY = ("第一张", "第二张", "骑就完了", "XHC")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def test_0908_benchmark_is_kept_but_not_fed_to_model() -> None:
    # 0908 是 Trae 复刻稿不是小编原声，盲判实验里它会把模型带偏；文件保留作参考，下划线开头即不入 few-shot
    assets = StyleAssets.load(ROOT / "style")
    assert not any("上周密云绕圈" in pair[0] for pair in assets.few_shots)

    fs_dir = ROOT / "style" / "few_shot"
    input_text = (fs_dir / "_20260908-ribao.input.md").read_text(encoding="utf-8")
    payload = json.loads(
        (fs_dir / "_20260908-ribao.output.json").read_text(encoding="utf-8")
    )

    assert "六石红井仅保留文字" in input_text
    assert payload["title"] == "0908，小火车日报｜不带兄弟，还爆金币"
    assert len(payload["screens"]) == 8
    assert all(screen["text"] for screen in payload["screens"])
    assert not any(
        word in json.dumps(payload, ensure_ascii=False) for word in FORBIDDEN_COPY
    )


def test_0908_html_references_each_delivery_image_once() -> None:
    html = (CASE_DIR / "index.html").read_text(encoding="utf-8")
    sources = re.findall(r'<img[^>]+src="\.\/assets\/([^"?]+)', html)
    delivery_images = {
        path.name
        for path in (CASE_DIR / "assets").iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    }

    assert len(sources) == len(set(sources)) == 5
    assert set(sources) == delivery_images
    assert html.count('data-subject="people"') == 5
    assert html.count('data-density="compact"') == 8
    assert html.count('data-layout="subject-safe"') == 5
    assert 'data-platform-safe-zone="wechat-watermark-right-bottom"' in html
    assert 'data-overlay="zones"' in html
    assert 'data-copy-flow="horizontal"' in html
    assert 'data-export-ignore="review-tools"' in html
    assert 'data-download-file="./exports/0908-xzq-ribao-full.png"' in html
    assert "reviewNotes" in html
    assert "localStorage" in html
    assert "annotationMode" in html
    assert "annotation-marker" in html
    assert "schemaVersion: 1" in html
    assert "exportReviewJson" in html
    assert "importReviewJson" in html
    assert "已重新打开" in html
    assert "成品图尚未生成" in html
    assert "route-chat" not in html
    assert not any(word in html for word in FORBIDDEN_COPY)


def test_0908_cover_has_wechat_ratio_and_style_hook() -> None:
    cover = (CASE_DIR / "cover.html").read_text(encoding="utf-8")

    assert "width: 1080px" in cover
    assert "height: 460px" in cover
    assert "0908，小火车日报｜不带兄弟，还爆金币" in cover
    assert "不带兄弟，还爆金币" in cover
    assert "./assets/group-cutout.png" in cover
    assert "./assets/meme-chat.webp" in cover
    assert "./assets/kom-coins.webp" in cover
    assert "密云收官吃上鱼" in cover
    assert "文化衫热议 · 妙峰山 · 六石红井" in cover
    assert cover.count("<img ") == 3
    assert cover.count('data-subject="people"') == 3
    assert cover.count('data-layout="subject-safe"') == 3
    assert 'data-density="compact"' in cover
    assert 'data-copy-flow="horizontal"' in cover
    assert not any(word in cover for word in FORBIDDEN_COPY)


def test_0908_benchmark_links_verified_wechat_publication() -> None:
    readme = (CASE_DIR / "README.md").read_text(encoding="utf-8")
    skill = (
        ROOT / ".trae" / "skills" / "xiaohuoche-ribao-html" / "SKILL.md"
    ).read_text(encoding="utf-8")
    publication = "https://mp.weixin.qq.com/s/njZr19jAdslBWiT7uVETZA"

    assert publication in readme
    assert publication in skill
    assert 'data-platform-safe-zone="wechat-watermark-right-bottom"' in skill
