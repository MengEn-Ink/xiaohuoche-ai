"""Strava 官方 API 采集（合规、自动）。

用小编本人 Strava 账号走 OAuth：client_id/secret + refresh_token 换 access_token。
能力覆盖：
- club_activities   俱乐部最近活动（谁骑了什么、里程、均速）
- recent_activities 本人/关注运动员最近活动明细（含 segment_efforts）
- recent_prs        最近活动里刷出的路段 PR（战报硬数据核心）
- segment_leaderboard 路段排行榜
- athlete_stats     运动员近期/累计统计

文档：https://developers.strava.com/docs/reference/  鉴权：https://developers.strava.com/docs/authentication/
注意：segment effort / leaderboard 明细对已授权运动员可用；功率等字段取决于用户是否公开。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from ..models import Material

AUTH_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"


class StravaClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        club_id: str | None = None,
        club_url: str | None = None,
        token_cache: str | Path | None = None,
    ) -> None:
        self.client_id = client_id or os.getenv("STRAVA_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("STRAVA_CLIENT_SECRET", "")
        self.refresh_token = refresh_token or os.getenv("STRAVA_REFRESH_TOKEN", "")
        self.club_id = str(club_id or os.getenv("STRAVA_CLUB_ID", "")).strip()
        self.club_url = club_url or os.getenv("STRAVA_CLUB_URL", "")
        self.token_cache = Path(
            token_cache or os.getenv("STRAVA_TOKEN_CACHE", "./data/.strava_token.json")
        )
        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._load_token_cache()

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    # ---- 鉴权 ----
    def _load_token_cache(self) -> None:
        """缓存里的轮换 refresh_token 优先，避免继续使用已被 Strava 作废的旧值。"""
        if not self.token_cache.is_file():
            return
        try:
            cached = json.loads(self.token_cache.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if str(cached.get("client_id", "")) != str(self.client_id):
            return
        try:
            expires_at = float(cached.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0.0
        self.refresh_token = str(cached.get("refresh_token") or self.refresh_token)
        self._access_token = str(cached.get("access_token") or "")
        self._expires_at = expires_at

    def _write_token_cache(self) -> None:
        self.token_cache.parent.mkdir(parents=True, exist_ok=True)
        self.token_cache.write_text(
            json.dumps(
                {
                    "client_id": str(self.client_id),
                    "access_token": self._access_token,
                    "refresh_token": self.refresh_token,
                    "expires_at": self._expires_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            self.token_cache.chmod(0o600)
        except OSError:
            pass

    def _token(self) -> str:
        """refresh_token 换 access_token，并持久化 Strava 返回的轮换 token。"""
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        resp = requests.post(
            AUTH_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        self.refresh_token = str(body.get("refresh_token") or self.refresh_token)
        self._expires_at = float(body.get("expires_at", time.time() + 3600))
        self._write_token_cache()
        return self._access_token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = requests.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self._token()}"},
            params=params or {},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    # ---- 运动员 ----
    def athlete_me(self) -> dict[str, Any]:
        return self._get("/athlete")

    def athlete_stats(self, athlete_id: int | str) -> dict[str, Any]:
        return self._get(f"/athletes/{athlete_id}/stats")

    def athlete_stats_material(self) -> Material:
        """授权运动员骑行统计；read scope 即可，作为权限不足时仍可用的基础素材。"""
        athlete = self.athlete_me()
        stats = self.athlete_stats(athlete["id"])
        recent = stats.get("recent_ride_totals", {}) or {}
        ytd = stats.get("ytd_ride_totals", {}) or {}
        name = f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
        return Material(
            kind="strava",
            text=(
                f"{name} 近 4 周骑行 {recent.get('count', 0)} 次、"
                f"{(recent.get('distance', 0) or 0) / 1000:.1f}km；"
                f"今年累计 {ytd.get('count', 0)} 次、"
                f"{(ytd.get('distance', 0) or 0) / 1000:.1f}km"
            ),
            source=str(athlete.get("id", "")),
            meta={"recent": recent, "ytd": ytd},
        )

    # ---- 俱乐部 ----
    def resolve_club_id(self) -> str:
        """把数字 ID、slug 或完整 Club URL 解析成 API 所需数字 ID。

        Strava Web 链接常是 `/clubs/Team_XZQ`，API 则要求数字 ID。优先使用明确的数字
        `STRAVA_CLUB_ID`；否则从 `STRAVA_CLUB_URL` 提取 slug，再在授权账号的俱乐部列表中匹配。
        """
        if self.club_id.isdigit():
            return self.club_id
        ref = self.club_url or self.club_id
        slug = _club_slug(ref)
        if not slug:
            return ""
        if slug.isdigit():
            self.club_id = slug
            return self.club_id

        page = 1
        while True:
            clubs = self._get(
                "/athlete/clubs", params={"page": page, "per_page": 100}
            )
            if not clubs:
                break
            for club in clubs:
                if (
                    str(club.get("url", "")).casefold() == slug.casefold()
                    or str(club.get("id", "")) == slug
                ):
                    self.club_id = str(club["id"])
                    return self.club_id
            page += 1
        raise RuntimeError(f"授权账号未加入 Strava Club：{slug}")

    def club_detail(self) -> dict[str, Any]:
        club_id = self.resolve_club_id()
        return self._get(f"/clubs/{club_id}") if club_id else {}

    def club_activities(self, per_page: int = 30) -> list[Material]:
        """俱乐部最近活动 -> Material(kind=strava)。"""
        club_id = self.resolve_club_id()
        if not club_id:
            return []
        acts = self._get(
            f"/clubs/{club_id}/activities", params={"per_page": per_page}
        )
        return [self._activity_to_material(a) for a in acts]

    # ---- 活动明细 + PR ----
    def recent_activities(self, per_page: int = 10) -> list[dict[str, Any]]:
        """授权运动员最近活动（列表，不含 segment_efforts，需再拉详情）。"""
        return self._get(
            "/athlete/activities", params={"per_page": per_page}
        )

    def activity_detail(self, activity_id: int | str) -> dict[str, Any]:
        """活动详情，include_all_efforts=true 才返回全部路段成绩。"""
        return self._get(
            f"/activities/{activity_id}",
            params={"include_all_efforts": "true"},
        )

    def recent_prs(self, per_page: int = 10) -> list[Material]:
        """扫最近活动，提取刷出 PR 的路段成绩（pr_rank 非空即 PR/上榜）。

        这是日报战报"XX 怒改 路段 PR xx:xx"的权威数据来源，AI 不许另编。
        """
        out: list[Material] = []
        for act in self.recent_activities(per_page):
            detail = self.activity_detail(act["id"])
            rider = detail.get("athlete", {}).get("firstname", "") or "我"
            for eff in detail.get("segment_efforts", []) or []:
                if not eff.get("pr_rank"):  # pr_rank: 1=PR, 2/3=第二/三好成绩
                    continue
                out.append(
                    Material(
                        kind="strava",
                        text=(
                            f"{rider} 在「{eff.get('name', '路段')}」刷出 PR，"
                            f"成绩 {_fmt_elapsed(eff.get('elapsed_time'))}"
                            f"（pr_rank={eff.get('pr_rank')}）"
                        ),
                        source=str(eff.get("id", "")),
                        meta={
                            "segment": eff.get("name"),
                            "elapsed_time": eff.get("elapsed_time"),
                            "pr_rank": eff.get("pr_rank"),
                            "activity": act.get("name"),
                        },
                    )
                )
        return out

    def segment_leaderboard(self, segment_id: int | str, top: int = 10) -> list[Material]:
        """路段排行榜 Top N，用于"XX 进入黑山寨 12x 时代"这类榜单素材。"""
        data = self._get(
            f"/segments/{segment_id}/leaderboard",
            params={"per_page": top},
        )
        out: list[Material] = []
        for i, e in enumerate(data.get("entries", []), 1):
            out.append(
                Material(
                    kind="strava",
                    text=(
                        f"路段榜第{i}名：{e.get('athlete_name', '车手')} "
                        f"{_fmt_elapsed(e.get('elapsed_time'))}"
                    ),
                    source=str(segment_id),
                    meta={"rank": i, "elapsed_time": e.get("elapsed_time")},
                )
            )
        return out

    # ---- 汇总 ----
    def collect_all(self) -> list[Material]:
        """三路独立采集：运动统计 + 俱乐部活动 + 最近 PR。

        私密 Club 或 scope 不足时某一路可能返回 401/404；各路独立降级，避免一次权限失败
        把已经能读取的运动统计一并丢掉。失败原因作为 source_error 素材留证据，writer 会看见
        但不得把它当成文章事实。
        """
        mats: list[Material] = []
        collectors = (
            ("athlete_stats", lambda: [self.athlete_stats_material()]),
            ("club_activities", self.club_activities),
            ("recent_prs", self.recent_prs),
        )
        for name, collect in collectors:
            try:
                mats.extend(collect())
            except Exception as e:
                mats.append(
                    Material(
                        kind="source_error",
                        text=f"[{name} 暂不可用：{e}]",
                        meta={"source": name},
                    )
                )
        return mats

    @staticmethod
    def _activity_to_material(a: dict[str, Any]) -> Material:
        rider = (
            f"{a.get('athlete', {}).get('firstname', '')}"
            f"{a.get('athlete', {}).get('lastname', '')}"
        ).strip()
        text = (
            f"{rider} {a.get('name', '')} "
            f"{(a.get('distance', 0) or 0) / 1000:.1f}km "
            f"{_fmt_elapsed(a.get('moving_time'))} "
            f"均速 {(a.get('average_speed', 0) or 0) * 3.6:.1f}km/h"
        )
        return Material(
            kind="strava",
            text=text.strip(),
            source=str(a.get("id", "")),
            meta={"name": a.get("name"), "distance": a.get("distance")},
        )


def _club_slug(ref: str) -> str:
    """接受 `Team_XZQ` 或完整 URL，返回 Club slug；过滤 query/fragment/尾斜杠。"""
    ref = (ref or "").strip()
    if not ref:
        return ""
    if "://" in ref:
        path = urlparse(ref).path.rstrip("/")
        return path.split("/")[-1] if "/clubs/" in path else ""
    return ref.strip("/").split("/")[-1]


def _fmt_elapsed(seconds: float | int | None) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
