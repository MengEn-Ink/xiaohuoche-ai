from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from xzq.copy_probe import CopyProbeReport
from xzq.density_probe import DensityProbeReport, RenderedImage, SectionDensity
from xzq.image_identity import ImageIdentityReport
from xzq.ribao_audit import AuditError, audit_ribao
from xzq.subject_probe import SubjectProbeReport


def _artifacts(tmp_path: Path) -> tuple[Path, Path]:
    html = tmp_path / "0912-ribao.html"
    html.write_text("<section class='scr'></section>", encoding="utf-8")
    Image.new("RGB", (1080, 1200), "white").save(tmp_path / "0912-ribao.png")
    Image.new("RGB", (1080, 460), "white").save(tmp_path / "0912-ribao-cover.jpg")
    return html, tmp_path


def _stub_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "xzq.ribao_audit.probe_subject_occlusion",
        lambda path: SubjectProbeReport(2, 801, []),
    )
    monkeypatch.setattr(
        "xzq.ribao_audit.probe_copy_occlusion",
        lambda path: CopyProbeReport(652131, 16053, 0, 0, 0),
    )
    monkeypatch.setattr(
        "xzq.ribao_audit.validate_image_identities",
        lambda paths: ImageIdentityReport(9, 9),
    )
    monkeypatch.setattr(
        "xzq.ribao_audit.probe_content_density",
        lambda path: DensityProbeReport(7183, [SectionDensity(1, "scr", 0, 460, .42, False)],
            [RenderedImage(2, "people", "rider.png", 432, 633, 650)]),
    )


def test_audit_writes_machine_readable_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    html, assets = _artifacts(tmp_path)
    _stub_probes(monkeypatch)
    monkeypatch.setattr("xzq.ribao_audit._git_facts", lambda root: {
        "short_sha": "abc1234", "subject": "test", "clean": True, "changes": []
    })

    report = audit_ribao(tmp_path, html_path=html, assets_dir=assets)

    evidence = tmp_path / "build" / "audits" / "0912-abc1234.json"
    assert evidence.is_file()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["artifacts"]["long_image"]["size"] == [1080, 1200]
    assert payload["probes"]["subject"]["checked_points"] == 801
    assert payload["probes"]["density"]["images"][0]["width"] == 432
    assert report.evidence_path == evidence


def test_audit_strict_rejects_dirty_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    html, assets = _artifacts(tmp_path)
    _stub_probes(monkeypatch)
    monkeypatch.setattr("xzq.ribao_audit._git_facts", lambda root: {
        "short_sha": "abc1234", "subject": "test", "clean": False, "changes": [" M file.py"]
    })

    with pytest.raises(AuditError, match="工作区不干净"):
        audit_ribao(tmp_path, html_path=html, assets_dir=assets, strict=True)


def test_archive_audit_records_explicit_probe_exemptions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    case = tmp_path / "docs" / "ribao-html" / "0910"
    exports = case / "exports"
    exports.mkdir(parents=True)
    (case / ".ribao-archive").write_text("frozen", encoding="utf-8")
    (case / "index.html").write_text("<html></html>", encoding="utf-8")
    (case / "cover.html").write_text("<html></html>", encoding="utf-8")
    Image.new("RGB", (1080, 1200), "white").save(exports / "0910-xzq-ribao-full.png")
    Image.new("RGB", (1080, 460), "white").save(exports / "0910-xzq-cover-full.png")
    monkeypatch.setattr("xzq.ribao_audit._git_facts", lambda root: {
        "short_sha": "abc1234", "subject": "test", "clean": True, "changes": []
    })
    monkeypatch.setattr("xzq.ribao_audit.probe_subject_occlusion", lambda path: pytest.fail("archive probe ran"))

    report = audit_ribao(tmp_path, date="20260910")

    assert report.payload["archived"] is True
    assert report.payload["exemptions"] == ["image_identity", "subject", "copy", "density"]
