"""`Settings` keeps the Anthropic key out of reprs and dumps."""

import pytest

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
