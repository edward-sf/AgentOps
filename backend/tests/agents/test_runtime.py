"""Runtime tests. Every model call is scripted or replayed: no test calls a model API."""

import asyncio
import json
import uuid
from collections import Counter
from collections.abc import Iterator
from typing import Any

import pytest
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import AgentConfig
from app.agents.providers.anthropic import AnthropicProvider, to_anthropic_kwargs
from app.agents.providers.base import (
    AssistantTurn,
    ModelRequest,
    ModelResponse,
    ProviderAPIError,
    ProviderResponseError,
    ToolCall,
    ToolResultsTurn,
    UserTurn,
)
from app.agents.runtime import RunOutcome, run_agent
from app.models import Dataset, EvaluationRun, Span, TestCase, Trace
from app.observability.tracing import RunTracer
from app.services.traces import get_trace_detail
from tests.agents.fakes import (
    THINKING_BLOCK,
    ScriptedProvider,
    end_turn,
    response,
    thinking_tool_use,
    tool_use,
)
from tests.agents.replay import (
    assert_thinking_before_tool_use,
    expected_text,
    expected_thinking_blocks,
    final_answer_layouts,
    load_fixture,
    replay_client,
    replay_responses,
    thinking_tool_use_layouts,
    wire_form,
)
from tests.factories import make_agent_version

QUESTION = "What's the weather in Seattle?"


async def run(
    session: AsyncSession,
    provider: Any,
    *,
    parameters: dict[str, Any] | None = None,
    tools: list[str] | None = None,
) -> tuple[RunOutcome, Trace, list[Span]]:
    version = await make_agent_version(
        session,
        parameters=parameters,
        tools=["get_weather"] if tools is None else tools,
    )
    config = AgentConfig.from_version(version)
    outcome = await run_agent(session=session, provider=provider, config=config, input=QUESTION)
    stored = await session.get(Trace, outcome.trace_id)
    assert stored is not None
    spans = list(await session.scalars(select(Span).where(Span.trace_id == outcome.trace_id)))
    return outcome, stored, spans


def root_of(spans: list[Span]) -> Span:
    roots = [s for s in spans if s.parent_otel_span_id is None]
    assert len(roots) == 1
    return roots[0]


def of_kind(spans: list[Span], kind: str) -> list[Span]:
    return sorted((s for s in spans if s.kind == kind), key=lambda s: s.started_at)


def assert_failed(
    outcome: RunOutcome, stored: Trace, spans: list[Span], error: str, failing_kind: str | None
) -> None:
    assert outcome.status == "failed"
    assert stored.status == "failed"
    assert stored.error == error
    assert stored.output is None
    root = root_of(spans)
    assert root.status == "error"
    assert root.error == error
    if failing_kind is not None:
        failing = [s for s in spans if s.kind == failing_kind and s.status == "error"]
        assert len(failing) == 1
        assert failing[0].error == error
    others = [s for s in spans if s.kind != "chain" and s.kind != failing_kind]
    assert all(s.status != "error" for s in others)


# --- completed runs -------------------------------------------------------------------------


async def test_tool_then_answer_completes(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(), end_turn("It is 12°C and raining")])
    outcome, stored, spans = await run(db_session, provider)

    assert outcome.status == "completed"
    assert stored.status == "completed"
    assert stored.output == "It is 12°C and raining"
    assert stored.input == QUESTION
    assert stored.error is None

    assert Counter(s.kind for s in spans) == {"chain": 1, "llm": 2, "tool": 1}
    root = root_of(spans)
    assert root.name == "agent.run"
    assert root.status == "ok"
    assert root.attributes["agentops.span.kind"] == "chain"
    assert root.attributes["agentops.agent_version_id"] == str(stored.agent_version_id)
    assert root.input == QUESTION
    assert root.output == "It is 12°C and raining"
    assert all(s.parent_otel_span_id == root.otel_span_id for s in spans if s is not root)

    # The second request carries the tool result back to the model.
    second = provider.requests[1]
    last_turn = second.turns[-1]
    assert isinstance(last_turn, ToolResultsTurn)
    assert last_turn.results[0].tool_call_id == "toolu_1"
    assert json.loads(last_turn.results[0].content)["temperature_c"] == 12

    llm = of_kind(spans, "llm")
    model = stored_model(provider)
    for span in llm:
        assert span.name == f"chat {model}"
        assert span.attributes["agentops.span.kind"] == "llm"
        assert span.attributes["gen_ai.operation.name"] == "chat"
        assert span.attributes["gen_ai.system"] == "anthropic"
        assert span.attributes["gen_ai.request.model"] == model
        assert span.attributes["gen_ai.response.model"] == "claude-sonnet-5-5"
        assert span.attributes["gen_ai.usage.input_tokens"] == 100
        assert span.attributes["gen_ai.usage.output_tokens"] == 20
    assert llm[0].attributes["gen_ai.response.finish_reasons"] == ["tool_use"]
    assert llm[1].attributes["gen_ai.response.finish_reasons"] == ["end_turn"]

    assert llm[0].input["system"] == "You are a helpful assistant."
    assert llm[0].input["turns"] == [{"type": "user", "text": QUESTION}]
    assert llm[0].input["tools"][0]["name"] == "get_weather"
    assert llm[0].input["params"] == {"max_tokens": 1024}
    assert llm[0].output == {
        "text": None,
        "tool_calls": [
            {"id": "toolu_1", "name": "get_weather", "arguments": {"location": "Seattle"}}
        ],
    }
    assert llm[1].output == {"text": "It is 12°C and raining", "tool_calls": []}
    assert [t["type"] for t in llm[1].input["turns"]] == ["user", "assistant", "tool_results"]

    (tool,) = of_kind(spans, "tool")
    assert tool.name == "execute_tool get_weather"
    assert tool.status == "ok"
    assert tool.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool.attributes["gen_ai.tool.name"] == "get_weather"
    assert tool.attributes["gen_ai.tool.call.id"] == "toolu_1"
    assert tool.input == {"location": "Seattle"}
    assert json.loads(tool.output)["conditions"] == "light rain"

    # Spans nest in model → tool → model order under the root.
    assert llm[0].ended_at <= tool.started_at
    assert tool.ended_at <= llm[1].started_at


async def test_thinking_block_replayed_unchanged(db_session: AsyncSession) -> None:
    first = thinking_tool_use()
    provider = ScriptedProvider([first, end_turn()])

    outcome, _, spans = await run(db_session, provider)

    assert outcome.status == "completed"
    assistant = provider.requests[1].turns[1]
    assert isinstance(assistant, AssistantTurn)
    assert assistant.raw_content == first.raw_content
    assert assistant.raw_content[0] == THINKING_BLOCK
    sent = to_anthropic_kwargs(provider.requests[1])["messages"][1]
    assert sent == {"role": "assistant", "content": list(first.raw_content)}
    assert [b["type"] for b in sent["content"]] == ["thinking", "tool_use"]

    llm = of_kind(spans, "llm")
    assert [s.attributes["agentops.llm.thinking_blocks"] for s in llm] == [1, 0]
    # Thinking content and signatures are not stored on spans.
    assert all("signature" not in json.dumps([s.input, s.output]) for s in llm)


@pytest.mark.parametrize("final_layout", sorted(final_answer_layouts()))
@pytest.mark.parametrize("tool_layout", sorted(thinking_tool_use_layouts()))
async def test_replayed_thinking_tool_use_sends_thinking_back(
    db_session: AsyncSession, tool_layout: str, final_layout: str
) -> None:
    tool_body = thinking_tool_use_layouts()[tool_layout]
    final_body = final_answer_layouts()[final_layout]
    captured: list[dict[str, Any]] = []
    client = replay_responses((200, tool_body), (200, final_body), requests=captured)
    outcome, stored, spans = await run(db_session, AnthropicProvider(client, sleep=no_sleep))

    assert outcome.status == "completed"
    assert stored.output == (expected_text(final_body) or "")
    sent = captured[1]["messages"][1]
    assert sent == {"role": "assistant", "content": wire_form(tool_body["content"])}
    assert_thinking_before_tool_use(sent["content"])
    llm = of_kind(spans, "llm")
    assert [s.attributes["agentops.llm.thinking_blocks"] for s in llm] == [
        expected_thinking_blocks(tool_body),
        expected_thinking_blocks(final_body),
    ]


def stored_model(provider: ScriptedProvider) -> str:
    return provider.requests[0].model


async def test_trace_rebuilds_as_one_tree(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(), end_turn()])
    outcome, _, _ = await run(db_session, provider)
    detail = await get_trace_detail(db_session, outcome.trace_id)
    assert detail is not None
    assert len(detail.spans) == 1
    (root,) = detail.spans
    assert [c.kind for c in root.children] == ["llm", "tool", "llm"]


async def test_direct_answer_completes(db_session: AsyncSession) -> None:
    outcome, stored, spans = await run(db_session, ScriptedProvider([end_turn("Hello")]))
    assert outcome.status == "completed"
    assert stored.output == "Hello"
    assert Counter(s.kind for s in spans) == {"chain": 1, "llm": 1}


async def test_refusal_completes(db_session: AsyncSession) -> None:
    refusal = response("refusal", text="I can't help with that.")
    outcome, stored, spans = await run(db_session, ScriptedProvider([refusal]))
    assert outcome.status == "completed"
    assert stored.output == "I can't help with that."
    (llm,) = of_kind(spans, "llm")
    assert llm.attributes["gen_ai.response.finish_reasons"] == ["refusal"]
    assert llm.status != "error"


async def test_refusal_without_text_completes_with_empty_output(db_session: AsyncSession) -> None:
    outcome, stored, _ = await run(db_session, ScriptedProvider([response("refusal")]))
    assert outcome.status == "completed"
    assert stored.output == ""


async def test_stop_sequence_completes(db_session: AsyncSession) -> None:
    stopped = response("stop_sequence", text="Partial but deliberate")
    outcome, stored, _ = await run(db_session, ScriptedProvider([stopped]))
    assert outcome.status == "completed"
    assert stored.output == "Partial but deliberate"


# --- failed runs ----------------------------------------------------------------------------


async def test_max_tokens_fails(db_session: AsyncSession) -> None:
    truncated = response("max_tokens", text="It is 12")
    outcome, stored, spans = await run(db_session, ScriptedProvider([truncated]))
    assert_failed(outcome, stored, spans, "answer truncated at max_tokens", "llm")


async def test_max_tokens_with_empty_content_fails(db_session: AsyncSession) -> None:
    outcome, stored, spans = await run(db_session, ScriptedProvider([response("max_tokens")]))
    assert_failed(outcome, stored, spans, "answer truncated at max_tokens", "llm")


async def test_max_tokens_after_tool_call_fails(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(), response("max_tokens", text="It is")])
    outcome, stored, spans = await run(db_session, provider)
    assert_failed(outcome, stored, spans, "answer truncated at max_tokens", "llm")
    assert Counter(s.kind for s in spans) == {"chain": 1, "llm": 2, "tool": 1}


@pytest.mark.parametrize("reason", ["pause_turn", "model_context_window_exceeded"])
async def test_unknown_stop_reason_fails(db_session: AsyncSession, reason: str) -> None:
    outcome, stored, spans = await run(db_session, ScriptedProvider([response(reason, text="x")]))
    assert_failed(outcome, stored, spans, f"unexpected stop reason: {reason}", "llm")


async def test_tool_use_without_tool_calls_fails(db_session: AsyncSession) -> None:
    outcome, stored, spans = await run(db_session, ScriptedProvider([response("tool_use")]))
    assert_failed(
        outcome, stored, spans, "model stopped for tool_use without calling a tool", "llm"
    )


async def test_unknown_tool_fails(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(name="search", arguments={"q": "x"})])
    outcome, stored, spans = await run(db_session, provider)
    assert_failed(outcome, stored, spans, "model called unknown tool: search", "tool")
    (tool,) = of_kind(spans, "tool")
    assert tool.name == "execute_tool search"
    assert tool.input == {"q": "x"}
    assert len(provider.requests) == 1  # no loop after the failure


async def test_registered_tool_not_in_version_is_unknown(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(name="get_weather")])
    outcome, stored, spans = await run(db_session, provider, tools=[])
    assert_failed(outcome, stored, spans, "model called unknown tool: get_weather", "tool")
    assert provider.requests[0].tools == []


async def test_tool_exception_fails(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(arguments={})])
    outcome, stored, spans = await run(db_session, provider)
    assert_failed(outcome, stored, spans, "tool get_weather failed: location is required", "tool")
    assert len(provider.requests) == 1


async def test_second_tool_call_failure_stops_the_run(db_session: AsyncSession) -> None:
    calls = (
        ToolCall(id="toolu_1", name="get_weather", arguments={"location": "London"}),
        ToolCall(id="toolu_2", name="nope", arguments={}),
    )
    provider = ScriptedProvider([response("tool_use", tool_calls=calls)])
    outcome, stored, spans = await run(db_session, provider)
    assert_failed(outcome, stored, spans, "model called unknown tool: nope", "tool")
    assert [s.status for s in of_kind(spans, "tool")] == ["ok", "error"]


@pytest.mark.parametrize(
    "exc",
    [
        ProviderAPIError("anthropic API error after 1 attempt: authentication_error (status 401)"),
        ProviderResponseError("anthropic response: unknown content block type 'thinking'"),
    ],
)
async def test_provider_error_fails(db_session: AsyncSession, exc: Exception) -> None:
    provider = ScriptedProvider([tool_use(), exc])
    outcome, stored, spans = await run(db_session, provider)
    assert_failed(outcome, stored, spans, f"provider error: {exc}", "llm")
    assert Counter(s.kind for s in spans) == {"chain": 1, "llm": 2, "tool": 1}
    failed_llm = of_kind(spans, "llm")[1]
    assert failed_llm.output is None
    assert "gen_ai.response.finish_reasons" not in failed_llm.attributes


# --- step limit (D8) ------------------------------------------------------------------------


async def test_answer_on_last_allowed_step_completes(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(), end_turn()])
    outcome, _, spans = await run(db_session, provider, parameters={"max_steps": 2})
    assert outcome.status == "completed"
    assert len(of_kind(spans, "llm")) == 2


async def test_step_limit_fails(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(), tool_use(call_id="toolu_2")])
    outcome, stored, spans = await run(db_session, provider, parameters={"max_steps": 2})
    assert_failed(outcome, stored, spans, "step limit reached (2 model calls)", None)
    assert len(of_kind(spans, "llm")) == 2
    assert len(of_kind(spans, "tool")) == 2
    assert all(s.status != "error" for s in spans if s.kind != "chain")
    assert len(provider.requests) == 2


async def test_step_limit_of_one(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use()])
    outcome, stored, spans = await run(db_session, provider, parameters={"max_steps": 1})
    assert_failed(outcome, stored, spans, "step limit reached (1 model calls)", None)
    assert len(of_kind(spans, "llm")) == 1


async def test_default_step_limit_is_ten(db_session: AsyncSession) -> None:
    script: list[ModelResponse | Exception] = [tool_use(call_id=f"t{i}") for i in range(10)]
    provider = ScriptedProvider(script)
    outcome, stored, spans = await run(db_session, provider, parameters={})
    assert_failed(outcome, stored, spans, "step limit reached (10 model calls)", None)
    assert len(of_kind(spans, "llm")) == 10


async def test_long_run_within_limit_does_not_hit_recursion_limit(
    db_session: AsyncSession,
) -> None:
    script: list[ModelResponse | Exception] = [tool_use(call_id=f"t{i}") for i in range(24)]
    provider = ScriptedProvider([*script, end_turn()])
    outcome, _, _ = await run(db_session, provider, parameters={"max_steps": 25})
    assert outcome.status == "completed"


async def test_max_steps_not_sent_to_provider(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([end_turn()])
    await run(db_session, provider, parameters={"max_steps": 3, "max_tokens": 256})
    request = provider.requests[0]
    assert "max_steps" not in request.params
    assert request.params == {"max_tokens": 256}
    assert request.turns == [UserTurn(text=QUESTION)]


# --- tracing --------------------------------------------------------------------------------


class RetryingProvider:
    """Adds a retry event to the current span, like the adapter does, then answers."""

    name = "anthropic"

    async def complete(self, request: ModelRequest) -> ModelResponse:
        span = trace.get_current_span()
        span.add_event("gen_ai.retry", {"error.type": "rate_limit_error"})
        span.set_attribute("agentops.llm.attempts", 2)
        return end_turn()


async def test_retry_event_lands_on_llm_span(db_session: AsyncSession) -> None:
    _, _, spans = await run(db_session, RetryingProvider())
    (llm,) = of_kind(spans, "llm")
    assert llm.events[0]["name"] == "gen_ai.retry"
    assert llm.attributes["agentops.llm.attempts"] == 2
    assert root_of(spans).events == []


async def test_run_does_not_touch_global_tracer_provider(db_session: AsyncSession) -> None:
    before = trace.get_tracer_provider()
    await run(db_session, ScriptedProvider([tool_use(), end_turn()]))
    assert trace.get_tracer_provider() is before


async def test_parallel_run_tracers_do_not_share_spans() -> None:
    async def emit(run_tracer: RunTracer, name: str) -> None:
        with run_tracer.tracer.start_as_current_span(name):
            await asyncio.sleep(0)
            with run_tracer.tracer.start_as_current_span(f"{name}.child"):
                await asyncio.sleep(0)

    with RunTracer() as a, RunTracer() as b:
        await asyncio.gather(emit(a, "a"), emit(b, "b"))
        a_records, b_records = a.records(), b.records()
    assert sorted(r.name for r in a_records) == ["a", "a.child"]
    assert sorted(r.name for r in b_records) == ["b", "b.child"]
    assert {r.otel_trace_id for r in a_records}.isdisjoint({r.otel_trace_id for r in b_records})


async def test_two_runs_of_one_version_store_separate_traces(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    config = AgentConfig.from_version(version)
    outcomes: list[RunOutcome] = []
    for answer in ("one", "two"):
        outcome = await run_agent(
            session=db_session,
            provider=ScriptedProvider([end_turn(answer)]),
            config=config,
            input=QUESTION,
        )
        outcomes.append(outcome)
    assert outcomes[0].trace_id != outcomes[1].trace_id
    for outcome in outcomes:
        spans = list(
            await db_session.scalars(select(Span).where(Span.trace_id == outcome.trace_id))
        )
        assert Counter(s.kind for s in spans) == {"chain": 1, "llm": 1}


async def test_unexpected_exception_propagates_and_stores_nothing(
    db_session: AsyncSession,
) -> None:
    version = await make_agent_version(db_session)
    config = AgentConfig.from_version(version)
    with pytest.raises(AssertionError, match="script exhausted"):
        await run_agent(
            session=db_session, provider=ScriptedProvider([]), config=config, input=QUESTION
        )
    stored = await db_session.scalar(select(Trace.id).where(Trace.agent_version_id == version.id))
    assert stored is None


# --- the real adapter over replayed responses (Review Focus 5) ------------------------------


async def no_sleep(_: float) -> None:
    return None


RATE_LIMITED = (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})


async def test_replayed_tool_then_answer_completes(db_session: AsyncSession) -> None:
    provider = AnthropicProvider(replay_client("tool_use", "final_answer"), sleep=no_sleep)
    outcome, stored, spans = await run(db_session, provider)
    assert outcome.status == "completed"
    assert stored.output == (expected_text(load_fixture("final_answer")["body"]) or "")
    assert Counter(s.kind for s in spans) == {"chain": 1, "llm": 2, "tool": 1}
    assert all(s.attributes["agentops.llm.attempts"] == 1 for s in of_kind(spans, "llm"))


async def test_replayed_429_then_success_is_one_llm_span(db_session: AsyncSession) -> None:
    ok = load_fixture("direct_answer")["body"]
    provider = AnthropicProvider(replay_responses(RATE_LIMITED, (200, ok)), sleep=no_sleep)
    outcome, _, spans = await run(db_session, provider)
    assert outcome.status == "completed"
    (llm,) = of_kind(spans, "llm")
    assert llm.attributes["agentops.llm.attempts"] == 2
    assert [e["name"] for e in llm.events] == ["gen_ai.retry"]
    assert llm.status == "ok"


async def test_replayed_three_429s_fail_the_trace(db_session: AsyncSession) -> None:
    provider = AnthropicProvider(
        replay_responses(RATE_LIMITED, RATE_LIMITED, RATE_LIMITED), sleep=no_sleep
    )
    outcome, stored, spans = await run(db_session, provider)
    assert outcome.status == "failed"
    assert stored.error is not None
    assert stored.error.startswith("provider error: anthropic API error after 3 attempts")
    assert "rate_limit_error (status 429)" in stored.error
    (llm,) = of_kind(spans, "llm")
    assert llm.status == "error"
    assert llm.error == stored.error
    assert llm.attributes["agentops.llm.attempts"] == 3
    assert len([e for e in llm.events if e["name"] == "gen_ai.retry"]) == 3


async def test_replayed_401_fails_without_retry(db_session: AsyncSession) -> None:
    unauthorized = (
        401,
        {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}},
    )
    provider = AnthropicProvider(replay_responses(unauthorized), sleep=no_sleep)
    outcome, stored, spans = await run(db_session, provider)
    assert outcome.status == "failed"
    assert stored.error is not None
    assert stored.error.startswith("provider error: ")
    assert "authentication_error (status 401)" in stored.error
    (llm,) = of_kind(spans, "llm")
    assert llm.status == "error"
    assert llm.events == []
    assert llm.attributes["agentops.llm.attempts"] == 1


async def test_replayed_empty_max_tokens_fails(db_session: AsyncSession) -> None:
    provider = AnthropicProvider(replay_client("max_tokens"), sleep=no_sleep)
    outcome, stored, spans = await run(db_session, provider)
    assert_failed(outcome, stored, spans, "answer truncated at max_tokens", "llm")


def test_run_outcome_shape() -> None:
    outcome = RunOutcome(trace_id=uuid.uuid4(), status="completed")
    assert outcome.status == "completed"
    assert outcome.output is None
    assert outcome.error is None
    assert outcome.provider_failure is None


# --- richer outcomes (M2.T2, D10) -----------------------------------------------------------


async def test_outcome_carries_output(db_session: AsyncSession) -> None:
    outcome, stored, _ = await run(db_session, ScriptedProvider([end_turn("Paris")]))
    assert outcome.status == "completed"
    assert outcome.output == "Paris"
    assert outcome.error is None
    assert outcome.provider_failure is None
    assert stored.output == "Paris"


async def test_outcome_carries_agent_failure_error(db_session: AsyncSession) -> None:
    outcome, stored, _ = await run(db_session, ScriptedProvider([response("max_tokens")]))
    assert outcome.status == "failed"
    assert outcome.output is None
    assert outcome.error == "answer truncated at max_tokens"
    assert outcome.error == stored.error
    assert outcome.provider_failure is None


@pytest.mark.parametrize("kind", ["account", "unavailable"])
async def test_outcome_provider_failure(db_session: AsyncSession, kind: str) -> None:
    exc = ProviderAPIError("x", kind=kind)  # type: ignore[arg-type]
    outcome, stored, spans = await run(db_session, ScriptedProvider([exc]))
    assert outcome.status == "failed"
    assert outcome.provider_failure == kind
    assert outcome.error is not None
    assert outcome.error.startswith("provider error:")
    assert outcome.error == stored.error
    assert outcome.output is None
    assert_failed(outcome, stored, spans, "provider error: x", "llm")


async def test_provider_failure_after_tool_call(db_session: AsyncSession) -> None:
    provider = ScriptedProvider([tool_use(), ProviderAPIError("down", kind="unavailable")])
    outcome, _, _ = await run(db_session, provider)
    assert outcome.status == "failed"
    assert outcome.provider_failure == "unavailable"


@pytest.mark.parametrize(
    "exc",
    [
        ProviderResponseError("anthropic response: unknown content block type 'thinking'"),
        ProviderAPIError("anthropic API error after 1 attempt: invalid_request_error"),
    ],
    ids=["response_error", "api_error_without_kind"],
)
async def test_response_error_is_agent_failure(db_session: AsyncSession, exc: Exception) -> None:
    outcome, _, _ = await run(db_session, ScriptedProvider([exc]))
    assert outcome.status == "failed"
    assert outcome.provider_failure is None
    assert outcome.error == f"provider error: {exc}"


async def test_replayed_401_is_account_failure(db_session: AsyncSession) -> None:
    unauthorized = (
        401,
        {"type": "error", "error": {"type": "authentication_error", "message": "bad key"}},
    )
    provider = AnthropicProvider(replay_responses(unauthorized), sleep=no_sleep)
    outcome, _, _ = await run(db_session, provider)
    assert outcome.provider_failure == "account"


async def test_replayed_three_429s_are_unavailable(db_session: AsyncSession) -> None:
    provider = AnthropicProvider(
        replay_responses(RATE_LIMITED, RATE_LIMITED, RATE_LIMITED), sleep=no_sleep
    )
    outcome, _, _ = await run(db_session, provider)
    assert outcome.provider_failure == "unavailable"


# --- evaluation links (M2.T2; ingest copies them from M2.T1) --------------------------------


async def make_run_and_case(
    session: AsyncSession, version_id: uuid.UUID
) -> tuple[EvaluationRun, TestCase]:
    """T1-independent setup: inline rows that satisfy the trace's foreign keys."""
    dataset = Dataset(name=f"dataset-{uuid.uuid4().hex[:12]}")
    session.add(dataset)
    await session.flush()
    case = TestCase(dataset_id=dataset.id, key="case_001", input=QUESTION)
    evaluation_run = EvaluationRun(agent_version_id=version_id, dataset_id=dataset.id)
    session.add_all([case, evaluation_run])
    await session.flush()
    return evaluation_run, case


async def run_linked(session: AsyncSession) -> tuple[RunOutcome, EvaluationRun, TestCase]:
    version = await make_agent_version(session)
    evaluation_run, case = await make_run_and_case(session, version.id)
    outcome = await run_agent(
        session=session,
        provider=ScriptedProvider([end_turn()]),
        config=AgentConfig.from_version(version),
        input=QUESTION,
        evaluation_run_id=evaluation_run.id,
        test_case_id=case.id,
    )
    return outcome, evaluation_run, case


async def test_evaluation_ids_set_on_root_span(db_session: AsyncSession) -> None:
    outcome, evaluation_run, case = await run_linked(db_session)
    spans = list(await db_session.scalars(select(Span).where(Span.trace_id == outcome.trace_id)))
    root = root_of(spans)
    assert root.attributes["agentops.evaluation_run_id"] == str(evaluation_run.id)
    assert root.attributes["agentops.test_case_id"] == str(case.id)
    assert all("agentops.evaluation_run_id" not in s.attributes for s in spans if s is not root)


async def test_evaluation_ids_omitted_by_default(db_session: AsyncSession) -> None:
    outcome, _, spans = await run(db_session, ScriptedProvider([end_turn()]))
    root = root_of(spans)
    assert "agentops.evaluation_run_id" not in root.attributes
    assert "agentops.test_case_id" not in root.attributes
    stored = await db_session.get(Trace, outcome.trace_id)
    assert stored is not None
    assert stored.evaluation_run_id is None
    assert stored.test_case_id is None


async def test_trace_linked_to_evaluation(db_session: AsyncSession) -> None:
    outcome, evaluation_run, case = await run_linked(db_session)
    stored = await db_session.get(Trace, outcome.trace_id)
    assert stored is not None
    assert stored.evaluation_run_id == evaluation_run.id
    assert stored.test_case_id == case.id


# --- no third-party tracing (D6) ------------------------------------------------------------


def _clear_langsmith_env_caches() -> None:
    """langsmith 0.15 memoizes env lookups; `tracing_is_enabled` reads them via `get_env_var`."""
    from langsmith import utils as ls_utils

    ls_utils.get_env_var.cache_clear()
    ls_utils.get_tracer_project.cache_clear()


@pytest.fixture
def langsmith_tracing_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """LangSmith tracing switched on by env, as a deployment might have it, for one test.

    The caches are cleared after `setenv`, so this test sees the variables whatever ran
    before it, and again at teardown, so a cached "true" never leaks into later tests.
    """
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    _clear_langsmith_env_caches()
    yield
    monkeypatch.undo()
    _clear_langsmith_env_caches()


@pytest.mark.usefixtures("langsmith_tracing_env")
async def test_langsmith_tracing_env_does_not_attach_a_tracer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LangSmith env vars must never switch on LangChain tracing of a run's data (D6)."""
    import langsmith
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import utils as ls_utils

    assert ls_utils.tracing_is_enabled() is True  # the env really is on for this test
    tracers: list[object] = []
    clients: list[object] = []
    tracer_init = LangChainTracer.__init__
    client_init = langsmith.Client.__init__

    def record_tracer(self: LangChainTracer, *args: Any, **kwargs: Any) -> None:
        tracers.append(self)
        tracer_init(self, *args, **kwargs)

    def record_client(self: langsmith.Client, *args: Any, **kwargs: Any) -> None:
        clients.append(self)
        client_init(self, *args, **kwargs)

    monkeypatch.setattr(LangChainTracer, "__init__", record_tracer)
    monkeypatch.setattr(langsmith.Client, "__init__", record_client)

    outcome, _, _ = await run(db_session, ScriptedProvider([tool_use(), end_turn()]))

    assert outcome.status == "completed"
    assert tracers == []
    assert clients == []
