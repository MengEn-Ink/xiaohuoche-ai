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
    assert "文案工作台" in html
    assert "成稿标题" in html
    assert "导语" in html
    assert "正文要点" in html
    assert "结尾文案" in html
    assert "全局 AI 要求" in html
    assert "实现预览" in html
    assert "确认预览后导出" in html
    assert "素材回收区" in html
    assert "分页工作台" in html
    assert "stage-nav" in html
    assert "工作台摘要" in html
    assert "上一步" in html
    assert "下一步" in html
    assert "我确认后续实现必须以当前最终预览为准" in html
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


def test_material_studio_exports_preview_layout_and_instructions_contract() -> None:
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert "function buildLayoutJson" in script
    assert "function buildInstructionsMarkdown" in script
    assert "function buildPreviewHtml" in script
    assert "instructions.md" in script
    assert "layout.json" in script
    assert "preview/preview.html" in script
    assert "implementation_contract" in script
    assert "source_of_truth" in script
    assert "layout_role" in script
    assert "caption" in script
    assert "ai_instruction" in script


def test_material_studio_supports_ordering_delete_undo_and_confirmed_export() -> None:
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'data-file-action="move-up"' in script
    assert 'data-file-action="move-down"' in script
    assert 'data-file-action="delete"' in script
    assert 'id="undo-delete"' in html
    assert "function moveFile" in script
    assert "function deleteFile" in script
    assert "function undoDelete" in script
    assert "function updateExportState" in script
    assert "confirm-export" in html
    assert "zipButton.disabled = !state.confirmed" in script


def test_material_studio_uses_paged_workbench_flow() -> None:
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'data-stage="activity"' in html
    assert 'data-stage="materials"' in html
    assert 'data-stage="layout"' in html
    assert 'data-stage="copy"' in html
    assert 'data-stage="preview"' in html
    assert 'data-stage-panel="activity"' in html
    assert 'data-stage-panel="materials"' in html
    assert 'data-stage-panel="layout"' in html
    assert 'data-stage-panel="copy"' in html
    assert 'data-stage-panel="preview"' in html
    assert "活动信息" in html
    assert "素材池" in html
    assert "排版台" in html
    assert "文案批注" in html
    assert "最终预览" in html
    assert "currentStage" in script
    assert "function setStage" in script
    assert "function computeStageStatus" in script
    assert "function renderStageNav" in script
    assert "function renderWorkspaceSummary" in script
    assert "data-stage-panel" in script
    assert "data-stage-status" in script
    assert "state.currentStage === \"preview\"" in script


def test_material_studio_file_field_inputs_do_not_double_render() -> None:
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'event.target.hasAttribute("data-file-field")' in script
    assert "return;" in script


def test_material_studio_preview_stage_does_not_overlay_summary() -> None:
    style = (PAGE_ROOT / "styles.css").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert 'form.classList.toggle("preview-mode", stageId === "preview")' in script
    assert 'document.body.classList.toggle("preview-mode", stageId === "preview")' in script
    assert ".workspace.preview-mode .workspace-summary" in style
    assert ".workspace.preview-mode .preview-stage" in style
    assert "body.preview-mode .stage-nav" in style


def test_material_studio_supports_storyboard_preview_contract() -> None:
    html = (PAGE_ROOT / "index.html").read_text(encoding="utf-8")
    style = (PAGE_ROOT / "styles.css").read_text(encoding="utf-8")
    script = (PAGE_ROOT / "app.js").read_text(encoding="utf-8")

    assert "段落标题" in html
    assert "副标题 / 短句" in html
    assert 'data-file-field="segment_title"' in script
    assert 'data-file-field="segment_subtitle"' in script
    assert "storyboard-preview" in script
    assert "storyboard-card" in script
    assert "function renderStoryboardPreview" in script
    assert "function materialStoryTitle" in script
    assert "function materialStorySubtitle" in script
    assert "segment_title" in script
    assert "segment_subtitle" in script
    assert "段落标题" in script
    assert "副标题" in script
    assert "日报故事板" in script
    assert ".storyboard-preview" in style
    assert ".storyboard-card" in style
    assert ".story-label" in style
