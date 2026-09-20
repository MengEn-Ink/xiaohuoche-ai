from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_open_source_snapshot import build_snapshot
from scripts.check_open_source_snapshot import SnapshotViolation, check_snapshot


def _write_required_files(root: Path) -> None:
    for name in ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md"):
        (root / name).write_text(f"{name}\n", encoding="utf-8")


def test_snapshot_rejects_forbidden_paths(tmp_path: Path) -> None:
    _write_required_files(tmp_path)
    forbidden = tmp_path / "aime-site" / "private.txt"
    forbidden.parent.mkdir()
    forbidden.write_text("private", encoding="utf-8")

    with pytest.raises(SnapshotViolation, match="aime-site"):
        check_snapshot(tmp_path)


def test_snapshot_rejects_secret_like_content(tmp_path: Path) -> None:
    _write_required_files(tmp_path)
    (tmp_path / "README.md").write_text("token: github_pat_" + "a" * 40, encoding="utf-8")

    with pytest.raises(SnapshotViolation, match="secret-like content"):
        check_snapshot(tmp_path)


def test_snapshot_accepts_public_minimum(tmp_path: Path) -> None:
    _write_required_files(tmp_path)
    (tmp_path / ".env.example").write_text("LLM_API_KEY=\n", encoding="utf-8")
    (tmp_path / "xzq").mkdir()
    (tmp_path / "xzq" / "__init__.py").write_text("", encoding="utf-8")

    assert check_snapshot(tmp_path) == []


def test_snapshot_accepts_placeholders_tests_and_code_references(tmp_path: Path) -> None:
    _write_required_files(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "auth.md").write_text(
        "WECHAT_APP_SECRET=你的公众号AppSecret\n"
        "STRAVA_REFRESH_TOKEN=授权返回的RefreshToken\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_auth.py").write_text(
        'refresh_token="stale-env-refresh"\n'
        'client_secret="secret"\n',
        encoding="utf-8",
    )
    (tmp_path / "xzq").mkdir()
    (tmp_path / "xzq" / "client.py").write_text(
        "self.refresh_token = refresh_token\n"
        "self._access_token = body[\"access_token\"]\n"
        "self.refresh_token = str(body.get(\"refresh_token\") or self.refresh_token)\n",
        encoding="utf-8",
    )

    assert check_snapshot(tmp_path) == []


def test_builder_copies_public_files_and_excludes_private_paths(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "snapshot"
    (source / "xzq").mkdir(parents=True)
    (source / "xzq" / "__init__.py").write_text("", encoding="utf-8")
    (source / "README.md").write_text("# public\n", encoding="utf-8")
    (source / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (source / "CONTRIBUTING.md").write_text("Contribute\n", encoding="utf-8")
    (source / "SECURITY.md").write_text("Security\n", encoding="utf-8")
    (source / ".env.example").write_text("LLM_API_KEY=\n", encoding="utf-8")
    (source / "aime-site").mkdir()
    (source / "aime-site" / "app.txt").write_text("internal", encoding="utf-8")
    (source / "docs" / "ribao-html").mkdir(parents=True)
    (source / "docs" / "ribao-html" / "index.html").write_text("private", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / ".strava_token.json").write_text("{}", encoding="utf-8")
    (source / ".env").write_text("LLM_API_KEY=secret\n", encoding="utf-8")

    build_snapshot(source, output)

    assert (output / "xzq" / "__init__.py").is_file()
    assert not (output / "aime-site").exists()
    assert not (output / "docs" / "ribao-html").exists()
    assert not (output / "data").exists()
    assert not (output / ".env").exists()
