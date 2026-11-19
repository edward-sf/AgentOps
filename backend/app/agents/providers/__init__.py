"""Model providers, and the factory that picks one for an AgentVersion (spec D5, 6.2)."""

from app.agents.providers.anthropic import AnthropicProvider, make_anthropic_client
from app.agents.providers.base import Provider
from app.config import Settings


class UnsupportedProviderError(ValueError):
    """An AgentVersion names a provider that M1 can't run (only `anthropic`)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"unsupported provider: {name}")
        self.name = name


class ProviderNotConfiguredError(RuntimeError):
    """The provider is supported, but its credentials aren't configured."""


def provider_for(name: str, settings: Settings) -> Provider:
    """The provider for `name`. Raises `UnsupportedProviderError` or
    `ProviderNotConfiguredError`."""
    if name != "anthropic":
        raise UnsupportedProviderError(name)
    if not settings.anthropic_api_key:
        raise ProviderNotConfiguredError("ANTHROPIC_API_KEY is not set")
    return AnthropicProvider(make_anthropic_client(settings.anthropic_api_key))
