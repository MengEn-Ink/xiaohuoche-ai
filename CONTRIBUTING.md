# Contributing

Thanks for improving `xiaohuoche-ai`. This repository is published as source code only. Do not contribute private riding materials, group chat screenshots, personal photos, generated WeChat drafts, access tokens, or environment files.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

External services are optional for development. The offline path should continue to work without WeChat, Strava, or LLM credentials.

## Checks

Run the focused checks before opening a pull request:

```bash
python3 -m compileall -q xzq pipeline.py scripts
python3 -m pytest -q
python3 scripts/check_open_source_snapshot.py .
```

The snapshot checker is meant for the public GitHub snapshot. It will intentionally fail in a private working tree that still contains `aime-site/`, `data/`, or historical ribao outputs.

## Privacy Rules

- Do not commit `.env`, `config.yaml`, token caches, screenshots, exported PNGs, ZIP packages, or real user materials.
- Use synthetic examples or clearly licensed assets in tests and docs.
- Keep generated artifacts out of source control unless they are small, deterministic, and safe to publish.
- If a bug requires private material to reproduce, describe the structure and redact the content.

## Commit Style

Use concise conventional commits when practical:

```text
feat(scope): add behavior
fix(scope): correct behavior
docs(scope): update documentation
test(scope): cover behavior
chore(scope): maintain tooling
```
