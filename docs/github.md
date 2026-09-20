# 发布开源快照到 GitHub

本项目以脱敏源码快照的方式发布到 GitHub。公开仓库不得包含私有骑行素材、已生成的日报成品、`aime-site/`、本地数据或密钥。

## 生成快照

在私有工作树中执行：

```bash
python3 scripts/build_open_source_snapshot.py \
  --output /tmp/xiaohuoche-ai-open-source \
  --force
```

构建器只复制公开白名单中的文件，并在返回前运行快照检查器。

## 校验快照

```bash
python3 scripts/check_open_source_snapshot.py /tmp/xiaohuoche-ai-open-source
find /tmp/xiaohuoche-ai-open-source -maxdepth 2 -type d | sort
```

输出中不得出现：

- `aime-site/`
- `data/`
- `docs/ribao-html/`
- `.env`
- `config.yaml`
- 导出图片或 ZIP 文件

## 发布

使用已登录的 GitHub CLI，或配置具备创建仓库和推送权限的 `GH_TOKEN`：

```bash
gh auth status --hostname github.com
gh repo create MengEn-Ink/xiaohuoche-ai \
  --public \
  --description "[xiaohuoche-ai] AI pipeline for cycling club WeChat drafts" \
  --disable-wiki
```

然后在生成的快照目录中推送：

```bash
cd /tmp/xiaohuoche-ai-open-source
git init
git checkout -b main
git add .
git commit -m "chore: publish open source snapshot"
git remote add origin https://github.com/MengEn-Ink/xiaohuoche-ai.git
git push -u origin main
```

如果目标仓库已经存在，必须先确认它属于预期账号，且描述中包含 `[xiaohuoche-ai]` 标记，再继续推送。

## 发布 GitHub Pages

公开快照包含 `pages/material-studio/` 和 `.github/workflows/pages.yml`。推送 `main` 后，GitHub Actions 会把素材采集台部署到 Pages：

```text
https://mengen-ink.github.io/xiaohuoche-ai/
```

如果 Pages 还没有启用，需要把仓库 Pages 来源设置为 GitHub Actions：

```bash
gh api -X POST repos/MengEn-Ink/xiaohuoche-ai/pages \
  -f build_type=workflow
```

采集台是纯静态页面，只生成本地下载文件，不上传真实素材。

采集台推荐导出完整素材包 ZIP，结构固定为：

```text
日期-栏目/
  draft.md
  manifest.json
  README.txt
  materials/
    original/
      原始文件或原目录结构
```

`materials/original/` 中的文件以浏览器读取到的原始字节写入 ZIP，不经过 canvas 压缩或像素重采样。

## 验证

```bash
git ls-remote --heads https://github.com/MengEn-Ink/xiaohuoche-ai.git main
gh repo view MengEn-Ink/xiaohuoche-ai --json url,visibility,defaultBranchRef
```

仓库应为公开仓库，默认分支应为 `main`，内容应只包含脱敏源码快照。
