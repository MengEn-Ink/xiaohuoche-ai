# 本地开发与联调

本文面向 macOS 本地仓库 `/Users/bytedance/work/xiaohuoche-ai`，覆盖首次配置、健康检查、离线冒烟和真实外部能力联调。任何密钥都只保存在本地 `.env`，不得提交、粘贴到 Issue/MR 或写入命令日志。

## 1. 初始化环境

```bash
cd /Users/bytedance/work/xiaohuoche-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m playwright install chromium
```

`config.yaml` 可选。仓库没有该文件时会自动读取 `config.example.yaml`，因此只做离线验证无需复制。

## 2. 配置 `.env`

从可信旧环境迁移时直接复制文件，不要把密钥逐项粘贴到终端：

```bash
cp /可信路径/.env .env
chmod 600 .env
```

也可以从示例开始：

```bash
cp .env.example .env
chmod 600 .env
```

配置分为三组：

| 能力 | 环境变量 | 未配置时 |
| --- | --- | --- |
| Strava | `STRAVA_CLIENT_ID`、`STRAVA_CLIENT_SECRET`、`STRAVA_REFRESH_TOKEN`、`STRAVA_CLUB_ID` / `STRAVA_CLUB_URL` | 仍可读取素材筐，不能拉骑行数据 |
| LLM | `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_VISION_MODEL` | 只能生成带“禁止发布”标记的离线骨架 |
| 微信草稿箱 | `WECHAT_APP_ID`、`WECHAT_APP_SECRET` | 不能执行 `push`，不影响写稿和出图 |

路径变量 `XHC_INBOX_DIR`、`XHC_WORK_DIR` 建议保留相对路径，便于仓库整体移动。项目会自动读取仓库根目录 `.env`；即使本机未安装可选的 `python-dotenv` 依赖，也会使用内置解析器加载常见 `.env` 语法。

Strava 刷新授权时可能返回轮换后的 `refresh_token`。程序默认将最新 token 写入 `data/.strava_token.json` 并优先复用，避免 `.env` 中的旧 token 在首次刷新后失效；可用 `STRAVA_TOKEN_CACHE` 修改缓存路径。

## 3. 健康检查

```bash
python pipeline.py doctor
```

`doctor` 不打印密钥。已配置的外部能力探活失败时命令返回非零；未配置的 LLM/微信只提示能力降级，不影响离线流程。

### Strava 常见结果

- `账号/refresh_token` 失败：refresh token 失效、Client 不匹配，或本机网络无法访问 Strava。
- `本人活动/PR scope` 返回 401/403：重新授权时必须包含 `read,profile:read_all,activity:read_all`。
- `私密 Club 活动` 返回 403/404：确认授权账号已加入目标 Club，并核对 `STRAVA_CLUB_ID=780580`。
- `请求未获得 HTTP 响应`：优先检查本机网络、DNS 和代理，不要反复更换 token。

重新授权入口格式如下；把 `<CLIENT_ID>` 替换为自己的 Strava Client ID，并使用 Strava 后台登记的回调地址：

```text
https://www.strava.com/oauth/authorize?client_id=<CLIENT_ID>&response_type=code&approval_prompt=force&scope=read,profile:read_all,activity:read_all&redirect_uri=<CALLBACK_URL>
```

授权完成后用官方 OAuth token 接口交换新的 refresh token，再只更新本地 `.env`。

## 4. 离线冒烟

离线命令会执行素材采集和脚本骨架生成，不需要 LLM 或微信配置：

```bash
python pipeline.py run --offline --date 0909 --kind ribao
python pipeline.py list
```

成功信号：生成 `data/articles/0909-ribao.json`，状态为 `draft`，包含 8 个固定栏目。离线骨架不可发布；必须接入 LLM 或人工补齐真实文案后重新走审核。

## 5. 测试与发布门禁

```bash
# 全量单测
python -m pytest -q

# 覆盖率
python -m coverage run -m pytest -q
python -m coverage report -m

# 静态检查（全仓当前保留部分历史风格告警，交付至少保证语法/未定义引用与新增测试通过）
python -m ruff check tests/test_doctor.py
python -m ruff check --select E9,F pipeline.py xzq tests

# 0908 唯一日报范本发布门禁
python pipeline.py ribao-check --date 20260908
```

发布门禁必须检查通过：标题、图片唯一性、人物安全区、紧凑密度、微信右下角水印安全区、few-shot、封面尺寸和正文分屏 PNG。

## 6. 真实链路

```bash
python pipeline.py collect --date MMDD --kind ribao
python pipeline.py generate --date MMDD --kind ribao
python pipeline.py review MMDD-ribao
python pipeline.py review MMDD-ribao --check-all
python pipeline.py approve MMDD-ribao
python pipeline.py render MMDD-ribao
python pipeline.py push MMDD-ribao
```

`push` 只进入公众号草稿箱。个人主体公众号最后的预览和群发必须在手机端人工完成。
