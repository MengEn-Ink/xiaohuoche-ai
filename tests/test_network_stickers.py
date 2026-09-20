import json
import sys
from pathlib import Path

import pytest

import pipeline
from xzq.renderer.network_stickers import (
    NetworkStickerCatalog,
    StickerCandidate,
)
from xzq.renderer.stickers import StickerLib


class FakeResponse:
    def __init__(self, *, data=None, content=b"", content_type="application/json"):
        self._data = data
        self.content = content
        self.headers = {"content-type": content_type}

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


def _style_dir(tmp_path: Path) -> Path:
    sticker_dir = tmp_path / "style" / "stickers"
    sticker_dir.mkdir(parents=True)
    (sticker_dir / "stickers.json").write_text(
        json.dumps({"_doc": "测试贴纸库", "stickers": []}), encoding="utf-8"
    )
    return tmp_path / "style"


def test_search_openmoji_returns_licensed_candidates(tmp_path: Path):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(
            data={
                "icons": [
                    "openmoji:astonished-face",
                    "openmoji:face-with-open-mouth",
                ]
            }
        )

    catalog = NetworkStickerCatalog(_style_dir(tmp_path), http_get=get)
    candidates = catalog.search_openmoji("shock face", limit=2)

    assert [item.identifier for item in candidates] == [
        "openmoji:astonished-face",
        "openmoji:face-with-open-mouth",
    ]
    assert all(item.license == "CC BY-SA 4.0" for item in candidates)
    assert calls == [
        (
            "https://api.iconify.design/search",
            {
                "params": {
                    "query": "shock face",
                    "prefix": "openmoji",
                    "limit": 2,
                },
                "timeout": 12,
            },
        )
    ]


def test_twemoji_candidate_uses_pinned_version_and_codepoints(tmp_path: Path):
    catalog = NetworkStickerCatalog(_style_dir(tmp_path))

    candidate = catalog.twemoji("🏳️")

    assert candidate.identifier == "twemoji:1f3f3"
    assert candidate.download_url.endswith(
        "/jdecked/twemoji@v17.0.3/assets/svg/1f3f3.svg"
    )
    assert candidate.license == "CC BY 4.0"


def test_import_candidate_localizes_asset_and_registers_source(tmp_path: Path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'

    def get(url, **kwargs):
        return FakeResponse(content=svg, content_type="image/svg+xml")

    style_dir = _style_dir(tmp_path)
    catalog = NetworkStickerCatalog(style_dir, http_get=get)
    candidate = StickerCandidate(
        provider="openmoji",
        identifier="openmoji:astonished-face",
        title="astonished-face",
        download_url="https://api.iconify.design/openmoji:astonished-face.svg",
        source_url=(
            "https://icon-sets.iconify.design/openmoji/astonished-face/"
        ),
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="OpenMoji contributors",
    )

    first = catalog.import_candidate(
        candidate, name="震惊脸", use="震惊/赛前科技求助"
    )
    second = catalog.import_candidate(
        candidate, name="重复名字不会覆盖", use="重复用途"
    )

    assert first == second
    assert first.read_bytes() == svg
    manifest = json.loads(
        (style_dir / "stickers" / "stickers.json").read_text(encoding="utf-8")
    )
    assert len(manifest["stickers"]) == 1
    item = manifest["stickers"][0]
    assert item["name"] == "震惊脸"
    assert item["file"] == "external/openmoji-astonished-face.svg"
    assert item["provider"] == "openmoji"
    assert item["source_url"] == candidate.source_url
    assert item["license"] == "CC BY-SA 4.0"
    assert len(item["sha256"]) == 64
    assert StickerLib(style_dir).find("震惊脸") == first


def test_import_rejects_active_svg_content(tmp_path: Path):
    def get(url, **kwargs):
        return FakeResponse(
            content=b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            content_type="image/svg+xml",
        )

    catalog = NetworkStickerCatalog(_style_dir(tmp_path), http_get=get)
    candidate = catalog.twemoji("😲")

    with pytest.raises(ValueError, match="不安全"):
        catalog.import_candidate(candidate, name="震惊", use="震惊")


def test_sticker_search_cli_lists_candidates(monkeypatch, capsys, tmp_path: Path):
    candidate = StickerCandidate(
        provider="openmoji",
        identifier="openmoji:astonished-face",
        title="astonished-face",
        download_url="https://api.iconify.design/openmoji:astonished-face.svg",
        source_url=(
            "https://icon-sets.iconify.design/openmoji/astonished-face/"
        ),
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="OpenMoji contributors",
    )

    class FakeCatalog:
        def __init__(self, style_dir):
            assert style_dir == str(tmp_path / "style")

        def search_openmoji(self, query, *, limit):
            assert (query, limit) == ("astonished", 3)
            return [candidate]

    monkeypatch.setattr(pipeline, "NetworkStickerCatalog", FakeCatalog, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline.py",
            "sticker-search",
            "astonished",
            "--limit",
            "3",
            "--style-dir",
            str(tmp_path / "style"),
        ],
    )

    assert pipeline.main() == 0
    assert (
        "1. openmoji:astonished-face | astonished-face | CC BY-SA 4.0"
        in capsys.readouterr().out
    )


def test_sticker_import_cli_registers_selected_asset(
    monkeypatch, capsys, tmp_path: Path
):
    output = tmp_path / "style" / "stickers" / "external" / "face.svg"
    candidate = StickerCandidate(
        provider="openmoji",
        identifier="openmoji:astonished-face",
        title="astonished-face",
        download_url="https://api.iconify.design/openmoji:astonished-face.svg",
        source_url=(
            "https://icon-sets.iconify.design/openmoji/astonished-face/"
        ),
        license="CC BY-SA 4.0",
        license_url="https://creativecommons.org/licenses/by-sa/4.0/",
        attribution="OpenMoji contributors",
    )

    class FakeCatalog:
        def __init__(self, style_dir):
            assert style_dir == str(tmp_path / "style")

        def openmoji(self, identifier):
            assert identifier == "openmoji:astonished-face"
            return candidate

        def import_candidate(self, selected, *, name, use):
            assert (selected, name, use) == (
                candidate,
                "震惊脸",
                "震惊/看不懂",
            )
            return output

    monkeypatch.setattr(pipeline, "NetworkStickerCatalog", FakeCatalog, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline.py",
            "sticker-import",
            "openmoji:astonished-face",
            "--provider",
            "openmoji",
            "--name",
            "震惊脸",
            "--use",
            "震惊/看不懂",
            "--style-dir",
            str(tmp_path / "style"),
        ],
    )

    assert pipeline.main() == 0
    assert f"[ok] 网络贴图已入库：{output}" in capsys.readouterr().out
