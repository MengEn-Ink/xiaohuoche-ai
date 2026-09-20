from __future__ import annotations

import os
from pathlib import Path

from xzq.config import _load_local_env


def test_load_local_env_without_optional_dependency(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "export STRAVA_CLIENT_ID=12345\n"
        "STRAVA_CLIENT_SECRET='secret value' # inline comment\n"
        "WECHAT_APP_ID=wx-app\n"
        "BROKEN_QUOTE='ignored\n",
        encoding="utf-8",
    )
    for name in ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "WECHAT_APP_ID"):
        monkeypatch.delenv(name, raising=False)

    _load_local_env(env_file)

    assert os.environ["STRAVA_CLIENT_ID"] == "12345"
    assert os.environ["STRAVA_CLIENT_SECRET"] == "secret value"
    assert os.environ["WECHAT_APP_ID"] == "wx-app"
    assert "BROKEN_QUOTE" not in os.environ


def test_load_local_env_does_not_override_explicit_environment(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("STRAVA_CLIENT_ID=from-file\n", encoding="utf-8")
    monkeypatch.setenv("STRAVA_CLIENT_ID", "from-shell")

    _load_local_env(env_file)

    assert os.environ["STRAVA_CLIENT_ID"] == "from-shell"
