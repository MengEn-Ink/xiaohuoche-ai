"""Read-only release evidence collector for rendered日报 artifacts."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .copy_probe import probe_copy_occlusion
from .density_probe import probe_content_density
from .image_identity import validate_image_identities
from .subject_probe import probe_subject_occlusion


class AuditError(RuntimeError):
    """Release evidence cannot be proved."""


@dataclass(frozen=True)
class AuditReport:
    evidence_path: Path
    payload: dict[str, object]


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise AuditError(f"git 取证失败：{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.rstrip("\n")


def _git_facts(root: Path) -> dict[str, object]:
    changes = [line for line in _run_git(root, "status", "--short").splitlines() if line]
    return {
        "short_sha": _run_git(root, "rev-parse", "--short", "HEAD"),
        "subject": _run_git(root, "log", "-1", "--pretty=%s"),
        "clean": not changes,
        "changes": changes,
    }


def _artifact(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise AuditError(f"缺少产物：{path}")
    try:
        with Image.open(path) as image:
            size = [image.width, image.height]
    except Exception as exc:
        raise AuditError(f"产物不可读：{path}: {exc}") from exc
    return {
        "path": str(path.resolve()),
        "size": size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _resolve_targets(
    root: Path, date: str | None, html_path: Path | None, assets_dir: Path | None
) -> tuple[str, Path, list[Path], Path, Path, bool]:
    if date:
        if len(date) != 8 or not date.isdigit():
            raise AuditError("date 必须为 YYYYMMDD")
        md = date[4:]
        case = root / "docs" / "ribao-html" / md
        html = case / "index.html"
        htmls = [html, case / "cover.html"]
        return (
            md, html, htmls, case / "exports" / f"{md}-xzq-ribao-full.png",
            case / "exports" / f"{md}-xzq-cover-full.png",
            (case / ".ribao-archive").is_file(),
        )
    if html_path is None or assets_dir is None:
        raise AuditError("必须提供 --date，或同时提供 --html 与 --assets-dir")
    html = Path(html_path).resolve()
    output = Path(assets_dir).resolve()
    stem = html.stem
    label = next((part for part in stem.split("-") if len(part) == 4 and part.isdigit()), stem)
    return label, html, [html], output / f"{stem}.png", output / f"{stem}-cover.jpg", (html.parent / ".ribao-archive").is_file()


def audit_ribao(
    root: Path, *, date: str | None = None, html_path: Path | None = None,
    assets_dir: Path | None = None, strict: bool = False,
) -> AuditReport:
    root = Path(root).resolve()
    label, html, identity_pages, long_image, cover, archived = _resolve_targets(
        root, date, html_path, assets_dir
    )
    git = _git_facts(root)
    if strict and not git["clean"]:
        raise AuditError("工作区不干净（--strict）：" + ", ".join(git["changes"]))
    payload: dict[str, object] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git": git,
        "archived": archived,
        "exemptions": [],
        "artifacts": {
            "html": str(html),
            "long_image": _artifact(long_image),
            "cover": _artifact(cover),
        },
        "probes": {},
    }
    if archived:
        payload["exemptions"] = ["image_identity", "subject", "copy", "density"]
    else:
        try:
            identity = validate_image_identities(identity_pages)
            subject = probe_subject_occlusion(html)
            copy = probe_copy_occlusion(html)
            density = probe_content_density(html)
        except Exception as exc:
            raise AuditError(str(exc)) from exc
        payload["probes"] = {
            "identity": asdict(identity),
            "subject": asdict(subject),
            "copy": asdict(copy),
            "density": {
                "total_height": density.total_height,
                "sections": [asdict(item) for item in density.sections],
                "images": [asdict(item) for item in density.images],
            },
        }
    evidence = root / "build" / "audits" / f"{label}-{git['short_sha']}.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return AuditReport(evidence, payload)
