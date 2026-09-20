#!/usr/bin/env bash
# Dependencies: project .venv with Playwright Chromium, YuNet model and rembg/u2net ready.
# Red lines: trial outputs belong only in build/; never modify archived日报 directories.
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PYTHON_BIN=${RIBAO_PYTHON:-"$ROOT/.venv/bin/python"}
JSON_PYTHON_BIN=${RIBAO_JSON_PYTHON:-"$ROOT/.venv/bin/python"}
WORK_DIR=${RIBAO_WORK_DIR:-"$ROOT/data/articles"}
STEP=arguments

fail() {
  local code=$?
  printf '[error] 失败步骤：%s（exit %s）\n' "$STEP" "$code" >&2
  exit "$code"
}
trap fail ERR

if [[ ${1:-} != "--date" || ! ${2:-} =~ ^[0-9]{4}$ || $# -ne 2 ]]; then
  printf '用法：%s --date MMDD\n' "$0" >&2
  exit 2
fi

DATE=$2
ARTICLE_ID="${DATE}-ribao"
ARTICLE_JSON="$WORK_DIR/${ARTICLE_ID}.json"

STEP=render
"$PYTHON_BIN" "$ROOT/pipeline.py" render "$ARTICLE_ID"

STEP=resolve-artifacts
ARTIFACT_OUTPUT=$("$JSON_PYTHON_BIN" - "$ARTICLE_JSON" <<'PY'
import json
import sys
from pathlib import Path

article = Path(sys.argv[1])
if not article.is_file():
    raise SystemExit(f"缺少稿件 JSON：{article}")
payload = json.loads(article.read_text(encoding="utf-8"))
for key in ("image_path", "cover_path"):
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"稿件缺少 {key}")
    print(Path(value).resolve())
PY
)
LONG_IMAGE=$(printf '%s\n' "$ARTIFACT_OUTPUT" | sed -n '1p')
COVER=$(printf '%s\n' "$ARTIFACT_OUTPUT" | sed -n '2p')
ASSETS_DIR=$(dirname "$LONG_IMAGE")
HTML="$ASSETS_DIR/${ARTICLE_ID}.html"

STEP=ribao-check
"$PYTHON_BIN" "$ROOT/pipeline.py" ribao-check --html "$HTML" --assets-dir "$ASSETS_DIR"

STEP=ribao-audit
"$PYTHON_BIN" "$ROOT/pipeline.py" ribao-audit --html "$HTML" --assets-dir "$ASSETS_DIR" --strict

STEP=summary
for artifact in "$LONG_IMAGE" "$COVER"; do
  [[ -f $artifact ]]
  sha=$(shasum -a 256 "$artifact" | awk '{print $1}')
  printf '[artifact] %s | sha256=%s\n' "$artifact" "$sha"
done
printf '[ok] 日报发布流程全部通过\n'
