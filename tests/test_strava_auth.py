from __future__ import annotations

import json
import time
from pathlib import Path

from xzq.collector.strava import StravaClient


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


def test_strava_persists_rotated_refresh_token(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "strava-token.json"
    calls: list[dict] = []

    def fake_post(url: str, data: dict, timeout: int) -> _Response:
        calls.append(data)
        return _Response(
            {
                "access_token": "access-new",
                "refresh_token": "refresh-new",
                "expires_at": time.time() + 3600,
            }
        )

    monkeypatch.setattr("xzq.collector.strava.requests.post", fake_post)
    client = StravaClient(
        client_id="client-1",
        client_secret="secret",
        refresh_token="refresh-old",
        token_cache=cache,
    )

    assert client._token() == "access-new"
    assert calls[0]["refresh_token"] == "refresh-old"
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "refresh-new"
    assert client.refresh_token == "refresh-new"
    assert cache.stat().st_mode & 0o777 == 0o600


def test_strava_reuses_cache_and_newest_refresh_token(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "strava-token.json"
    cache.write_text(
        json.dumps(
            {
                "client_id": "client-1",
                "access_token": "cached-access",
                "refresh_token": "cached-refresh",
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "xzq.collector.strava.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应刷新 token")),
    )

    client = StravaClient(
        client_id="client-1",
        client_secret="secret",
        refresh_token="stale-env-refresh",
        token_cache=cache,
    )

    assert client.refresh_token == "cached-refresh"
    assert client._token() == "cached-access"


def test_strava_ignores_cache_from_another_client(tmp_path: Path) -> None:
    cache = tmp_path / "strava-token.json"
    cache.write_text(
        json.dumps(
            {
                "client_id": "another-client",
                "access_token": "wrong-access",
                "refresh_token": "wrong-refresh",
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )

    client = StravaClient(
        client_id="client-1",
        client_secret="secret",
        refresh_token="right-refresh",
        token_cache=cache,
    )

    assert client.refresh_token == "right-refresh"
    assert client._access_token == ""


def test_strava_ignores_invalid_cached_expiry(tmp_path: Path) -> None:
    cache = tmp_path / "strava-token.json"
    cache.write_text(
        json.dumps(
            {
                "client_id": "client-1",
                "access_token": "cached-access",
                "refresh_token": "cached-refresh",
                "expires_at": "broken",
            }
        ),
        encoding="utf-8",
    )

    client = StravaClient(
        client_id="client-1",
        client_secret="secret",
        refresh_token="fallback-refresh",
        token_cache=cache,
    )

    assert client.refresh_token == "cached-refresh"
    assert client._access_token == "cached-access"
    assert client._expires_at == 0.0
