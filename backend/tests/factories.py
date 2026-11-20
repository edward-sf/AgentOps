"""Shared builders for tests. Later tasks reuse these; keep signatures stable."""

import secrets
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentVersion, Dataset, EvaluationRun, TestCase, Trace
from app.observability.ingest import SpanRecord

DEFAULT_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
DEFAULT_SPAN_ID = "b7ad6b7169203331"


async def make_agent_version(
    session: AsyncSession,
    *,
    tools: list[str] = ["get_weather"],  # noqa: B006 - signature fixed by the M1 plan; copied below
    parameters: dict[str, Any] | None = None,
    provider: str = "anthropic",
) -> AgentVersion:
    """Create an Agent with a unique name and its version 1."""
    agent = Agent(name=f"agent-{uuid.uuid4().hex[:12]}")
    session.add(agent)
    await session.flush()
    version = AgentVersion(
        agent_id=agent.id,
        version=1,
        provider=provider,
        model="claude-sonnet-5-5",
        system_prompt="You are a helpful assistant.",
        parameters=dict(parameters) if parameters is not None else {"max_tokens": 1024},
        tools=list(tools),
    )
    session.add(version)
    await session.flush()
    return version


async def make_dataset(session: AsyncSession, *, cases: int = 3) -> Dataset:
    """Create a Dataset with a unique name and `cases` test cases keyed `case_001`…."""
    dataset = Dataset(name=f"dataset-{uuid.uuid4().hex[:12]}")
    session.add(dataset)
    await session.flush()
    session.add_all(
        TestCase(
            dataset_id=dataset.id,
            key=f"case_{n:03d}",
            input=f"Question {n}?",
            expected_behavior=["does the thing"],
        )
        for n in range(1, cases + 1)
    )
    await session.flush()
    return dataset


async def make_evaluation_run(
    session: AsyncSession,
    *,
    version: AgentVersion | None = None,
    dataset: Dataset | None = None,
    **fields: Any,
) -> EvaluationRun:
    """Create an EvaluationRun, creating the version and dataset when not given."""
    if version is None:
        version = await make_agent_version(session)
    if dataset is None:
        dataset = await make_dataset(session)
    run = EvaluationRun(agent_version_id=version.id, dataset_id=dataset.id, **fields)
    session.add(run)
    await session.flush()
    return run


async def make_trace(
    session: AsyncSession, *, version: AgentVersion | None = None, **fields: Any
) -> Trace:
    """Create a completed Trace with a random `otel_trace_id`; override any column."""
    if version is None:
        version = await make_agent_version(session)
    values: dict[str, Any] = {
        "otel_trace_id": secrets.token_hex(16),
        "status": "completed",
        "input": "What's the weather?",
        "output": "Sunny.",
        "ended_at": datetime.now(UTC),
        **fields,
    }
    trace = Trace(agent_version_id=version.id, **values)
    session.add(trace)
    await session.flush()
    return trace


def span_record(**overrides: Any) -> SpanRecord:
    """A valid root `chain` span; override any field."""
    base = SpanRecord(
        otel_trace_id=DEFAULT_TRACE_ID,
        otel_span_id=DEFAULT_SPAN_ID,
        parent_otel_span_id=None,
        name="agent.run",
        started_at=datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC),
        ended_at=datetime(2026, 11, 19, 12, 0, 1, tzinfo=UTC),
        status="ok",
        status_message=None,
        attributes={"agentops.span.kind": "chain"},
        events=[],
    )
    return replace(base, **overrides)
