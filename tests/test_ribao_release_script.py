from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ribao_release.sh"


def _fake_python(tmp_path: Path, *, fail_step: str = "") -> tuple[Path, Path]:
    log = tmp_path / "calls.log"
    fake = tmp_path / "python"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f"if [[ \"$*\" == *'{fail_step}'* && -n '{fail_step}' ]]; then exit 7; fi\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake, log


def _article(tmp_path: Path) -> Path:
    out = tmp_path / "build"
    out.mkdir()
    long_image = out / "0912-ribao.png"
    cover = out / "0912-ribao-cover.jpg"
    html = out / "0912-ribao.html"
    long_image.write_bytes(b"long-image")
    cover.write_bytes(b"cover")
    html.write_text("<html></html>", encoding="utf-8")
    work = tmp_path / "data" / "articles"
    work.mkdir(parents=True)
    article = work / "0912-ribao.json"
    article.write_text(
        json.dumps({"image_path": str(long_image), "cover_path": str(cover)}),
        encoding="utf-8",
    )
    return article


def _run(tmp_path: Path, fake: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RIBAO_PYTHON": str(fake),
        "RIBAO_WORK_DIR": str(tmp_path / "data" / "articles"),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), "--date", "0912"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=False,
    )


def test_release_script_runs_render_check_audit_in_order_and_prints_artifacts(tmp_path: Path):
    _article(tmp_path)
    fake, log = _fake_python(tmp_path)

    result = _run(tmp_path, fake)

    assert result.returncode == 0, result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls[0].endswith("pipeline.py render 0912-ribao")
    assert "pipeline.py ribao-check --html" in calls[1]
    assert "pipeline.py ribao-audit --html" in calls[2]
    assert "--strict" in calls[2]
    assert "0912-ribao.png" in result.stdout
    assert "0912-ribao-cover.jpg" in result.stdout
    assert "sha256=" in result.stdout


def test_release_script_stops_and_names_failed_step(tmp_path: Path):
    _article(tmp_path)
    fake, log = _fake_python(tmp_path, fail_step="ribao-check")

    result = _run(tmp_path, fake)

    assert result.returncode != 0
    assert "失败步骤：ribao-check" in result.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    assert len(calls) == 2
