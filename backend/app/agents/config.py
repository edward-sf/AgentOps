"""Turn an AgentVersion into a validated run configuration (spec D5, D8)."""

import uuid
from dataclasses import dataclass
from typing import Any

from app.agents.tools import Tool, get_tools
from app.models import AgentVersion

ALLOWED_PARAMETERS = frozenset({"max_steps", "max_tokens"})
"""Exactly these keys (D8): a typo must never silently change behaviour."""
DEFAULT_MAX_STEPS = 10
DEFAULT_MAX_TOKENS = 1024
"""The Messages API requires `max_tokens`."""


class InvalidParametersError(ValueError):
    """An AgentVersion's `parameters` can't be run."""


@dataclass(frozen=True)
class RunParams:
    max_steps: int
    """How many model calls a run may make (D8)."""
    model_params: dict[str, Any]
    """What goes to the provider: never `max_steps`."""


def _positive_int(parameters: dict[str, Any], key: str, default: int) -> int:
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidParametersError(f"{key} must be a positive integer (got {value!r})")
    return value


def validate_parameters(parameters: dict[str, Any]) -> RunParams:
    unknown = sorted(set(parameters) - ALLOWED_PARAMETERS)
    if unknown:
        raise InvalidParametersError(
            f"unsupported parameter(s): {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(ALLOWED_PARAMETERS))}"
        )
    return RunParams(
        max_steps=_positive_int(parameters, "max_steps", DEFAULT_MAX_STEPS),
        model_params={"max_tokens": _positive_int(parameters, "max_tokens", DEFAULT_MAX_TOKENS)},
    )


@dataclass(frozen=True)
class AgentConfig:
    agent_version_id: uuid.UUID
    provider: str
    model: str
    system_prompt: str
    params: RunParams
    tools: list[Tool]

    @classmethod
    def from_version(cls, version: AgentVersion) -> "AgentConfig":
        """Raises `InvalidParametersError` or `UnknownToolError`."""
        return cls(
            agent_version_id=version.id,
            provider=version.provider,
            model=version.model,
            system_prompt=version.system_prompt,
            params=validate_parameters(dict(version.parameters)),
            tools=get_tools(list(version.tools)),
        )
