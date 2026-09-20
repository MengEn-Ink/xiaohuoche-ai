from __future__ import annotations

from types import SimpleNamespace

import pytest

import pipeline


class _ConfiguredLLM:
    def is_configured(self) -> bool:
        return False


class _UnconfiguredTokenManager:
    def __init__(self, _cache: str) -> None:
        pass

    def is_configured(self) -> bool:
        return False


class _AuthFailure(Exception):
    def __init__(self, status_code: int | None = None) -> None:
        response = None if status_code is None else SimpleNamespace(status_code=status_code)
        self.response = response
        super().__init__("sensitive upstream response must not be printed")


def _doctor_ctx():
    config = SimpleNamespace(
        get=lambda *keys, default=None: default,
    )
    return config, object(), object(), _ConfiguredLLM()


def test_doctor_stops_strava_checks_after_refresh_token_failure(monkeypatch, capsys) -> None:
    calls: list[str] = []

    class FakeStrava:
        def __init__(self, **_kwargs) -> None:
            pass

        def is_configured(self) -> bool:
            return True

        def athlete_me(self):
            calls.append("athlete_me")
            raise _AuthFailure(401)

        def club_detail(self):
            calls.append("club_detail")

        def athlete_stats_material(self):
            calls.append("athlete_stats_material")

        def club_activities(self, **_kwargs):
            calls.append("club_activities")

        def recent_activities(self, **_kwargs):
            calls.append("recent_activities")

    monkeypatch.setattr(pipeline, "_ctx", lambda _args: _doctor_ctx())
    monkeypatch.setattr(pipeline, "StravaClient", FakeStrava)
    monkeypatch.setattr(pipeline, "TokenManager", _UnconfiguredTokenManager)

    with pytest.raises(RuntimeError, match="Strava/账号/refresh_token"):
        pipeline.cmd_doctor(SimpleNamespace())

    output = capsys.readouterr().out
    assert calls == ["athlete_me"]
    assert "refresh_token 已失效或与 Client 不匹配" in output
    assert output.count("[skip]") == 4
    assert "sensitive upstream response" not in output


def test_strava_check_failure_hints_are_actionable() -> None:
    assert "activity:read_all" in pipeline._strava_check_failure_hint(
        "本人活动/PR scope", _AuthFailure(401)
    )
    assert "已加入俱乐部" in pipeline._strava_check_failure_hint(
        "私密 Club 活动", _AuthFailure(404)
    )
    assert "网络、DNS 或代理" in pipeline._strava_check_failure_hint(
        "本人骑行统计", _AuthFailure()
    )


def test_doctor_reports_network_hint_without_leaking_exception(monkeypatch, capsys) -> None:
    class FakeStrava:
        def __init__(self, **_kwargs) -> None:
            pass

        def is_configured(self) -> bool:
            return True

        def athlete_me(self):
            raise _AuthFailure()

    monkeypatch.setattr(pipeline, "_ctx", lambda _args: _doctor_ctx())
    monkeypatch.setattr(pipeline, "StravaClient", FakeStrava)
    monkeypatch.setattr(pipeline, "TokenManager", _UnconfiguredTokenManager)

    with pytest.raises(RuntimeError):
        pipeline.cmd_doctor(SimpleNamespace())

    output = capsys.readouterr().out
    assert "请检查本机网络、DNS 或代理后重试" in output
    assert "sensitive upstream response" not in output


def test_strava_auth_failure_hints_cover_status_boundaries() -> None:
    assert "refresh_token 已失效" in pipeline._strava_failure_hint(_AuthFailure(400))
    assert "账号限制、凭证不匹配或 scope 不足" in pipeline._strava_failure_hint(
        _AuthFailure(403)
    )
    assert "网络、DNS 或代理" in pipeline._strava_failure_hint(_AuthFailure())
    assert "HTTP 500" in pipeline._strava_failure_hint(_AuthFailure(500))


def test_doctor_runs_downstream_checks_after_authentication(monkeypatch, capsys) -> None:
    class FakeStrava:
        def __init__(self, **_kwargs) -> None:
            pass

        def is_configured(self) -> bool:
            return True

        def athlete_me(self):
            return {"id": 1}

        def club_detail(self):
            return {"id": 780580}

        def athlete_stats_material(self):
            return object()

        def club_activities(self, **_kwargs):
            return []

        def recent_activities(self, **_kwargs):
            return []

    monkeypatch.setattr(pipeline, "_ctx", lambda _args: _doctor_ctx())
    monkeypatch.setattr(pipeline, "StravaClient", FakeStrava)
    monkeypatch.setattr(pipeline, "TokenManager", _UnconfiguredTokenManager)

    pipeline.cmd_doctor(SimpleNamespace())

    output = capsys.readouterr().out
    assert output.count("[ok]") == 5
    assert "[fail]" not in output


def test_doctor_reports_downstream_scope_failure(monkeypatch, capsys) -> None:
    class FakeStrava:
        def __init__(self, **_kwargs) -> None:
            pass

        def is_configured(self) -> bool:
            return True

        def athlete_me(self):
            return {"id": 1}

        def club_detail(self):
            return {"id": 780580}

        def athlete_stats_material(self):
            return object()

        def club_activities(self, **_kwargs):
            return []

        def recent_activities(self, **_kwargs):
            raise _AuthFailure(401)

    monkeypatch.setattr(pipeline, "_ctx", lambda _args: _doctor_ctx())
    monkeypatch.setattr(pipeline, "StravaClient", FakeStrava)
    monkeypatch.setattr(pipeline, "TokenManager", _UnconfiguredTokenManager)

    with pytest.raises(RuntimeError, match="Strava/本人活动/PR scope"):
        pipeline.cmd_doctor(SimpleNamespace())

    output = capsys.readouterr().out
    assert "activity:read_all" in output
    assert "sensitive upstream response" not in output
