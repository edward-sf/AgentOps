"""Record the replayable Anthropic fixtures from the real API (spec D3, D11).

Run by the author only, through `make record`. It costs money and needs `ANTHROPIC_API_KEY`;
no test, `make check` step or CI job runs it. It uses the seeded `weather-bot` version 1 prompt,
model and `get_weather` tool, inline, so it needs no database.
"""

import asyncio
import json
import sys
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
from app.config import Settings, get_settings

RECORDABLE_SCENARIOS = ("tool_use", "final_answer", "direct_answer", "max_tokens")
"""Scenarios that `make record` writes. They must end up `"source": "recorded"`."""

CONSTRUCTED_SCENARIOS = ("refusal", "unknown_block", "unknown_stop_reason", "overloaded")
"""Scenarios that can't be triggered reliably, so they stay hand-written from the SDK types."""

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


def _request(turns: list[Turn], **params: Any) -> ModelRequest:
    return ModelRequest(
        model=MODEL,
        system=SYSTEM_PROMPT,
        turns=turns,
        tools=[WEATHER_TOOL],
        params={**PARAMS, **params},
    )


class UnexpectedResponse(Exception):
    pass


async def _call(
    client: anthropic.AsyncAnthropic, scenario: str, request: ModelRequest, expected_stop: str
) -> tuple[dict[str, Any], ModelResponse]:
    raw = await client.messages.with_raw_response.create(**to_anthropic_kwargs(request))
    body: dict[str, Any] = raw.http_response.json()
    response = parse_message(await raw.parse())
    if response.stop_reason != expected_stop:
        raise UnexpectedResponse(
            f"{scenario}: expected stop_reason {expected_stop!r}, got {response.stop_reason!r}; "
            "nothing was written, try again"
        )
    return body, response


async def _record_all(client: anthropic.AsyncAnthropic) -> dict[str, dict[str, Any]]:
    bodies: dict[str, dict[str, Any]] = {}

    seattle: list[Turn] = [UserTurn(text=SEATTLE)]
    bodies["tool_use"], tool_use = await _call(client, "tool_use", _request(seattle), "tool_use")

    follow_up: list[Turn] = [
        *seattle,
        AssistantTurn(text=tool_use.text, tool_calls=tool_use.tool_calls),
        ToolResultsTurn(
            results=tuple(ToolResult(call.id, WEATHER_RESULT) for call in tool_use.tool_calls)
        ),
    ]
    bodies["final_answer"], _ = await _call(client, "final_answer", _request(follow_up), "end_turn")
    bodies["direct_answer"], _ = await _call(
        client, "direct_answer", _request([UserTurn(text=FRANCE)]), "end_turn"
    )
    bodies["max_tokens"], _ = await _call(
        client, "max_tokens", _request(seattle, max_tokens=5), "max_tokens"
    )
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
