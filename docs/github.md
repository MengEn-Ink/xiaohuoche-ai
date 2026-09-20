# Publishing The Open Source Snapshot To GitHub

This project is published to GitHub as a sanitized source snapshot. The public repository must not include private riding materials, generated ribao outputs, `aime-site/`, local data, or secrets.

## Build The Snapshot

From the private working tree:

```bash
python3 scripts/build_open_source_snapshot.py \
  --output /tmp/xiaohuoche-ai-open-source \
  --force
```

The builder copies only the public whitelist and runs the snapshot checker before returning.

## Validate The Snapshot

```bash
python3 scripts/check_open_source_snapshot.py /tmp/xiaohuoche-ai-open-source
find /tmp/xiaohuoche-ai-open-source -maxdepth 2 -type d | sort
```

The output must not contain:

- `aime-site/`
- `data/`
- `docs/ribao-html/`
- `.env`
- `config.yaml`
- exported images or ZIP files

## Publish

Use GitHub CLI authentication or a `GH_TOKEN` with repository creation and push permission:

```bash
gh auth status --hostname github.com
gh repo create MengEn-Ink/xiaohuoche-ai \
  --public \
  --description "[xiaohuoche-ai] AI pipeline for cycling club WeChat drafts" \
  --disable-wiki
```

Then push from the generated snapshot:

```bash
cd /tmp/xiaohuoche-ai-open-source
git init
git checkout -b main
git add .
git commit -m "chore: publish open source snapshot"
git remote add origin https://github.com/MengEn-Ink/xiaohuoche-ai.git
git push -u origin main
```

If the repository already exists, verify that it belongs to the expected owner and its description contains `[xiaohuoche-ai]` before pushing.

## Verify

```bash
git ls-remote --heads https://github.com/MengEn-Ink/xiaohuoche-ai.git main
gh repo view MengEn-Ink/xiaohuoche-ai --json url,visibility,defaultBranchRef
```

The repository should be public, use `main` as the default branch, and contain only the sanitized source snapshot.
