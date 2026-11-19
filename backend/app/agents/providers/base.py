"""Provider-neutral model interface. The runtime speaks only these types (spec D5, D6)."""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

StopReason = Literal["tool_use", "end_turn", "stop_sequence", "refusal", "max_tokens"]
"""The stop reasons the runtime knows (D7). `ModelResponse.stop_reason` may hold any other."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UserTurn:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    text: str | None
    tool_calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class ToolResultsTurn:
    results: tuple[ToolResult, ...]


Turn = UserTurn | AssistantTurn | ToolResultsTurn


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    turns: list[Turn]
    tools: list[ToolSpec]
    params: dict[str, Any]
    """SDK parameters only. `max_steps` has already been removed."""


@dataclass(frozen=True)
class ModelResponse:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    """A plain string: the runtime decides what an unknown value means (D7)."""
    response_model: str
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    name: str

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class ProviderError(Exception):
    """Base class for provider failures."""


class ProviderResponseError(ProviderError):
    """The response had a missing or ill-typed field, or an unknown content-block type."""


class ProviderAPIError(ProviderError):
    """The API call failed, after any retries."""
