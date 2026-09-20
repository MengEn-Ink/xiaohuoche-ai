#!/usr/bin/env python3
"""Build a sanitized source snapshot for GitHub publication."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

try:
    from scripts.check_open_source_snapshot import check_snapshot
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_open_source_snapshot import check_snapshot


PUBLIC_FILES = (
    ".env.example",
    ".gitignore",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "config.example.yaml",
    "docs/github.md",
    "docs/local-development.md",
    "docs/strava.md",
    "docs/wechat-strava-auth.md",
    "pipeline.py",
    "requirements-dev.txt",
    "requirements.txt",
    "scripts/ribao_release.sh",
)
PUBLIC_DIRS = (
    ".github/workflows",
    ".trae/skills/xiaohuoche-ribao-html/references",
    "pages",
    "style/fonts",
    "style/templates",
    "tests",
    "xzq",
)
PUBLIC_GLOBS = (
    ".trae/skills/xiaohuoche-ribao-html/*.md",
    ".trae/skills/xiaohuoche-ribao-html/scripts/*.py",
    "scripts/*.py",
    "style/*.md",
    "style/*.yaml",
    "style/few_shot/README.md",
    "style/few_shot/202405*.*",
    "style/few_shot/20240605-*.*",
    "style/stickers/external/*.svg",
)
EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "aime-site",
    "data",
    "node_modules",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _should_copy(path: Path, source_root: Path) -> bool:
    rel = path.relative_to(source_root).as_posix()
    if any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(source_root).parts):
        return False
    if rel.startswith("docs/ribao-html/"):
        return False
    if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
        return False
    if path.name == "config.yaml":
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def _copy_file(source_root: Path, output_root: Path, source: Path) -> None:
    if not source.is_file() or not _should_copy(source, source_root):
        return
    target = output_root / source.relative_to(source_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_dir(source_root: Path, output_root: Path, relative_dir: str) -> None:
    root = source_root / relative_dir
    if not root.is_dir():
        return
    for source in sorted(root.rglob("*")):
        if source.is_file():
            _copy_file(source_root, output_root, source)


def build_snapshot(source_root: Path | str, output_root: Path | str, *, force: bool = False) -> Path:
    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    if not source_root.is_dir():
        raise ValueError(f"source root does not exist: {source_root}")
    if output_root == source_root or source_root in output_root.parents:
        raise ValueError("output root must be outside the source repository")
    if output_root.exists():
        if not force:
            raise FileExistsError(f"output root already exists: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    for relative_file in PUBLIC_FILES:
        _copy_file(source_root, output_root, source_root / relative_file)
    for relative_dir in PUBLIC_DIRS:
        _copy_dir(source_root, output_root, relative_dir)
    for pattern in PUBLIC_GLOBS:
        for source in sorted(source_root.glob(pattern)):
            _copy_file(source_root, output_root, source)

    check_snapshot(output_root)
    return output_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=".", help="source repository root")
    parser.add_argument("--output", required=True, help="output snapshot directory")
    parser.add_argument("--force", action="store_true", help="replace the output directory if it exists")
    args = parser.parse_args(argv)
    snapshot = build_snapshot(Path(args.source), Path(args.output), force=args.force)
    print(f"open source snapshot written to {snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
