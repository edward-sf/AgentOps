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


RawContent = tuple[dict[str, Any], ...]
"""Assistant content blocks exactly as the API returned them, in order (M2.T11)."""

THINKING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking"})


@dataclass(frozen=True)
class AssistantTurn:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    raw_content: RawContent = ()
    """When set, the adapter sends these blocks back unchanged instead of rebuilding them from
    `text` and `tool_calls`. Thinking blocks must be replayed as received, signature included,
    in their original position, so never edit or reorder them."""


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
    raw_content: RawContent = ()
    """Every content block, thinking included, as returned (see `AssistantTurn.raw_content`)."""

    @property
    def thinking_blocks(self) -> int:
        """How many `thinking` and `redacted_thinking` blocks the response had."""
        return sum(1 for block in self.raw_content if block.get("type") in THINKING_BLOCK_TYPES)


class Provider(Protocol):
    name: str

    async def complete(self, request: ModelRequest) -> ModelResponse: ...


class ProviderError(Exception):
    """Base class for provider failures."""


class ProviderResponseError(ProviderError):
    """The response had a missing or ill-typed field, or an unknown content-block type."""


ProviderFailureKind = Literal["account", "unavailable"]
"""Why a provider call failed, when it was the provider's fault (M2 D10).

- `account`: authentication, permission or billing. Every later call would fail the same way.
- `unavailable`: a rate limit, overload, server, connection or timeout error that outlasted
  the adapter's retries.
"""


class ProviderAPIError(ProviderError):
    """The API call failed, after any retries.

    `kind` is set when the provider, not the agent, is at fault (M2 D10). `None` means the
    request itself was rejected (for example a 400 or 404), which counts as an agent failure.
    """

    def __init__(self, message: str, *, kind: ProviderFailureKind | None = None) -> None:
        super().__init__(message)
        self.kind: ProviderFailureKind | None = kind
