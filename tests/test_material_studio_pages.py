from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE_ROOT = ROOT / "pages" / "material-studio"


def test_material_studio_static_files_exist() -> None:
    assert (PAGE_ROOT / "index.html").is_file()
    assert (PAGE_ROOT / "styles.css").is_file()
    assert (PAGE_ROOT / "app.js").is_file()


def test_material_studio_states_local_only_privacy_contract() -> None:
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert "本地处理" in html
    assert "不上传" in html
    assert "下载 draft.md" in html
    assert "下载 manifest.json" in html
    assert "导出完整素材包 ZIP" in html
    assert "fetch(" not in script
    assert "XMLHttpRequest" not in script


def test_material_studio_exports_pipeline_compatible_files() -> None:
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert "function buildDraftMarkdown" in script
    assert "function buildManifest" in script
    assert "function buildZipPackage" in script
    assert "materials/original/" in script
    assert "preserve_original_bytes" in script
    assert "URL.createObjectURL" in script
    assert "strava_links" in script
    assert "materials" in script
    assert ".arrayBuffer()" in script
    assert "canvas" not in script.lower()


def test_pages_workflow_publishes_material_studio_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8"
    )

    assert "pages/material-studio" in workflow
    assert "actions/upload-pages-artifact" in workflow
    assert "actions/deploy-pages" in workflow
