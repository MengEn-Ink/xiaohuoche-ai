# 贡献指南

感谢你改进 `xiaohuoche-ai`。这个仓库只发布源码，不接收私有骑行素材、群聊截图、个人照片、已生成的微信公众号草稿、访问 token 或环境文件。

## 开发环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

外部服务对开发不是必需项。离线路径应当在没有微信、Strava 或 LLM 凭证的情况下继续可用。

## 提交前检查

提交 Pull Request 前至少运行公开快照可用的检查：

```bash
python3 -m compileall -q xzq pipeline.py scripts
python3 -m pytest -q \
  tests/test_open_source_snapshot.py \
  tests/test_config.py \
  tests/test_strava_auth.py \
  tests/test_wechat_token.py \
  tests/test_pipeline.py \
  tests/test_quality_check.py
python3 scripts/check_open_source_snapshot.py .
```

快照检查器面向公开 GitHub 快照。如果直接跑在仍包含 `aime-site/`、`data/` 或历史日报成品的私有工作树上，它会按设计失败。

## 隐私规则

- 不要提交 `.env`、`config.yaml`、token cache、截图、导出 PNG、ZIP 包或真实用户素材。
- 测试和文档使用合成样例或许可清晰的素材。
- 生成产物默认不进源码，除非它们体积小、可复现且确认可公开。
- 如果某个问题必须依赖私有素材复现，请描述结构并打码内容。

## 提交格式

优先使用简洁的 Conventional Commits：

```text
feat(scope): add behavior
fix(scope): correct behavior
docs(scope): update documentation
test(scope): cover behavior
chore(scope): maintain tooling
```
