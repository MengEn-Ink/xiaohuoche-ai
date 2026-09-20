# Xiaohuoche AI

`xiaohuoche-ai` is an AI-assisted publishing pipeline for cycling clubs. It turns approved ride materials into WeChat Official Account draft articles: collect materials, generate a styled script, review sensitive details, render long images, and optionally push the result into the WeChat draft box.

This GitHub repository is a sanitized source release. It does not include private group chat screenshots, rider photos, historical ribao outputs, generated PNGs, local data, AIME site code, or service credentials.

## What It Does

```text
materials -> collect -> write -> review -> render -> draft box -> manual publish
            Strava     LLM     checklist   HTML/PNG   WeChat API
```

The pipeline is designed around a conservative privacy model:

- no reverse-engineered personal WeChat bot;
- no invented ride stats or PR data;
- no automatic public publishing for personal-subject WeChat accounts;
- no committed secrets or real private materials.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `pipeline.py` | CLI entry point for collection, generation, review, rendering, checks, and draft push. |
| `xzq/collector/` | Material inbox handling, screenshot transcription, and Strava API integration. |
| `xzq/writer/` | Prompt assembly, style loading, and offline fallback generation. |
| `xzq/reviewer/` | Review state, annotations, checklist, revisions, and preview server. |
| `xzq/renderer/` | HTML/raw/collage rendering, image identity checks, cutouts, stickers, and long-image export. |
| `xzq/publisher/` | WeChat access token, media upload, and draft creation. |
| `style/` | Public writing templates, sample few-shot style material, font license files, and rendering templates. |
| `.trae/skills/xiaohuoche-ribao-html/` | Reusable local skill for hand-composed ribao HTML and PNG export. |
| `tests/` | Python regression tests for core logic and release gates. |
| `scripts/` | Release and open-source snapshot utilities. |
| `docs/` | Public setup and authorization documentation. |

Excluded from the public source snapshot: `aime-site/`, `data/`, `docs/ribao-html/`, historical generated images, ZIP packages, and local environment files.

The default ribao visual language follows the XZQ hand-composed style: 单一天蓝底, high-contrast outlined headline text, cutout subjects, stickers, and dense editorial layout.

## Quick Start

Use Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

Run the offline path first. It does not need WeChat, Strava, or LLM credentials.

```bash
python3 pipeline.py run --offline --date 0913 --kind ribao
```

For a normal local workflow, put approved photos, screenshots, and a short `draft.md` in the inbox directory configured by `XHC_INBOX_DIR` or `config.yaml`, then run:

```bash
python3 pipeline.py collect --date 0913 --kind ribao
python3 pipeline.py generate --date 0913 --kind ribao
python3 pipeline.py review 0913-ribao
python3 pipeline.py approve 0913-ribao
python3 pipeline.py render 0913-ribao
```

Rendering long images requires Playwright Chromium. Install it only when you need image export:

```bash
python3 -m playwright install chromium
```

## Optional External Services

All credentials live in `.env`, which must stay untracked.

LLM generation:

```text
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_VISION_MODEL=
```

Strava data:

```text
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REFRESH_TOKEN=
STRAVA_CLUB_ID=
```

WeChat draft box:

```text
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

Run a local capability check without printing secrets:

```bash
python3 pipeline.py doctor
```

More setup detail:

- [Local development](docs/local-development.md)
- [WeChat and Strava authorization](docs/wechat-strava-auth.md)
- [Strava notes](docs/strava.md)
- [GitHub source snapshot publishing](docs/github.md)

## Review And Privacy

Generated drafts should pass human review before rendering or uploading:

```bash
python3 pipeline.py preview 0913-ribao
python3 pipeline.py review 0913-ribao --check-all
python3 pipeline.py approve 0913-ribao
```

Before publication, confirm:

- people shown in photos agreed to appear;
- names, workplaces, license plates, and unrelated people are redacted where needed;
- ride metrics match Strava or another trusted source;
- jokes remain within the group context and avoid personal attacks;
- exported images do not reveal private chat context.

Personal-subject WeChat Official Accounts can create drafts through the official API, but final mass sending still requires manual action in the WeChat app or admin console.

## Development Checks

```bash
python3 -m compileall -q xzq pipeline.py scripts
python3 -m pytest -q
python3 scripts/build_open_source_snapshot.py --output /tmp/xiaohuoche-ai-open-source --force
python3 scripts/check_open_source_snapshot.py /tmp/xiaohuoche-ai-open-source
```

The snapshot checker intentionally fails if run against a private working tree that still contains excluded directories such as `aime-site/`, `data/`, or `docs/ribao-html/`.

## Open Source Boundary

The public GitHub repository is built from a whitelist by:

```bash
python3 scripts/build_open_source_snapshot.py \
  --output /tmp/xiaohuoche-ai-open-source \
  --force
```

That snapshot excludes private materials and local integrations before it is pushed to GitHub. See [docs/github.md](docs/github.md) for the publication flow.

## License

MIT, see [LICENSE](LICENSE).

## Trademark Notice

This project is an unofficial third-party tool. Strava and WeChat are trademarks of their respective owners. This project is not affiliated with, sponsored by, or endorsed by Strava, Inc. or Tencent.
