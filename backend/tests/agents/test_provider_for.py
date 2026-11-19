"""`provider_for` treats a missing or empty `ANTHROPIC_API_KEY` as not configured."""

import pytest

from app.agents.providers import ProviderNotConfiguredError, provider_for
from app.config import Settings


def test_empty_env_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ANTHROPIC_API_KEY=` in `.env` (as in `.env.example`) loads as "" and counts as unset."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.anthropic_api_key == ""
    with pytest.raises(ProviderNotConfiguredError, match="ANTHROPIC_API_KEY is not set"):
        provider_for("anthropic", settings)


def test_missing_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ProviderNotConfiguredError, match="ANTHROPIC_API_KEY is not set"):
        provider_for("anthropic", settings)
