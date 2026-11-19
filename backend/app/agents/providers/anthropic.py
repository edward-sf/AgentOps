"""Anthropic adapter on the plain SDK (spec D6).

- Responses are parsed strictly: only `text` and `tool_use` blocks are understood (D3).
- The SDK's own retries and OTel spans are off. Transient errors are retried here, inside the
  caller's current span, and each failed attempt is recorded on it (D9).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import anthropic
import httpx2
from anthropic.types import Message
from opentelemetry import trace

from app.agents.providers.base import (
    AssistantTurn,
    ModelRequest,
    ModelResponse,
    ProviderAPIError,
    ProviderResponseError,
    ToolCall,
    ToolResultsTurn,
    Turn,
    UserTurn,
)

DEFAULT_MAX_TOKENS = 1024
"""Used when the version's parameters set no `max_tokens`; the Messages API requires one."""

RETRY_EVENT = "gen_ai.retry"
ATTEMPTS_ATTRIBUTE = "agentops.llm.attempts"


def make_anthropic_client(
    api_key: str, http_client: httpx2.AsyncClient | None = None
) -> anthropic.AsyncAnthropic:
    """The only way to build a client: no SDK retries, and no SDK spans."""
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        max_retries=0,
        open_telemetry=False,
        http_client=http_client,
    )


def _turn_to_message(turn: Turn) -> dict[str, Any]:
    if isinstance(turn, UserTurn):
        return {"role": "user", "content": turn.text}
    if isinstance(turn, AssistantTurn):
        content: list[dict[str, Any]] = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        content.extend(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            for call in turn.tool_calls
        )
        return {"role": "assistant", "content": content}
    if isinstance(turn, ToolResultsTurn):
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": result.tool_call_id,
                    "content": result.content,
                    "is_error": result.is_error,
                }
                for result in turn.results
            ],
        }
    raise TypeError(f"unknown turn type: {type(turn).__name__}")


SDK_PARAMS = frozenset({"max_tokens"})
"""The only `ModelRequest.params` keys allowed (D8).

anthropic 2.11 / API version 2026-10-21 no longer accepts `temperature`, `top_p` or `top_k`.
"""


def to_anthropic_kwargs(request: ModelRequest) -> dict[str, Any]:
    """Map a `ModelRequest` to `messages.create` keyword arguments.

    Raises `ValueError` for a parameter other than `max_tokens`: that is a programming error,
    because the runtime validates version parameters before building a request.
    """
    unsupported = sorted(set(request.params) - SDK_PARAMS)
    if unsupported:
        raise ValueError(f"unsupported model parameter(s): {', '.join(unsupported)}")
    kwargs: dict[str, Any] = {"max_tokens": DEFAULT_MAX_TOKENS, **request.params}
    kwargs["model"] = request.model
    kwargs["messages"] = [_turn_to_message(turn) for turn in request.turns]
    if request.system:
        kwargs["system"] = request.system
    if request.tools:
        kwargs["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in request.tools
        ]
    return kwargs


def _field(obj: object, name: str, expected: type | tuple[type, ...], where: str) -> Any:
    value = getattr(obj, name, None)
    if not isinstance(value, expected) or (isinstance(value, bool) and expected is int):
        raise ProviderResponseError(
            f"anthropic response: {where}.{name} is missing or not "
            f"{getattr(expected, '__name__', expected)} (got {value!r})"
        )
    return value


def parse_message(message: Message) -> ModelResponse:
    """Turn an SDK `Message` into a `ModelResponse`, or raise `ProviderResponseError`.

    The SDK does not validate responses, so every field used here is checked.
    """
    content = _field(message, "content", list, "message")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for i, block in enumerate(content):
        where = f"content[{i}]"
        block_type = getattr(block, "type", None)
        if block_type == "text":
            texts.append(_field(block, "text", str, where))
        elif block_type == "tool_use":
            calls.append(
                ToolCall(
                    id=_field(block, "id", str, where),
                    name=_field(block, "name", str, where),
                    arguments=_field(block, "input", dict, where),
                )
            )
        else:
            raise ProviderResponseError(
                f"anthropic response: unknown content block type {block_type!r} in {where}"
            )
    usage = getattr(message, "usage", None)
    return ModelResponse(
        text="".join(texts) if texts else None,
        tool_calls=tuple(calls),
        stop_reason=_field(message, "stop_reason", str, "message"),
        response_model=_field(message, "model", str, "message"),
        input_tokens=_field(usage, "input_tokens", int, "usage"),
        output_tokens=_field(usage, "output_tokens", int, "usage"),
    )


def _error_type(exc: anthropic.APIError) -> str:
    """The API's error type (e.g. `overloaded_error`), else the exception's class name."""
    body = exc.body
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("type"), str):
            return str(error["type"])
    return type(exc).__name__


def _is_transient(exc: anthropic.APIError) -> bool:
    if isinstance(exc, anthropic.RateLimitError | anthropic.InternalServerError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500 or _error_type(exc) == "overloaded_error"
    return False


def _describe(exc: anthropic.APIError) -> str:
    status = f" (status {exc.status_code})" if isinstance(exc, anthropic.APIStatusError) else ""
    return f"{_error_type(exc)}{status}: {exc.message}"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if client.max_retries != 0:
            raise ValueError("build the client with make_anthropic_client (max_retries=0)")
        self._client = client
        self._max_attempts = max_attempts
        self._sleep = sleep

    async def complete(self, request: ModelRequest) -> ModelResponse:
        span = trace.get_current_span()
        kwargs = to_anthropic_kwargs(request)
        attempt = 0
        while True:
            attempt += 1
            try:
                message = await self._client.messages.create(**kwargs)
            except anthropic.APIError as exc:
                transient = _is_transient(exc)
                if transient:
                    span.add_event(
                        RETRY_EVENT,
                        {
                            "error.type": _error_type(exc),
                            "error.message": _describe(exc),
                            "agentops.llm.attempt": attempt,
                        },
                    )
                if not transient or attempt >= self._max_attempts:
                    span.set_attribute(ATTEMPTS_ATTRIBUTE, attempt)
                    raise ProviderAPIError(
                        f"anthropic API error after {attempt} attempt"
                        f"{'s' if attempt > 1 else ''}: {_describe(exc)}"
                    ) from exc
                await self._sleep(0.5 * 2 ** (attempt - 1))
                continue
            span.set_attribute(ATTEMPTS_ATTRIBUTE, attempt)
            return parse_message(message)
