"""Record the replayable Anthropic fixtures from the real API (spec D3, D11).

Run by the author only, through `make record`. It costs money and needs `ANTHROPIC_API_KEY`;
no test, `make check` step or CI job runs it. It uses the seeded `weather-bot` version 1 prompt,
model and `get_weather` tool, inline, so it needs no database.
"""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import anthropic

from app.agents.providers.anthropic import (
    describe_api_error,
    make_anthropic_client,
    parse_message,
    to_anthropic_kwargs,
)
from app.agents.providers.base import (
    AssistantTurn,
    ModelRequest,
    ModelResponse,
    ToolResult,
    ToolResultsTurn,
    ToolSpec,
    Turn,
    UserTurn,
)
from app.agents.tools import TOOLS
from app.config import Settings, get_settings

RECORDABLE_SCENARIOS = (
    "tool_use",
    "final_answer",
    "direct_answer",
    "max_tokens",
    "thinking_after_tool",
    "thinking_answer",
)
"""Scenarios that `make record` writes. They must end up `"source": "recorded"`."""

CONSTRUCTED_SCENARIOS = (
    "refusal",
    "unknown_block",
    "unknown_stop_reason",
    "overloaded",
    "thinking_tool_use",
)
"""Scenarios that can't be triggered reliably, so they stay hand-written from the SDK types.

`thinking_tool_use` (a thinking block before a tool call) is possible but was never seen in
the M2 evaluation run, so it is constructed rather than recorded."""

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "agents" / "fixtures" / "anthropic"

MODEL = "claude-sonnet-5-5"
SYSTEM_PROMPT = "You are a helpful assistant. Use the get_weather tool to answer weather questions."
PARAMS: dict[str, Any] = {"max_tokens": 1024}
"""The seeded `weather-bot` version 1 parameters."""

WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Get the current weather for a location.",
    input_schema={
        "type": "object",
        "properties": {"location": {"type": "string", "description": "City name, e.g. Seattle"}},
        "required": ["location"],
    },
)
WEATHER_RESULT = json.dumps(
    {"location": "Seattle", "temperature_c": 12, "conditions": "light rain"}
)

SEATTLE = "Find the weather in Seattle"
FRANCE = "What is the capital of France?"

# The thinking scenarios use weather-bot's real request shape, as `make eval` sends it: the
# seeded system prompt and parameters with the registry's `get_weather` tool. Their questions
# are the sample dataset cases whose M2 evaluation traces had thinking blocks (run 648a1255).
WEATHER_BOT_TOOL = TOOLS["get_weather"]
THINKING_AFTER_TOOL_QUESTION = "What's the temperature in London in Fahrenheit?"
"""case_006: `tool_use` without thinking, then a thinking block on the answer after the tool."""
THINKING_ANSWER_QUESTION = "What's the weather like in Narnia today?"
"""case_015: a thinking block on a first call that answers without a tool."""
REPLAY_FOLLOW_UP = "Thanks."

THINKING_ATTEMPTS = 5
"""Adaptive thinking decides per request whether to think, so each thinking scenario is tried
up to this many times."""
MAX_REQUESTS = 4 + 2 * THINKING_ATTEMPTS + THINKING_ATTEMPTS + 1
"""Worst case for one `make record`: the four plain scenarios, two calls per
`thinking_after_tool` attempt, one per `thinking_answer` attempt, and the replay check."""
RERUN = "nothing was written; it is safe to rerun 'make record'"


def _request(turns: list[Turn], **params: Any) -> ModelRequest:
    return ModelRequest(
        model=MODEL,
        system=SYSTEM_PROMPT,
        turns=turns,
        tools=[WEATHER_TOOL],
        params={**PARAMS, **params},
    )


def _weather_bot_request(turns: list[Turn]) -> ModelRequest:
    return ModelRequest(
        model=MODEL,
        system=SYSTEM_PROMPT,
        turns=turns,
        tools=[WEATHER_BOT_TOOL.spec],
        params=dict(PARAMS),
    )


class UnexpectedResponse(Exception):
    """`make record` stops and writes nothing."""


class FailedAttempt(Exception):
    """One attempt at a thinking scenario gave an unusable response; it is retried."""


async def _call(
    client: anthropic.AsyncAnthropic, scenario: str, request: ModelRequest, expected_stop: str
) -> tuple[dict[str, Any], ModelResponse]:
    raw = await client.messages.with_raw_response.create(**to_anthropic_kwargs(request))
    body: dict[str, Any] = raw.http_response.json()
    response = parse_message(await raw.parse())
    if response.stop_reason != expected_stop:
        raise UnexpectedResponse(
            f"{scenario}: expected stop_reason {expected_stop!r}, got {response.stop_reason!r}; "
            f"{RERUN}"
        )
    return body, response


async def _attempt(
    client: anthropic.AsyncAnthropic,
    request: ModelRequest,
    expected_stop: str,
    *,
    thinking: bool,
) -> tuple[dict[str, Any], ModelResponse]:
    """One call of a thinking scenario. Raises `FailedAttempt` for an unusable response."""
    raw = await client.messages.with_raw_response.create(**to_anthropic_kwargs(request))
    body: dict[str, Any] = raw.http_response.json()
    response = parse_message(await raw.parse())
    if response.stop_reason != expected_stop:
        raise FailedAttempt(f"expected stop_reason {expected_stop!r}, got {response.stop_reason!r}")
    if thinking and not response.thinking_blocks:
        raise FailedAttempt("no thinking block")
    return body, response


async def _with_retries(
    scenario: str, attempt: Callable[[], Awaitable[tuple[dict[str, Any], ModelResponse]]]
) -> tuple[dict[str, Any], ModelResponse]:
    for number in range(1, THINKING_ATTEMPTS + 1):
        try:
            return await attempt()
        except FailedAttempt as exc:
            print(f"{scenario}: attempt {number} of {THINKING_ATTEMPTS}: {exc}", file=sys.stderr)
    raise UnexpectedResponse(
        f"{scenario}: no usable response in {THINKING_ATTEMPTS} attempts; {RERUN}"
    )


async def _thinking_after_tool(
    client: anthropic.AsyncAnthropic,
) -> tuple[dict[str, Any], ModelResponse]:
    """Call 1 must call the tool; call 2, the answer, must think. Call 2 is the fixture."""
    question: list[Turn] = [UserTurn(text=THINKING_AFTER_TOOL_QUESTION)]
    _, call_1 = await _attempt(client, _weather_bot_request(question), "tool_use", thinking=False)
    results: list[ToolResult] = []
    for call in call_1.tool_calls:
        if call.name != WEATHER_BOT_TOOL.spec.name:
            raise FailedAttempt(f"model called unknown tool {call.name!r}")
        try:
            content = await WEATHER_BOT_TOOL.func(call.arguments)
        except ValueError as exc:
            raise FailedAttempt(f"get_weather failed: {exc}") from exc
        results.append(ToolResult(call.id, content))
    turns: list[Turn] = [
        *question,
        # Sent back verbatim: the real-API check that a tool loop's history replays.
        AssistantTurn(
            text=call_1.text, tool_calls=call_1.tool_calls, raw_content=call_1.raw_content
        ),
        ToolResultsTurn(results=tuple(results)),
    ]
    return await _attempt(client, _weather_bot_request(turns), "end_turn", thinking=True)


async def _thinking_answer(
    client: anthropic.AsyncAnthropic,
) -> tuple[dict[str, Any], ModelResponse]:
    question: list[Turn] = [UserTurn(text=THINKING_ANSWER_QUESTION)]
    return await _attempt(client, _weather_bot_request(question), "end_turn", thinking=True)


def _follow_up(question: list[Turn], tool_use: ModelResponse) -> list[Turn]:
    """The tool-use turn replayed unchanged, thinking included, then the weather result."""
    return [
        *question,
        AssistantTurn(
            text=tool_use.text, tool_calls=tool_use.tool_calls, raw_content=tool_use.raw_content
        ),
        ToolResultsTurn(
            results=tuple(ToolResult(call.id, WEATHER_RESULT) for call in tool_use.tool_calls)
        ),
    ]


async def _record_all(client: anthropic.AsyncAnthropic) -> dict[str, dict[str, Any]]:
    bodies: dict[str, dict[str, Any]] = {}

    seattle: list[Turn] = [UserTurn(text=SEATTLE)]
    bodies["tool_use"], tool_use = await _call(client, "tool_use", _request(seattle), "tool_use")

    bodies["final_answer"], _ = await _call(
        client, "final_answer", _request(_follow_up(seattle, tool_use)), "end_turn"
    )
    bodies["direct_answer"], _ = await _call(
        client, "direct_answer", _request([UserTurn(text=FRANCE)]), "end_turn"
    )
    bodies["max_tokens"], _ = await _call(
        client, "max_tokens", _request(seattle, max_tokens=5), "max_tokens"
    )

    bodies["thinking_after_tool"], _ = await _with_retries(
        "thinking_after_tool", lambda: _thinking_after_tool(client)
    )
    bodies["thinking_answer"], answer = await _with_retries(
        "thinking_answer", lambda: _thinking_answer(client)
    )
    # Not saved: proves the API accepts a thinking turn sent back unchanged (a 400 otherwise).
    replay: list[Turn] = [
        UserTurn(text=THINKING_ANSWER_QUESTION),
        AssistantTurn(text=answer.text, tool_calls=(), raw_content=answer.raw_content),
        UserTurn(text=REPLAY_FOLLOW_UP),
    ]
    await _call(client, "thinking replay", _weather_bot_request(replay), "end_turn")
    return bodies


async def run(
    *,
    settings: Settings | None = None,
    client: anthropic.AsyncAnthropic | None = None,
    out_dir: Path = FIXTURES_DIR,
) -> int:
    """Record every recordable scenario, then write all of them. Returns the exit code."""
    if client is None:
        secret = (settings or get_settings()).anthropic_api_key
        api_key = secret.get_secret_value() if secret else ""
        if not api_key:
            print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
            return 2
        client = make_anthropic_client(api_key)

    try:
        bodies = await _record_all(client)
    except UnexpectedResponse as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except anthropic.APIError as exc:
        # One line, no traceback; nothing has been written yet.
        print(f"record: anthropic API error: {describe_api_error(exc)}", file=sys.stderr)
        return 1

    recorded_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sdk_version = version("anthropic")
    for scenario in RECORDABLE_SCENARIOS:
        fixture = {
            "scenario": scenario,
            "source": "recorded",
            "sdk_version": sdk_version,
            "model": MODEL,
            "recorded_at": recorded_at,
            "status_code": 200,
            "body": bodies[scenario],
        }
        path = out_dir / f"{scenario}.json"
        path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
