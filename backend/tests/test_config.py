"""`Settings`: the Anthropic key stays out of reprs and dumps; the evaluation settings."""

import pytest
from pydantic import ValidationError

from app.config import Settings

FAKE_KEY = "sk-ant-fake-0123456789abcdef"


def test_settings_masks_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == FAKE_KEY
    for text in (repr(settings), str(settings), settings.model_dump_json()):
        assert FAKE_KEY not in text


def test_database_urls_stay_plain_strings() -> None:
    """They go to SQLAlchemy and Alembic as-is; SQLAlchemy masks passwords in `URL` repr."""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert isinstance(settings.database_url, str)
    assert isinstance(settings.test_database_url, str)


def test_eval_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_CONCURRENCY", raising=False)
    monkeypatch.delenv("EVAL_STALE_AFTER_SECONDS", raising=False)
    monkeypatch.delenv("EVAL_POLL_INTERVAL_SECONDS", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.eval_concurrency == 4
    assert settings.eval_stale_after_seconds == 60
    assert settings.eval_poll_interval_seconds == 1.0


def test_eval_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_CONCURRENCY", "2")
    monkeypatch.setenv("EVAL_STALE_AFTER_SECONDS", "30")
    monkeypatch.setenv("EVAL_POLL_INTERVAL_SECONDS", "0.5")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.eval_concurrency == 2
    assert settings.eval_stale_after_seconds == 30
    assert settings.eval_poll_interval_seconds == 0.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EVAL_CONCURRENCY", "0"),
        ("EVAL_STALE_AFTER_SECONDS", "9"),
        ("EVAL_POLL_INTERVAL_SECONDS", "0"),
    ],
)
def test_eval_settings_bounds(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
