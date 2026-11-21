from collections.abc import Iterator
from typing import Any

import httpx2
import pytest
from anthropic.types import (
    ContentBlockParam,
    RedactedThinkingBlockParam,
    TextBlockParam,
    ThinkingBlockParam,
    ToolUseBlockParam,
)
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import TypeAdapter

from app.agents.providers.anthropic import AnthropicProvider, make_anthropic_client
from app.agents.providers.base import (
    AssistantTurn,
    ModelRequest,
    ModelResponse,
    ProviderAPIError,
    ProviderResponseError,
    ToolCall,
    ToolResult,
    ToolResultsTurn,
    ToolSpec,
    UserTurn,
)
from tests.agents.replay import (
    assert_thinking_before_tool_use,
    blocks,
    expected_text,
    expected_thinking_blocks,
    final_answer_layouts,
    load_fixture,
    replay_client,
    replay_responses,
    thinking_tool_use_layouts,
    wire_form,
)

WEATHER_TOOL = ToolSpec(
    name="get_weather",
    description="Get the current weather for a location.",
    input_schema={
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    },
)


def seattle_request(**params: Any) -> ModelRequest:
    return ModelRequest(
        model="claude-sonnet-5-5",
        system="You are a helpful assistant.",
        turns=[UserTurn(text="Find the weather in Seattle")],
        tools=[WEATHER_TOOL],
        params={"max_tokens": 1024, **params},
    )


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def tracer_provider(exporter: InMemorySpanExporter) -> Iterator[TracerProvider]:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield provider
    provider.shutdown()


async def complete_in_span(
    provider: AnthropicProvider, request: ModelRequest, tracer_provider: TracerProvider
) -> ModelResponse:
    tracer = tracer_provider.get_tracer("test")
    with tracer.start_as_current_span("llm"):
        return await provider.complete(request)


def only_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    spans = exporter.get_finished_spans()
    assert len(spans) == 1, [s.name for s in spans]
    return spans[0]


async def test_tool_use_response_parsed(tracer_provider: TracerProvider) -> None:
    provider = AnthropicProvider(replay_client("tool_use"))
    response = await complete_in_span(provider, seattle_request(), tracer_provider)
    assert response.stop_reason == "tool_use"
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.name == "get_weather"
    assert call.arguments["location"] == "Seattle"
    assert call.id
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert response.response_model


@pytest.mark.parametrize("scenario", ["final_answer", "direct_answer"])
async def test_final_and_direct_answer_parsed(
    scenario: str, tracer_provider: TracerProvider
) -> None:
    provider = AnthropicProvider(replay_client(scenario))
    response = await complete_in_span(provider, seattle_request(), tracer_provider)
    assert response.stop_reason == "end_turn"
    assert response.text
    assert response.tool_calls == ()


async def test_max_tokens_parsed(tracer_provider: TracerProvider) -> None:
    provider = AnthropicProvider(replay_client("max_tokens"))
    response = await complete_in_span(provider, seattle_request(max_tokens=5), tracer_provider)
    assert response.stop_reason == "max_tokens"


async def test_refusal_parsed(tracer_provider: TracerProvider) -> None:
    provider = AnthropicProvider(replay_client("refusal"))
    response = await complete_in_span(provider, seattle_request(), tracer_provider)
    assert response.stop_reason == "refusal"


async def test_unknown_stop_reason_passed_through(tracer_provider: TracerProvider) -> None:
    expected = load_fixture("unknown_stop_reason")["body"]["stop_reason"]
    provider = AnthropicProvider(replay_client("unknown_stop_reason"))
    response = await complete_in_span(provider, seattle_request(), tracer_provider)
    assert response.stop_reason == expected


async def test_unknown_block_raises(tracer_provider: TracerProvider) -> None:
    provider = AnthropicProvider(replay_client("unknown_block"))
    with pytest.raises(ProviderResponseError, match="unknown_block_type"):
        await complete_in_span(provider, seattle_request(), tracer_provider)


def message_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = dict(load_fixture("final_answer")["body"])
    body.update(overrides)
    return body


# --- thinking blocks (M2.T11) ---------------------------------------------------------------

CONTENT_BLOCK_PARAM: TypeAdapter[Any] = TypeAdapter(ContentBlockParam)
PARAM_KEYS = {
    "text": set(TextBlockParam.__annotations__),
    "thinking": set(ThinkingBlockParam.__annotations__),
    "redacted_thinking": set(RedactedThinkingBlockParam.__annotations__),
    "tool_use": set(ToolUseBlockParam.__annotations__),
}
REDACTED = {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix/LafPsn4aDFIT2Xlxh0L5L8rLVyIw"}
THINKING = {
    "type": "thinking",
    "thinking": "",
    "signature": "EqQBCgIYAhIM1gbcDa9GJwZA2b3hGgxBdjrkzLoky3dl1pk",
}


def assert_valid_block_params(blocks: tuple[dict[str, Any], ...]) -> None:
    """Every block is a content-block param the SDK accepts, with no field it doesn't know."""
    for block in blocks:
        CONTENT_BLOCK_PARAM.validate_python(block)
        assert set(block) <= PARAM_KEYS[block["type"]], block


TOOL_USE_LAYOUTS = thinking_tool_use_layouts()
FINAL_LAYOUTS = final_answer_layouts()


@pytest.mark.parametrize("layout", sorted(TOOL_USE_LAYOUTS))
async def test_thinking_then_tool_use_parsed(layout: str, tracer_provider: TracerProvider) -> None:
    body = TOOL_USE_LAYOUTS[layout]
    provider = AnthropicProvider(replay_responses((200, body)))

    response = await complete_in_span(provider, seattle_request(), tracer_provider)

    assert response.stop_reason == "tool_use"
    assert response.tool_calls == tuple(
        ToolCall(id=b["id"], name=b["name"], arguments=b["input"]) for b in blocks(body, "tool_use")
    )
    assert response.text == expected_text(body)
    assert response.raw_content == tuple(wire_form(body["content"]))
    assert response.thinking_blocks == expected_thinking_blocks(body)
    assert_thinking_before_tool_use(response.raw_content)
    assert_valid_block_params(response.raw_content)


async def test_redacted_thinking_parsed(tracer_provider: TracerProvider) -> None:
    text = {"type": "text", "text": "Seattle is rainy."}
    body = message_body(content=[REDACTED, text])
    provider = AnthropicProvider(replay_responses((200, body)))

    response = await complete_in_span(provider, seattle_request(), tracer_provider)

    assert response.text == "Seattle is rainy."
    assert response.raw_content == (REDACTED, text)
    assert response.thinking_blocks == 1
    assert_valid_block_params(response.raw_content)


async def test_thinking_text_never_becomes_response_text(tracer_provider: TracerProvider) -> None:
    # A summarised (not omitted) block, sitting between text and a tool call.
    shown = {**THINKING, "thinking": "I should look this up."}
    tool = {"type": "tool_use", "id": "toolu_9", "name": "get_weather", "input": {"location": "X"}}
    before = {"type": "text", "text": "Checking."}
    body = message_body(content=[before, shown, tool], stop_reason="tool_use")
    provider = AnthropicProvider(replay_responses((200, body)))

    response = await complete_in_span(provider, seattle_request(), tracer_provider)

    assert response.text == "Checking."
    assert response.raw_content == (before, shown, tool)
    assert response.thinking_blocks == 1


@pytest.mark.parametrize(
    ("block", "match"),
    [
        ({"type": "thinking", "thinking": ""}, "signature"),
        ({"type": "thinking", "signature": "sig"}, "thinking"),
        ({"type": "redacted_thinking"}, "data"),
    ],
)
async def test_ill_typed_thinking_block_raises(
    block: dict[str, Any], match: str, tracer_provider: TracerProvider
) -> None:
    provider = AnthropicProvider(replay_responses((200, message_body(content=[block]))))
    with pytest.raises(ProviderResponseError, match=match):
        await complete_in_span(provider, seattle_request(), tracer_provider)


async def test_mystery_block_still_raises(tracer_provider: TracerProvider) -> None:
    body = message_body(content=[THINKING, {"type": "mystery", "data": "?"}])
    provider = AnthropicProvider(replay_responses((200, body)))
    with pytest.raises(ProviderResponseError, match="unknown content block type 'mystery'"):
        await complete_in_span(provider, seattle_request(), tracer_provider)


RECORDED_LAYOUTS = {
    "tool_use": load_fixture("tool_use")["body"],
    "thinking_after_tool": load_fixture("thinking_after_tool")["body"],
    "thinking_answer": load_fixture("thinking_answer")["body"],
    **{f"final_answer-{name}": body for name, body in FINAL_LAYOUTS.items()},
    **{f"thinking_tool_use-{name}": body for name, body in TOOL_USE_LAYOUTS.items()},
}


@pytest.mark.parametrize("layout", sorted(RECORDED_LAYOUTS))
async def test_recorded_responses_keep_raw_content(
    layout: str, tracer_provider: TracerProvider
) -> None:
    body = RECORDED_LAYOUTS[layout]
    provider = AnthropicProvider(replay_responses((200, body)))
    response = await complete_in_span(provider, seattle_request(), tracer_provider)
    assert response.raw_content == tuple(wire_form(body["content"]))
    assert response.text == expected_text(body)
    assert response.thinking_blocks == expected_thinking_blocks(body)
    assert_valid_block_params(response.raw_content)


@pytest.mark.parametrize("scenario", ["thinking_after_tool", "thinking_answer"])
async def test_thinking_answer_fixtures_parsed(
    scenario: str, tracer_provider: TracerProvider
) -> None:
    """The shapes the M2 evaluation saw: a thinking block on an answer (spec M2.T11)."""
    body = load_fixture(scenario)["body"]
    provider = AnthropicProvider(replay_client(scenario))
    response = await complete_in_span(provider, seattle_request(), tracer_provider)
    assert response.stop_reason == "end_turn"
    assert response.tool_calls == ()
    assert response.text == expected_text(body)
    assert response.thinking_blocks == expected_thinking_blocks(body) >= 1
    assert response.raw_content == tuple(wire_form(body["content"]))


@pytest.mark.parametrize("final_layout", sorted(FINAL_LAYOUTS))
@pytest.mark.parametrize("tool_layout", sorted(TOOL_USE_LAYOUTS))
async def test_assistant_turn_with_raw_content_sent_verbatim(
    tool_layout: str, final_layout: str, tracer_provider: TracerProvider
) -> None:
    tool_body = TOOL_USE_LAYOUTS[tool_layout]
    captured: list[dict[str, Any]] = []
    provider = AnthropicProvider(
        replay_responses((200, tool_body), (200, FINAL_LAYOUTS[final_layout]), requests=captured)
    )
    tool_use = await complete_in_span(provider, seattle_request(), tracer_provider)
    follow_up = ModelRequest(
        model="claude-sonnet-5-5",
        system="You are a helpful assistant.",
        turns=[
            UserTurn(text="Find the weather in Seattle"),
            AssistantTurn(
                text=tool_use.text, tool_calls=tool_use.tool_calls, raw_content=tool_use.raw_content
            ),
            ToolResultsTurn(
                results=tuple(ToolResult(c.id, '{"temp_c": 12}') for c in tool_use.tool_calls)
            ),
        ],
        tools=[WEATHER_TOOL],
        params={"max_tokens": 1024},
    )

    await complete_in_span(provider, follow_up, tracer_provider)

    assistant = captured[1]["messages"][1]
    assert assistant == {"role": "assistant", "content": wire_form(tool_body["content"])}
    assert_thinking_before_tool_use(assistant["content"])


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (message_body(stop_reason=None), "stop_reason"),
        (message_body(usage={"input_tokens": 3}), "output_tokens"),
        (message_body(content=[{"type": "text"}]), "text"),
        (message_body(content=[{"type": "tool_use", "id": "t1", "name": "x"}]), "input"),
        (message_body(content=[{"type": "tool_use", "id": "t1", "input": {}}]), "name"),
    ],
)
async def test_missing_or_ill_typed_field_raises(
    body: dict[str, Any], match: str, tracer_provider: TracerProvider
) -> None:
    provider = AnthropicProvider(replay_responses((200, body)))
    with pytest.raises(ProviderResponseError, match=match):
        await complete_in_span(provider, seattle_request(), tracer_provider)


async def test_request_mapping(tracer_provider: TracerProvider) -> None:
    captured: list[dict[str, Any]] = []
    provider = AnthropicProvider(replay_client("final_answer", requests=captured))
    request = ModelRequest(
        model="claude-sonnet-5-5",
        system="You are a weather assistant.",
        turns=[
            UserTurn(text="Find the weather in Seattle"),
            AssistantTurn(
                text="Let me check.",
                tool_calls=(
                    ToolCall(id="toolu_1", name="get_weather", arguments={"location": "Seattle"}),
                ),
            ),
            ToolResultsTurn(
                results=(
                    ToolResult(tool_call_id="toolu_1", content='{"temp_c": 12}'),
                    ToolResult(tool_call_id="toolu_2", content="boom", is_error=True),
                )
            ),
        ],
        tools=[WEATHER_TOOL],
        params={"max_tokens": 512},
    )

    await complete_in_span(provider, request, tracer_provider)

    assert len(captured) == 1
    sent = captured[0]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["system"] == "You are a weather assistant."
    assert sent["max_tokens"] == 512
    assert set(sent) == {"model", "system", "max_tokens", "messages", "tools"}
    assert sent["tools"] == [
        {
            "name": "get_weather",
            "description": "Get the current weather for a location.",
            "input_schema": WEATHER_TOOL.input_schema,
        }
    ]
    assert sent["messages"] == [
        {"role": "user", "content": "Find the weather in Seattle"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_weather",
                    "input": {"location": "Seattle"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '{"temp_c": 12}',
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "content": "boom",
                    "is_error": True,
                },
            ],
        },
    ]


@pytest.mark.parametrize("key", ["temperature", "top_p", "top_k", "max_steps", "stop_sequences"])
async def test_unsupported_param_raises(key: str, tracer_provider: TracerProvider) -> None:
    """Only `max_tokens` reaches the SDK (D8); validate_parameters filters keys upstream."""
    captured: list[dict[str, Any]] = []
    provider = AnthropicProvider(replay_client("final_answer", requests=captured))
    with pytest.raises(ValueError, match=key):
        await complete_in_span(provider, seattle_request(**{key: 0}), tracer_provider)
    assert captured == []


async def test_request_without_tools_omits_tools(tracer_provider: TracerProvider) -> None:
    captured: list[dict[str, Any]] = []
    provider = AnthropicProvider(replay_client("direct_answer", requests=captured))
    request = ModelRequest(
        model="claude-sonnet-5-5",
        system="",
        turns=[UserTurn(text="What is the capital of France?")],
        tools=[],
        params={"max_tokens": 64},
    )
    await complete_in_span(provider, request, tracer_provider)
    assert "tools" not in captured[0]
    assert "system" not in captured[0]


async def test_transient_error_retried_then_succeeds(
    exporter: InMemorySpanExporter, tracer_provider: TracerProvider
) -> None:
    sleeps = Sleeps()
    provider = AnthropicProvider(replay_client("overloaded", "final_answer"), sleep=sleeps)

    response = await complete_in_span(provider, seattle_request(), tracer_provider)

    assert response.stop_reason == "end_turn"
    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["agentops.llm.attempts"] == 2
    retries = [e for e in span.events if e.name == "gen_ai.retry"]
    assert len(retries) == 1
    assert retries[0].attributes is not None
    assert retries[0].attributes["error.type"] == "overloaded_error"
    assert sleeps.calls == [0.5]


async def test_rate_limit_and_server_error_retried(
    exporter: InMemorySpanExporter, tracer_provider: TracerProvider
) -> None:
    sleeps = Sleeps()
    rate_limited = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}
    server_error = {"type": "error", "error": {"type": "api_error", "message": "oops"}}
    ok = load_fixture("final_answer")["body"]
    client = replay_responses((429, rate_limited), (500, server_error), (200, ok))
    provider = AnthropicProvider(client, sleep=sleeps)

    await complete_in_span(provider, seattle_request(), tracer_provider)

    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["agentops.llm.attempts"] == 3
    types = [
        e.attributes["error.type"]
        for e in span.events
        if e.name == "gen_ai.retry" and e.attributes is not None
    ]
    assert types == ["rate_limit_error", "api_error"]
    assert sleeps.calls == [0.5, 1.0]


async def test_retries_exhausted_raises(
    exporter: InMemorySpanExporter, tracer_provider: TracerProvider
) -> None:
    sleeps = Sleeps()
    client = replay_client("overloaded", "overloaded", "overloaded")
    provider = AnthropicProvider(client, sleep=sleeps)

    with pytest.raises(ProviderAPIError, match="overloaded_error") as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)

    assert "529" in str(excinfo.value)
    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["agentops.llm.attempts"] == 3
    assert len([e for e in span.events if e.name == "gen_ai.retry"]) == 3
    assert sleeps.calls == [0.5, 1.0]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "invalid_request_error"),
        (401, "authentication_error"),
        (403, "permission_error"),
        (404, "not_found_error"),
    ],
)
async def test_non_transient_error_not_retried(
    status: int,
    error_type: str,
    exporter: InMemorySpanExporter,
    tracer_provider: TracerProvider,
) -> None:
    sleeps = Sleeps()
    body = {"type": "error", "error": {"type": error_type, "message": "nope"}}
    ok = load_fixture("final_answer")["body"]
    provider = AnthropicProvider(replay_responses((status, body), (200, ok)), sleep=sleeps)

    with pytest.raises(ProviderAPIError, match=error_type):
        await complete_in_span(provider, seattle_request(), tracer_provider)

    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["agentops.llm.attempts"] == 1
    assert sleeps.calls == []


async def test_auth_error_not_retried(
    exporter: InMemorySpanExporter, tracer_provider: TracerProvider
) -> None:
    body = {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}
    provider = AnthropicProvider(replay_responses((401, body)), sleep=Sleeps())
    with pytest.raises(ProviderAPIError, match="401"):
        await complete_in_span(provider, seattle_request(), tracer_provider)
    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["agentops.llm.attempts"] == 1


async def test_complete_works_without_a_current_span() -> None:
    provider = AnthropicProvider(replay_client("final_answer"))
    response = await provider.complete(seattle_request())
    assert response.stop_reason == "end_turn"


def test_client_has_no_sdk_retries() -> None:
    assert make_anthropic_client("k").max_retries == 0


def test_client_emits_no_sdk_spans() -> None:
    """The SDK's own OTel spans are off: the runtime owns the llm span (D6)."""
    assert make_anthropic_client("k").open_telemetry["traces"]["enabled"] is False


def test_provider_name() -> None:
    assert AnthropicProvider(make_anthropic_client("k")).name == "anthropic"


# --- failure classification (M2 D10) --------------------------------------------------------


def error_body(error_type: str, message: str = "nope") -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, "authentication_error"),
        (403, "permission_error"),
        (402, "billing_error"),
    ],
)
async def test_kind_account(
    status: int,
    error_type: str,
    exporter: InMemorySpanExporter,
    tracer_provider: TracerProvider,
) -> None:
    sleeps = Sleeps()
    provider = AnthropicProvider(replay_responses((status, error_body(error_type))), sleep=sleeps)

    with pytest.raises(ProviderAPIError) as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)

    assert excinfo.value.kind == "account"
    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["agentops.llm.attempts"] == 1
    assert sleeps.calls == []


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "billing_error"),
        (400, "authentication_error"),
        (400, "permission_error"),
    ],
)
async def test_kind_account_from_error_type_alone(
    status: int, error_type: str, tracer_provider: TracerProvider
) -> None:
    provider = AnthropicProvider(replay_responses((status, error_body(error_type))), sleep=Sleeps())
    with pytest.raises(ProviderAPIError) as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)
    assert excinfo.value.kind == "account"


async def test_kind_unavailable_after_retries(tracer_provider: TracerProvider) -> None:
    client = replay_client("overloaded", "overloaded", "overloaded")
    provider = AnthropicProvider(client, sleep=Sleeps())
    with pytest.raises(ProviderAPIError) as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)
    assert excinfo.value.kind == "unavailable"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(429, "rate_limit_error"), (500, "api_error"), (503, "api_error")],
)
async def test_kind_unavailable_for_rate_limit_and_server_errors(
    status: int, error_type: str, tracer_provider: TracerProvider
) -> None:
    body = error_body(error_type)
    provider = AnthropicProvider(
        replay_responses((status, body), (status, body), (status, body)), sleep=Sleeps()
    )
    with pytest.raises(ProviderAPIError) as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)
    assert excinfo.value.kind == "unavailable"


def raising_client(exc: Exception) -> Any:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    http_client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return make_anthropic_client("test-key", http_client=http_client)


@pytest.mark.parametrize(
    "exc",
    [httpx2.ConnectError("connection refused"), httpx2.ReadTimeout("timed out")],
    ids=["connection", "timeout"],
)
async def test_kind_connection_error(exc: Exception, tracer_provider: TracerProvider) -> None:
    provider = AnthropicProvider(raising_client(exc), sleep=Sleeps())
    with pytest.raises(ProviderAPIError) as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)
    assert excinfo.value.kind == "unavailable"


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(400, "invalid_request_error"), (404, "not_found_error"), (413, "request_too_large")],
)
async def test_kind_none_for_400(
    status: int, error_type: str, tracer_provider: TracerProvider
) -> None:
    provider = AnthropicProvider(replay_responses((status, error_body(error_type))), sleep=Sleeps())
    with pytest.raises(ProviderAPIError) as excinfo:
        await complete_in_span(provider, seattle_request(), tracer_provider)
    assert excinfo.value.kind is None


def test_provider_api_error_kind_defaults_to_none() -> None:
    assert ProviderAPIError("x").kind is None
    assert ProviderAPIError("x", kind="account").kind == "account"
    assert str(ProviderAPIError("x", kind="unavailable")) == "x"
