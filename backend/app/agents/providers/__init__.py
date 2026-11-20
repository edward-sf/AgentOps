"""Model providers, and the factory that picks one for an AgentVersion (spec D5, 6.2)."""

from app.agents.providers.anthropic import AnthropicProvider, make_anthropic_client
from app.agents.providers.base import Provider
from app.config import Settings

SUPPORTED_PROVIDERS = ("anthropic",)
"""Provider names an AgentVersion may use. The API checks this at submit time without
building a provider; `provider_for` checks it too."""


class UnsupportedProviderError(ValueError):
    """An AgentVersion names a provider that can't be run (see `SUPPORTED_PROVIDERS`)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unsupported provider: {name}")
        self.name = name


class ProviderNotConfiguredError(RuntimeError):
    """The provider is supported, but its credentials aren't configured."""


def provider_for(name: str, settings: Settings) -> Provider:
    """The provider for `name`. Raises `UnsupportedProviderError` or
    `ProviderNotConfiguredError`."""
    if name not in SUPPORTED_PROVIDERS:
        raise UnsupportedProviderError(name)
    api_key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else ""
    if not api_key:
        raise ProviderNotConfiguredError("ANTHROPIC_API_KEY is not set")
    return AnthropicProvider(make_anthropic_client(api_key))
