"""Shared builders for tests. Later tasks reuse these; keep signatures stable."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Agent, AgentVersion
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
        model="claude-sonnet-4-5",
        system_prompt="You are a helpful assistant.",
        parameters=dict(parameters) if parameters is not None else {"max_tokens": 1024},
        tools=list(tools),
    )
    session.add(version)
    await session.flush()
    return version


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
