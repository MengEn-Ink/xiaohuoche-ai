# Xiaohuoche AI

`xiaohuoche-ai` 是面向骑行社群的 AI 辅助发布流水线。它把已授权的骑行素材整理成微信公众号草稿：采集素材、生成文案、审阅敏感细节、渲染长图，并可选推送到微信公众号草稿箱。

这个 GitHub 仓库是脱敏后的源码发布版。仓库不包含私有群聊截图、骑友照片、历史日报成品、导出 PNG、本地数据、AIME 站点代码或任何服务凭证。

## 功能概览

```text
素材 -> 采集 -> 写作 -> 审阅 -> 渲染 -> 草稿箱 -> 人工发布
       Strava   LLM    清单    HTML/PNG  微信 API
```

流水线采用保守的隐私模型：

- 不使用逆向个人微信机器人；
- 不编造骑行数据、PR 或成绩；
- 不对个人主体微信公众号做自动群发；
- 不提交密钥或真实私有素材。

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| `pipeline.py` | 采集、生成、审阅、渲染、检查和草稿推送的 CLI 入口。 |
| `xzq/collector/` | 素材 inbox、截图转写和 Strava API 集成。 |
| `xzq/writer/` | Prompt 组装、风格加载和离线兜底生成。 |
| `xzq/reviewer/` | 审阅状态、批注、检查清单、修订和预览服务。 |
| `xzq/renderer/` | HTML/raw/collage 渲染、图片身份检查、抠图、贴纸和长图导出。 |
| `xzq/publisher/` | 微信 access token、素材上传和草稿创建。 |
| `style/` | 可公开的写作模板、脱敏 few-shot、字体许可和渲染模板。 |
| `.trae/skills/xiaohuoche-ribao-html/` | 可复用的本地 skill，用于手工拼贴日报 HTML 和导出 PNG。 |
| `tests/` | 核心逻辑和发布门禁的 Python 回归测试。 |
| `scripts/` | 发布脚本和开源快照工具。 |
| `docs/` | 公开安装、授权和发布文档。 |

公开源码快照排除：`aime-site/`、`data/`、`docs/ribao-html/`、历史生成图片、ZIP 包和本地环境文件。

默认日报视觉语言遵循 XZQ 手工拼贴风格：单一天蓝底、高对比描边标题、抠图主体、贴纸和高密度编辑版式。

## 快速开始

需要 Python 3.9+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
cp config.example.yaml config.yaml
```

建议先跑离线路径。离线运行不需要微信、Strava 或 LLM 凭证。

```bash
python3 pipeline.py run --offline --date 0913 --kind ribao
```

常规本地流程：把已授权的照片、截图和简短 `draft.md` 放进 `XHC_INBOX_DIR` 或 `config.yaml` 配置的 inbox 目录，然后执行：

```bash
python3 pipeline.py collect --date 0913 --kind ribao
python3 pipeline.py generate --date 0913 --kind ribao
python3 pipeline.py review 0913-ribao
python3 pipeline.py approve 0913-ribao
python3 pipeline.py render 0913-ribao
```

长图渲染需要 Playwright Chromium。只有需要导出图片时再安装：

```bash
python3 -m playwright install chromium
```

## 素材采集台

公开仓库通过 GitHub Pages 提供一个纯静态素材采集台：

```text
https://mengen-ink.github.io/xiaohuoche-ai/
```

采集台只在浏览器本地处理内容，不上传照片、截图或密钥。它采用分页工作台：先填活动信息，再管理素材池，随后进入排版台调整素材顺序、版位、段落标题和副标题 / 短句，最后补充文案与 AI 要求。最终预览页是确认和导出入口，会按“日报故事板”展示段落标签、大标题、短句、图片和批注；后续实现应按预览中的文案、排序、版位、故事板标题、图片说明、AI 要求和隐私提醒执行。

推荐使用“导出完整素材包 ZIP”。ZIP 会保持固定结构：

```text
日期-栏目/
  draft.md
  instructions.md
  layout.json
  manifest.json
  README.txt
  preview/
    preview.html
  materials/
    original/
      原始文件或原目录结构
```

`instructions.md` 和 `layout.json` 记录已确认的文案、版位、素材顺序、故事板段落标题、副标题和 AI 批注；`preview/preview.html` 是人工复核用的日报故事板预览。`materials/original/` 中的图片按浏览器拿到的原始 File 字节写入，不经过 canvas，不压缩、不缩放、不改变像素大小。

## 可选外部服务

所有凭证都放在 `.env` 中，且必须保持未跟踪状态。

LLM 生成：

```text
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
LLM_VISION_MODEL=
```

Strava 数据：

```text
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_REFRESH_TOKEN=
STRAVA_CLUB_ID=
```

微信公众号草稿箱：

```text
WECHAT_APP_ID=
WECHAT_APP_SECRET=
```

运行本地能力检查，命令不会打印密钥：

```bash
python3 pipeline.py doctor
```

更多说明：

- [本地开发](docs/local-development.md)
- [微信与 Strava 授权](docs/wechat-strava-auth.md)
- [Strava 说明](docs/strava.md)
- [GitHub 源码快照发布](docs/github.md)

## 审阅与隐私

生成草稿在渲染或上传前必须经过人工审阅：

```bash
python3 pipeline.py preview 0913-ribao
python3 pipeline.py review 0913-ribao --check-all
python3 pipeline.py approve 0913-ribao
```

发布前确认：

- 照片中可识别人物已同意出现；
- 姓名、工作地点、车牌和无关路人已按需打码或移除；
- 骑行数据来自 Strava 或其他可信来源；
- 梗和玩笑限定在群内语境，避免人身攻击；
- 导出图片不会泄露私密聊天上下文。

个人主体微信公众号可以通过官方 API 创建草稿，但最终群发仍需在微信 App 或公众号后台人工完成。

## 开发检查

```bash
python3 -m compileall -q xzq pipeline.py scripts
python3 -m pytest -q
python3 scripts/build_open_source_snapshot.py --output /tmp/xiaohuoche-ai-open-source --force
python3 scripts/check_open_source_snapshot.py /tmp/xiaohuoche-ai-open-source
```

快照检查器如果直接跑在仍包含 `aime-site/`、`data/` 或 `docs/ribao-html/` 的私有工作树上，会按设计失败。

## 开源边界

公开 GitHub 仓库由白名单快照生成：

```bash
python3 scripts/build_open_source_snapshot.py \
  --output /tmp/xiaohuoche-ai-open-source \
  --force
```

该快照会在推送 GitHub 前排除私有素材和本地集成。发布流程见 [docs/github.md](docs/github.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。

## 商标声明

本项目是非官方第三方工具。Strava 和 WeChat 是各自权利人的商标。本项目不隶属于 Strava, Inc. 或 Tencent，也未获得其赞助或背书。
