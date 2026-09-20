from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from xzq.publisher.token import TokenManager, WeChatTokenError


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


def _manager(tmp_path: Path, monkeypatch, app_id: str = "wx-app") -> TokenManager:
    monkeypatch.setenv("WECHAT_APP_ID", app_id)
    monkeypatch.setenv("WECHAT_APP_SECRET", "secret")
    return TokenManager(str(tmp_path / "wechat-token.json"))


def test_wechat_cache_is_bound_to_current_app_id(tmp_path: Path, monkeypatch) -> None:
    cache = tmp_path / "wechat-token.json"
    cache.write_text(
        json.dumps(
            {
                "app_id": "another-app",
                "access_token": "wrong-token",
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "xzq.publisher.token.requests.get",
        lambda *args, **kwargs: _Response(
            {"access_token": "right-token", "expires_in": 7200}
        ),
    )

    manager = _manager(tmp_path, monkeypatch)

    assert manager.get_token() == "right-token"
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["app_id"] == "wx-app"
    assert cache.stat().st_mode & 0o777 == 0o600


def test_wechat_cache_without_access_token_refreshes(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "wechat-token.json"
    cache.write_text(
        json.dumps(
            {
                "app_id": "wx-app",
                "expires_at": time.time() + 3600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "xzq.publisher.token.requests.get",
        lambda *args, **kwargs: _Response(
            {"access_token": "fresh-token", "expires_in": 7200}
        ),
    )

    assert _manager(tmp_path, monkeypatch).get_token() == "fresh-token"


@pytest.mark.parametrize(
    ("errcode", "expected"),
    [
        (40013, "AppID 无效"),
        (40125, "AppSecret 无效"),
        (40164, "出口 IP 不在公众号白名单"),
        (99999, "核对公众号凭证、接口权限和平台状态"),
    ],
)
def test_wechat_token_error_has_actionable_hint(
    tmp_path: Path, monkeypatch, errcode: int, expected: str
) -> None:
    monkeypatch.setattr(
        "xzq.publisher.token.requests.get",
        lambda *args, **kwargs: _Response({"errcode": errcode, "errmsg": "failed"}),
    )
    manager = _manager(tmp_path, monkeypatch)

    with pytest.raises(WeChatTokenError, match=expected):
        manager.get_token()
