# 微信公众号与 Strava 授权接入指南

本文介绍 `xiaohuoche-ai` 在本地接入微信公众号草稿箱和 Strava 官方 API 的完整授权流程。所有密钥只允许保存在仓库根目录的 `.env`，不得提交到 Git、聊天记录或截图中。

> 能力边界：微信公众号接入只负责把文章推进草稿箱；个人主体公众号仍需在手机端通过「公众号助手」预览并人工群发。Strava 仅使用官方 OAuth/API 采集数据，不使用爬虫或非官方 Hook。

## 1. 准备本地环境

```bash
cd /Users/bytedance/work/xiaohuoche-ai
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp -n .env.example .env
chmod 600 .env
```

项目会自动读取根目录 `.env`。即使未安装可选的 `python-dotenv`，也会使用内置解析器加载常见 `.env` 语法。

配置完成后统一执行：

```bash
python pipeline.py doctor
```

`doctor` 只显示配置和接口检查结果，不打印密钥。真实外部能力检查失败时会返回非零退出码。

---

## 2. 微信公众号授权接入

### 2.1 前置条件

需要准备一个可登录管理后台的微信公众号，并确认账号具备以下能力：

- 开发者基本配置可用；
- 可以获取 `AppID` 和 `AppSecret`；
- 可以维护调用 API 的 IP 白名单；
- 账号拥有草稿箱相关接口权限。

本项目调用微信官方 `client_credential` 接口获取 `access_token`，再使用素材上传和 `draft/add` 接口创建草稿。

### 2.2 获取 AppID 和 AppSecret

1. 登录[微信公众平台](https://mp.weixin.qq.com/)。
2. 进入「设置与开发」→「开发接口管理」或「基本配置」。
3. 复制开发者 ID（AppID）。
4. 查看或重置开发者密码（AppSecret）。
5. 把凭证写入本地 `.env`：

```dotenv
WECHAT_APP_ID=你的公众号AppID
WECHAT_APP_SECRET=你的公众号AppSecret
```

不要把真实值写入 `.env.example`、Markdown 文档或代码。

### 2.3 配置 IP 白名单

微信获取 `access_token` 时会校验请求来源的公网出口 IP。本地联调前需要：

1. 确认执行 `pipeline.py` 的机器当前公网出口 IP；
2. 在公众号后台「基本配置」的 IP 白名单中加入该 IP；
3. 保存配置后等待片刻，再执行健康检查。

如果公司网络、代理或 VPN 发生切换，公网出口 IP 可能变化，需要同步更新白名单。典型错误：

| 错误码 | 含义 | 处理方式 |
| --- | --- | --- |
| `40013` | AppID 无效 | 核对公众号后台的开发者 ID |
| `40125` | AppSecret 无效 | 在公众号后台重置 AppSecret，并更新本地 `.env` |
| `40164` | 出口 IP 不在白名单 | 把当前运行机器的公网出口 IP 加入白名单 |

### 2.4 验证微信鉴权

```bash
python pipeline.py doctor
```

预期输出：

```text
[doctor] 微信公众号：鉴权正常
```

微信 `access_token` 会缓存到：

```text
data/.wechat_token.json
```

缓存文件会绑定当前 `AppID`，并以 `0600` 权限保存。程序只在 token 即将过期时刷新，避免频繁调用微信接口导致旧 token 失效。

如需强制重新验证，可在确认没有其他进程依赖旧 token 后删除该缓存，再运行 `doctor`。不要手工编辑缓存内容。

### 2.5 验证草稿箱推送

先确保稿件已经完成审核和图片渲染，再执行：

```bash
python pipeline.py push <稿件ID>
```

例如：

```bash
python pipeline.py push 0909-ribao
```

推送链路为：

1. 获取或复用微信 `access_token`；
2. 上传正文图片；
3. 上传公众号封面并获取 `thumb_media_id`；
4. 调用 `draft/add` 创建图文草稿；
5. 在公众号后台或「公众号助手」中检查草稿；
6. 个人主体公众号在手机端人工预览、确认并群发。

验收时重点检查：

- 标题、作者和摘要正确；
- 封面没有被拉伸；
- 正文图片顺序、清晰度和裁切正确；
- 微信自动水印没有遮挡人物或关键文字；
- 草稿可在手机端正常预览。

---

## 3. Strava 授权接入

### 3.1 创建 Strava 应用

1. 登录 Strava。
2. 打开 [Strava API 设置](https://www.strava.com/settings/api)。
3. 创建应用并记录 `Client ID` 和 `Client Secret`。
4. 本地授权时可把 Authorization Callback Domain 配为 `localhost`。

### 3.2 发起 OAuth 授权

把下面地址中的 `<CLIENT_ID>` 替换为真实 Client ID，并确保回调地址与 Strava 后台配置一致：

```text
https://www.strava.com/oauth/authorize?client_id=<CLIENT_ID>&response_type=code&approval_prompt=force&scope=read,profile:read_all,activity:read_all&redirect_uri=http://localhost
```

所需 scope：

| Scope | 用途 |
| --- | --- |
| `read` | 读取基础账号信息 |
| `profile:read_all` | 读取完整运动员资料及相关统计 |
| `activity:read_all` | 读取本人全部活动、私密活动和路段 PR 数据 |

旧 token 不能自动扩权。如果健康检查提示 scope 不足，必须重新打开授权地址并完成授权。

授权后浏览器会跳转到类似地址：

```text
http://localhost/?state=&code=<一次性授权码>&scope=read,activity:read_all,profile:read_all
```

页面本身打不开不影响授权；从地址栏取得 `code` 即可。授权码有效期很短，应立即换取 token，并避免把授权码发到聊天中。

### 3.3 使用授权码交换 token

```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=<CLIENT_ID> \
  -d client_secret=<CLIENT_SECRET> \
  -d code=<一次性授权码> \
  -d grant_type=authorization_code
```

从返回结果中取得 `refresh_token`，写入本地 `.env`：

```dotenv
STRAVA_CLIENT_ID=你的ClientID
STRAVA_CLIENT_SECRET=你的ClientSecret
STRAVA_REFRESH_TOKEN=授权返回的RefreshToken
STRAVA_CLUB_URL=https://www.strava.com/clubs/Team_XZQ
STRAVA_CLUB_ID=780580
STRAVA_TOKEN_CACHE=./data/.strava_token.json
```

`access_token` 有效期较短，不需要写入 `.env`。程序会使用 `refresh_token` 自动刷新。

### 3.4 Refresh Token 轮换

Strava 在刷新 access token 时可能同时返回新的 refresh token，并立即使旧 token 失效。项目会自动把最新结果写入：

```text
data/.strava_token.json
```

缓存具备以下保护：

- 文件权限为 `0600`；
- 只在 `Client ID` 一致时复用；
- 缓存中的最新 refresh token 优先于 `.env` 中的初始值；
- 缓存已被 `.gitignore` 排除，不会提交到仓库。

不要在每次刷新后手工覆盖缓存，否则可能重新启用已经失效的旧 token。

### 3.5 配置辛庄桥小火车 Club

当前 Club 配置：

| 项目 | 值 |
| --- | --- |
| Club URL | `https://www.strava.com/clubs/Team_XZQ` |
| Club slug | `Team_XZQ` |
| API 数字 ID | `780580` |

授权账号必须已经加入目标 Club；私密 Club 对非成员可能返回 `403` 或 `404`。

### 3.6 验证 Strava 授权和数据采集

先运行完整诊断：

```bash
python pipeline.py doctor
```

Strava 部分应依次通过：

```text
[doctor] Strava 配置： 已配置
  [ok] 账号/refresh_token（1 条）
  [ok] Club 链接解析（1 条）
  [ok] 本人骑行统计（1 条）
  [ok] 私密 Club 活动（1 条）
  [ok] 本人活动/PR scope（1 条）
```

再验证真实素材采集：

```bash
python pipeline.py collect --date MMDD --kind ribao
```

采集能力包括：

- 本人近期和年度骑行统计；
- Club 最近活动、里程、用时和均速；
- 本人最近活动详情；
- 路段成绩及 `pr_rank` 标识的 PR 数据。

检查生成的稿件素材时，应确认 Strava 数据包含真实来源 ID。功率、心率、PR、时间和排名只能使用 API 返回值，不能由 AI 推测或补写。

### 3.7 常见故障

| 现象 | 常见原因 | 处理方式 |
| --- | --- | --- |
| 账号/refresh_token 失败 | refresh token 失效或 Client 不匹配 | 重新完成 OAuth，并更新 `.env` |
| 本人活动/PR 返回 `401` / `403` | 缺少 `activity:read_all` | 使用完整 scope 强制重新授权 |
| 私密 Club 活动返回 `403` / `404` | 授权账号未加入 Club，或 Club ID 错误 | 确认成员关系和 `STRAVA_CLUB_ID=780580` |
| 请求没有 HTTP 响应 | 网络、DNS、代理或 TLS 环境问题 | 先检查网络，不要连续更换 token |
| 首次刷新成功、随后 token 失效 | 仍在使用轮换前的旧 refresh token | 保留并复用 `data/.strava_token.json` |

---

## 4. 联调验收清单

### 微信公众号

- [ ] `.env` 已配置 `WECHAT_APP_ID` 和 `WECHAT_APP_SECRET`
- [ ] `.env` 权限为 `0600`
- [ ] 当前公网出口 IP 已加入公众号白名单
- [ ] `python pipeline.py doctor` 显示微信鉴权正常
- [ ] 测试稿成功进入草稿箱
- [ ] 手机端预览中的标题、封面、正文和水印安全区正确
- [ ] 未调用个人主体不具备权限的自动群发能力

### Strava

- [ ] OAuth 使用 `read,profile:read_all,activity:read_all`
- [ ] 授权账号已加入辛庄桥小火车 Club
- [ ] `doctor` 的五项 Strava 检查全部通过
- [ ] `data/.strava_token.json` 已生成且权限为 `0600`
- [ ] `collect` 能采集到真实 Club 活动和本人活动
- [ ] PR、功率、时间、排名等数字均可追溯到 Strava API

## 5. 安全约束

- `.env`、token 缓存、授权码、AppSecret 不得提交到 Git；
- 不在日志、Issue、MR、文档和截图中打印真实密钥；
- 不使用个人微信逆向机器人或非官方接口采集群消息；
- 微信个人主体公众号只自动进入草稿箱，不绕过手机端人工发布；
- Strava 只调用官方 OAuth/API，并遵守授权账号和 Club 的可见范围；
- 怀疑凭证泄漏时，立即在对应平台重置，并清理本地 token 缓存后重新授权。
