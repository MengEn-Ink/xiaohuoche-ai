from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import _preview_html
from xzq.collector.inbox import list_inbox_images
from xzq.models import Article, Material, Screen
from xzq.ribao_gate import GateError, _validate_facts


def test_inbox_hashes_and_deduplicates_same_image_content(tmp_path: Path) -> None:
    (tmp_path / "甲.jpg").write_bytes(b"same-image")
    (tmp_path / "乙.png").write_bytes(b"same-image")

    materials = list_inbox_images(tmp_path)

    assert len(materials) == 1
    assert materials[0].meta["content_sha256"]
    assert materials[0].meta["used_fingerprint"] == materials[0].meta["content_sha256"]
    restored = Material(**json.loads(json.dumps(materials[0].__dict__)))
    assert restored.meta["used_fingerprint"] == materials[0].meta["used_fingerprint"]


def test_preview_reloads_article_json_on_each_refresh(tmp_path: Path) -> None:
    path = tmp_path / "article.json"
    article = Article("0913-ribao", "ribao", "0913", title="第一版")
    article.screens = [Screen(role="cover", text=["旧正文"])]
    article.save(path)
    first = _preview_html(path)

    article.title = "第二版"
    article.screens[0].text = ["新正文"]
    article.save(path)
    second = _preview_html(path)

    assert "第一版" in first and "旧正文" in first
    assert "第二版" in second and "新正文" in second
    assert 'http-equiv="refresh"' in second


def test_facts_gate_rejects_placeholder() -> None:
    with pytest.raises(GateError, match="未替换占位符"):
        _validate_facts("<p>{{ rider_name }}</p>", None)


def test_facts_manifest_requires_exact_source_text(tmp_path: Path) -> None:
    manifest = tmp_path / "facts.json"
    manifest.write_text(
        json.dumps({"required_text": ["全程 128.6 公里", "爬升 1800 米"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(GateError, match="爬升 1800 米"):
        _validate_facts("<p>全程 128.6 公里</p>", manifest)

    _validate_facts("<p>全程 128.6 公里，爬升 1800 米</p>", manifest)
