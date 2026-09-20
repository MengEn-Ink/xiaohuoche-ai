#!/usr/bin/env python3
"""Validate that a directory is safe to publish as the GitHub source snapshot."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


class SnapshotViolation(RuntimeError):
    """Raised when a snapshot contains private material or misses governance files."""


REQUIRED_FILES = ("README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md")
FORBIDDEN_DIR_PARTS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "aime-site",
    "data",
    "node_modules",
}
FORBIDDEN_PREFIXES = ("docs/ribao-html/",)
FORBIDDEN_FILE_NAMES = {".env", "config.yaml"}
FORBIDDEN_SUFFIXES = {".zip", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_BINARY_PREFIXES = ("style/fonts/",)
SKIP_CONTENT_SCAN = {
    "scripts/check_open_source_snapshot.py",
    "tests/test_open_source_snapshot.py",
}
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]{12,}", re.IGNORECASE),
)
KEY_VALUE_PATTERN = re.compile(
    r"(?P<key>refresh_token|access_token|client_secret|app_secret|api_key|LLM_API_KEY|WECHAT_APP_SECRET|STRAVA_REFRESH_TOKEN)"
    r"\s*[:=]\s*['\"]?(?P<value>[^'\"\s,#]+)",
    re.IGNORECASE,
)
SAFE_PLACEHOLDER_VALUES = {
    "",
    "-",
    "secret",
    "test",
    "token",
    "refresh-old",
    "refresh-new",
    "cached-refresh",
    "cached-access",
    "wrong-refresh",
    "wrong-access",
    "right-refresh",
    "stale-env-refresh",
    "fresh-token",
    "wrong-token",
    "right-token",
    "fallback-refresh",
    "refresh_token",
    "client_secret",
    "access_token",
    "app_secret",
    "api_key",
}


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_allowed_binary(rel: str) -> bool:
    return any(rel.startswith(prefix) for prefix in ALLOWED_BINARY_PREFIXES)


def _is_forbidden_path(path: Path, root: Path) -> str | None:
    rel = _relative(path, root)
    parts = set(path.relative_to(root).parts)
    if parts & FORBIDDEN_DIR_PARTS:
        return rel
    if path.name in FORBIDDEN_FILE_NAMES:
        return rel
    if path.name.startswith(".env.") and path.name != ".env.example":
        return rel
    if any(rel.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return rel
    if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES and not _is_allowed_binary(rel):
        return rel
    return None


def _scan_content(path: Path, root: Path) -> str | None:
    rel = _relative(path, root)
    if rel in SKIP_CONTENT_SCAN or not path.is_file():
        return None
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or _is_allowed_binary(rel):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"non-UTF8 file outside allowlist: {rel}"
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return f"secret-like content in {rel}"
    for line in text.splitlines():
        match = KEY_VALUE_PATTERN.search(line)
        if not match:
            continue
        value = match.group("value").strip().strip("'\"")
        if _is_safe_placeholder_value(value):
            continue
        return f"secret-like content in {rel}"
    return None


def _is_safe_placeholder_value(value: str) -> bool:
    if value in SAFE_PLACEHOLDER_VALUES:
        return True
    if len(value) < 12:
        return True
    if not value.isascii():
        return True
    if value.startswith(("self.", "os.", "body[", "cached.", "config.", "values.")):
        return True
    if any(marker in value for marker in ("{", "}", "(", ")", "<", ">", "$", "YOUR_", "example", "EXAMPLE")):
        return True
    return False


def check_snapshot(root: Path | str) -> list[str]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise SnapshotViolation(f"snapshot root does not exist: {root}")

    violations: list[str] = []
    for required in REQUIRED_FILES:
        if not (root / required).is_file():
            violations.append(f"missing required file: {required}")

    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if not rel_parts or rel_parts[0] == ".git":
            continue
        forbidden = _is_forbidden_path(path, root)
        if forbidden:
            violations.append(f"forbidden path: {forbidden}")
            continue
        content_violation = _scan_content(path, root)
        if content_violation:
            violations.append(content_violation)

    if violations:
        raise SnapshotViolation("\n".join(violations))
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", help="snapshot root to validate")
    args = parser.parse_args(argv)
    try:
        check_snapshot(Path(args.root))
    except SnapshotViolation as error:
        print(str(error), file=sys.stderr)
        return 1
    print("open source snapshot check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
