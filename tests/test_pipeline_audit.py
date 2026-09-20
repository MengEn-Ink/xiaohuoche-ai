from pathlib import Path
from types import SimpleNamespace

import pipeline


def test_cmd_ribao_audit_prints_git_artifacts_probes_and_evidence(monkeypatch, capsys):
    payload = {
        "git": {"short_sha": "abc1234", "subject": "audit", "clean": True, "changes": []},
        "archived": False, "exemptions": [],
        "artifacts": {
            "long_image": {"path": "/tmp/long.png", "size": [1080, 7183], "sha256": "a" * 64},
            "cover": {"path": "/tmp/cover.jpg", "size": [1080, 460], "sha256": "b" * 64},
        },
        "probes": {
            "subject": {"checked_images": 2, "checked_points": 801, "occlusions": []},
            "copy": {"copy_pixels": 600000, "sticker_overlap_pixels": 0, "image_overlap_pixels": 0, "overlap_pixels": 0},
            "identity": {"image_count": 9, "identity_count": 9},
            "density": {"total_height": 7183, "sections": [{"index": 1, "fill_ratio": .42}], "images": [{"section": 2, "kind": "people", "source": "rider.png", "width": 432, "height": 633, "section_height": 650}]},
        },
    }
    monkeypatch.setattr(pipeline, "audit_ribao", lambda *a, **k: SimpleNamespace(evidence_path=Path("/tmp/audit.json"), payload=payload))

    pipeline.cmd_ribao_audit(SimpleNamespace(date=None, html="x.html", assets_dir="out", strict=False))

    output = capsys.readouterr().out
    for expected in ("abc1234 audit", "1080×7183", "人物：2 图 / 801 点 / 0 遮挡", "文字：600000", "9 图 / 9 身份", "第 1 屏 42.00%", "rider.png 432×633", "/tmp/audit.json"):
        assert expected in output


def test_cli_parser_accepts_ribao_audit_html_mode(monkeypatch):
    called = {}
    monkeypatch.setattr(pipeline, "cmd_ribao_audit", lambda args: called.update(vars(args)))
    monkeypatch.setattr("sys.argv", ["pipeline.py", "ribao-audit", "--html", "build/x.html", "--assets-dir", "build", "--strict"])

    assert pipeline.main() == 0
    assert called["html"] == "build/x.html"
    assert called["strict"] is True


def test_cli_parser_accepts_ribao_check_html_mode(monkeypatch):
    called = {}
    monkeypatch.setattr(pipeline, "cmd_ribao_check", lambda args: called.update(vars(args)))
    monkeypatch.setattr(
        "sys.argv",
        ["pipeline.py", "ribao-check", "--html", "build/x.html", "--assets-dir", "build"],
    )

    assert pipeline.main() == 0
    assert called["html"] == "build/x.html"
    assert called["date"] is None


def test_cmd_ribao_audit_warns_and_lists_dirty_worktree(monkeypatch, capsys):
    payload = {
        "git": {
            "short_sha": "abc1234", "subject": "audit", "clean": False,
            "changes": [" M pipeline.py", "?? xzq/ribao_audit.py"],
        },
        "archived": True,
        "exemptions": ["subject"],
        "artifacts": {
            "long_image": {"path": "/tmp/long.png", "size": [1080, 1200], "sha256": "a" * 64},
            "cover": {"path": "/tmp/cover.jpg", "size": [1080, 460], "sha256": "b" * 64},
        },
        "probes": {},
    }
    monkeypatch.setattr(
        pipeline, "audit_ribao",
        lambda *a, **k: SimpleNamespace(
            evidence_path=Path("/tmp/audit.json"), payload=payload
        ),
    )

    pipeline.cmd_ribao_audit(
        SimpleNamespace(date="20260910", html=None, assets_dir=None, strict=False)
    )

    output = capsys.readouterr().out
    assert "[warn] 工作区不干净" in output
    assert "M pipeline.py" in output
    assert "?? xzq/ribao_audit.py" in output
