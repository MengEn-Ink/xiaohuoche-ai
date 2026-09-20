# Strava 接入指南

用小编本人 Strava 账号走官方 OAuth，自动拉俱乐部活动和路段 PR。**全程合规，不碰微信。**

## 1. 创建 Strava 应用
1. 打开 https://www.strava.com/settings/api ，创建应用：
   - Application Name：随便填（如 `xiaohuoche-ai`）
   - Authorization Callback Domain：填 `localhost`（本地换 token 用）
2. 拿到 **Client ID** 和 **Client Secret**。

## 2. 拿 refresh_token
浏览器打开下面链接（替换 `YOUR_CLIENT_ID`），登录授权：

```
https://www.strava.com/oauth/authorize?client_id=YOUR_CLIENT_ID&redirect_uri=http://localhost&response_type=code&approval_prompt=force&scope=read,profile:read_all,activity:read_all
```

授权后跳转地址栏里拿到 `code=xxxxx`，立刻换 token（code 很快过期）：

```bash
curl -X POST https://www.strava.com/oauth/token \
  -d client_id=YOUR_CLIENT_ID \
  -d client_secret=YOUR_CLIENT_SECRET \
  -d code=刚拿到的code \
  -d grant_type=authorization_code
```

返回 JSON 里的 `refresh_token` 长期有效，填进 `.env`：

```
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_REFRESH_TOKEN=...
STRAVA_CLUB_ID=...        # Riduck 俱乐部 ID，见下
```

> access_token 只有几小时，程序会用 refresh_token 自动刷新。Strava 可能在刷新时轮换 refresh_token，程序会把最新 token 安全写入 `data/.strava_token.json`（权限 `0600`），后续优先读取缓存，避免继续使用 `.env` 中已失效的旧值。

## 3. 辛庄桥小火车 Club

- 网页链接：https://www.strava.com/clubs/Team_XZQ
- slug：`Team_XZQ`
- API 数字 ID：`780580`
- 2026-09-08 实测：名称「辛庄桥小火车」、北京、私密 Club、72 人。

程序同时支持填写完整链接、slug 或数字 ID；完整链接会通过授权账号的 `/athlete/clubs` 列表解析成数字 ID。配置示例：

```yaml
strava:
  club_url: "https://www.strava.com/clubs/Team_XZQ"
  club_id: "780580"
```

## 4. 已集成的能力

| 方法 | Strava 接口 | 用途 |
| --- | --- | --- |
| `club_activities()` | `GET /clubs/{id}/activities` | 俱乐部最近谁骑了什么、里程、均速 |
| `recent_prs()` | `GET /athlete/activities` + `GET /activities/{id}?include_all_efforts=true` | 扫最近活动里刷出的**路段 PR**（`pr_rank` 非空），战报核心 |
| `segment_leaderboard()` | `GET /segments/{id}/leaderboard` | 路段排行榜 Top N（"进入 12x 时代"）|
| `athlete_stats()` | `GET /athletes/{id}/stats` | 近期/累计里程统计 |
| `collect_all()` | 上述汇总 | 一键采集，喂给 writer |

## 5. 权限诊断与实测结果

运行：

```bash
python pipeline.py doctor
```

2026-09-08 用当前仅 `read` scope 的 token 实测：
- ✅ refresh_token 自动换 token；
- ✅ 账号信息；
- ✅ Club 链接解析和 Club 元信息；
- ✅ 本人近 4 周/本年度骑行统计；
- ❌ 私密 Club 活动：404；
- ❌ 本人活动及 PR：401。

因此必须重新走第 2 步授权，scope 使用：

```
read,profile:read_all,activity:read_all
```

旧 refresh_token 无法自行扩权；重新授权后将返回的新 `refresh_token` 替换进 `.env`。

## 6. （可选）Webhook 自动触发
Strava 支持订阅活动事件（`POST /push_subscriptions`），有人上传新活动就回调你的服务，
可实现"一有人传活动就自动更新战报素材"。需要一个公网可达的回调地址，本地跑可先不接，
用定时 `python pipeline.py collect` 即可。

## 注意
- 功率、心率等字段取决于运动员是否公开数据；PR/路段成绩对授权账号可见。
- 战报里所有数字只认 Strava 返回，AI 不编造。
