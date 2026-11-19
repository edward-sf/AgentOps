import json
import secrets
import uuid
from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentVersion, Span, Trace
from app.observability import attributes as attr
from app.observability.ingest import IngestError, SpanRecord, from_readable_span, ingest_spans
from tests.factories import make_agent_version, span_record

QUESTION = "What's the weather in Seattle?"
ANSWER = "It is 12°C and cloudy in Seattle."


def run_records(
    version: AgentVersion, *, root_overrides: dict[str, object] | None = None
) -> tuple[SpanRecord, SpanRecord, SpanRecord]:
    """A basic run: a root `chain`, an `llm` child and a `tool` child of the root."""
    trace_id = secrets.token_hex(16)
    root_id, llm_id, tool_id = (secrets.token_hex(8) for _ in range(3))
    root = span_record(
        otel_trace_id=trace_id,
        otel_span_id=root_id,
        name="agent.run",
        attributes={
            attr.SPAN_KIND: "chain",
            attr.AGENT_VERSION_ID: str(version.id),
            attr.INPUT: json.dumps(QUESTION),
            attr.OUTPUT: json.dumps(ANSWER),
        },
        **(root_overrides or {}),
    )
    llm = span_record(
        otel_trace_id=trace_id,
        otel_span_id=llm_id,
        parent_otel_span_id=root_id,
        name="chat claude-sonnet-4-5",
        attributes={attr.GEN_AI_OPERATION: "chat", attr.GEN_AI_INPUT_TOKENS: 20},
    )
    tool = span_record(
        otel_trace_id=trace_id,
        otel_span_id=tool_id,
        parent_otel_span_id=root_id,
        name="execute_tool get_weather",
        attributes={
            attr.SPAN_KIND: "tool",
            attr.GEN_AI_TOOL_NAME: "get_weather",
            attr.INPUT: json.dumps({"location": "Seattle"}),
            attr.OUTPUT: json.dumps({"temperature_c": 12}),
        },
    )
    return root, llm, tool


async def count(session: AsyncSession, model: type) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def spans_by_otel_id(session: AsyncSession, trace_id: uuid.UUID) -> dict[str, Span]:
    rows = (await session.execute(select(Span).where(Span.trace_id == trace_id))).scalars()
    return {s.otel_span_id: s for s in rows}


async def assert_basic_run(
    session: AsyncSession, version: AgentVersion, records: tuple[SpanRecord, ...]
) -> None:
    root, llm, tool = records
    trace = (
        await session.execute(select(Trace).where(Trace.otel_trace_id == root.otel_trace_id))
    ).scalar_one()
    assert trace.agent_version_id == version.id
    assert trace.status == "completed"
    assert trace.input == QUESTION
    assert trace.output == ANSWER
    assert trace.error is None
    assert trace.started_at == root.started_at
    assert trace.ended_at == root.ended_at

    spans = await spans_by_otel_id(session, trace.id)
    assert len(spans) == 3
    assert spans[root.otel_span_id].kind == "chain"
    assert spans[llm.otel_span_id].kind == "llm"
    assert spans[tool.otel_span_id].kind == "tool"
    assert spans[root.otel_span_id].parent_otel_span_id is None
    assert spans[llm.otel_span_id].parent_otel_span_id == root.otel_span_id
    assert spans[tool.otel_span_id].parent_otel_span_id == root.otel_span_id


async def test_ingest_builds_trace_and_spans(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    records = run_records(version)
    trace_ids = await ingest_spans(db_session, records)
    assert len(trace_ids) == 1
    await assert_basic_run(db_session, version, records)


async def test_ingest_children_before_root(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    root, llm, tool = run_records(version)
    await ingest_spans(db_session, [tool, llm, root])
    await assert_basic_run(db_session, version, (root, llm, tool))


async def test_ingest_is_idempotent(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    records = run_records(version)
    first = await ingest_spans(db_session, records)
    second = await ingest_spans(db_session, records)
    assert first == second
    assert await count(db_session, Trace) == 1
    assert await count(db_session, Span) == 3


async def test_ingest_later_batch_joins_existing_trace(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    root, llm, tool = run_records(version)
    first = await ingest_spans(db_session, [root, llm])
    second = await ingest_spans(db_session, [tool])
    assert first == second
    await assert_basic_run(db_session, version, (root, llm, tool))


async def test_ingest_failed_root(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    message = "step limit reached (10 model calls)"
    root, llm, tool = run_records(version)
    root = span_record(
        otel_trace_id=root.otel_trace_id,
        otel_span_id=root.otel_span_id,
        status="error",
        status_message=message,
        attributes={
            attr.SPAN_KIND: "chain",
            attr.AGENT_VERSION_ID: str(version.id),
            attr.INPUT: json.dumps(QUESTION),
            attr.ERROR: message,
        },
    )
    [trace_id] = await ingest_spans(db_session, [root, llm, tool])
    trace = await db_session.get(Trace, trace_id)
    assert trace is not None
    assert trace.status == "failed"
    assert trace.error == message
    assert trace.output is None
    spans = await spans_by_otel_id(db_session, trace_id)
    assert spans[root.otel_span_id].status == "error"
    assert spans[root.otel_span_id].error == message


async def test_ingest_strips_io_attributes(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    root, llm, tool = run_records(version)
    [trace_id] = await ingest_spans(db_session, [root, llm, tool])
    stored = (await spans_by_otel_id(db_session, trace_id))[tool.otel_span_id]
    assert stored.input == {"location": "Seattle"}
    assert stored.output == {"temperature_c": 12}
    assert "agentops.input" not in stored.attributes
    assert "agentops.output" not in stored.attributes
    assert stored.attributes[attr.GEN_AI_TOOL_NAME] == "get_weather"


async def test_ingest_stores_events(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    root, llm, tool = run_records(version)
    event = {
        "name": "gen_ai.retry",
        "timestamp": "2026-11-19T12:00:00.500000+00:00",
        "attributes": {"error.type": "RateLimitError"},
    }
    llm = span_record(**{**llm.__dict__, "events": [event]})
    [trace_id] = await ingest_spans(db_session, [root, llm, tool])
    stored = (await spans_by_otel_id(db_session, trace_id))[llm.otel_span_id]
    assert stored.events == [event]


async def test_ingest_unknown_trace_without_root_raises(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    _, llm, tool = run_records(version)
    with pytest.raises(IngestError):
        await ingest_spans(db_session, [llm, tool])
    assert await count(db_session, Trace) == 0
    assert await count(db_session, Span) == 0


async def test_ingest_root_without_agent_version_raises(db_session: AsyncSession) -> None:
    root = span_record(otel_trace_id=secrets.token_hex(16), attributes={attr.SPAN_KIND: "chain"})
    with pytest.raises(IngestError):
        await ingest_spans(db_session, [root])
    bad = span_record(
        otel_trace_id=secrets.token_hex(16),
        attributes={attr.SPAN_KIND: "chain", attr.AGENT_VERSION_ID: "not-a-uuid"},
    )
    with pytest.raises(IngestError):
        await ingest_spans(db_session, [bad])
    unknown = span_record(
        otel_trace_id=secrets.token_hex(16),
        attributes={attr.SPAN_KIND: "chain", attr.AGENT_VERSION_ID: str(uuid.uuid4())},
    )
    with pytest.raises(IngestError, match="unknown agent version"):
        await ingest_spans(db_session, [unknown])
    assert await count(db_session, Trace) == 0


async def test_ingest_batch_with_one_bad_trace_stores_nothing(db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    good = run_records(version)
    _, orphan_llm, _ = run_records(version)
    with pytest.raises(IngestError):
        await ingest_spans(db_session, [*good, orphan_llm])
    assert await count(db_session, Trace) == 0
    assert await count(db_session, Span) == 0


def test_from_readable_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("agent.run") as root:
        root.set_attribute(attr.SPAN_KIND, "chain")
        with tracer.start_as_current_span("execute_tool get_weather") as child:
            child.set_attribute(attr.SPAN_KIND, "tool")
            child.set_attribute(attr.GEN_AI_FINISH_REASONS, ["tool_use"])
            child.add_event("gen_ai.retry", {"attempt": 1})
            child.set_status(Status(StatusCode.ERROR, "boom"))
    provider.shutdown()

    finished = {s.name: s for s in exporter.get_finished_spans()}
    rec_root = from_readable_span(finished["agent.run"])
    rec_child = from_readable_span(finished["execute_tool get_weather"])

    assert len(rec_root.otel_trace_id) == 32
    assert len(rec_root.otel_span_id) == 16
    assert rec_root.otel_trace_id == rec_root.otel_trace_id.lower()
    assert rec_root.otel_trace_id == format(root.get_span_context().trace_id, "032x")
    assert rec_root.otel_span_id == format(root.get_span_context().span_id, "016x")
    assert rec_root.parent_otel_span_id is None
    assert rec_root.status == "unset"

    assert rec_child.otel_trace_id == rec_root.otel_trace_id
    assert rec_child.parent_otel_span_id == rec_root.otel_span_id
    assert rec_child.status == "error"
    assert rec_child.status_message == "boom"
    assert rec_child.attributes == {
        attr.SPAN_KIND: "tool",
        attr.GEN_AI_FINISH_REASONS: ["tool_use"],
    }
    assert rec_child.started_at.tzinfo is not None
    assert rec_child.started_at <= rec_child.ended_at
    [event] = rec_child.events
    assert event["name"] == "gen_ai.retry"
    assert event["attributes"] == {"attempt": 1}
    assert datetime.fromisoformat(event["timestamp"]).tzinfo == UTC
