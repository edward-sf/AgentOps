"""Run one input through an agent and store the trace (spec 6.2, D7, D8, D10).

A failed run is data: it returns a `failed` outcome with a stored trace. Only an
unexpected exception (a bug) propagates, and then nothing is stored.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from langsmith import tracing_context
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import AgentConfig
from app.agents.graph import build_graph, initial_state, recursion_limit
from app.agents.providers.base import Provider, ProviderFailureKind
from app.observability import attributes as attr
from app.observability.ingest import ingest_spans
from app.observability.tracing import RunTracer

ROOT_SPAN_NAME = "agent.run"


@dataclass(frozen=True)
class RunOutcome:
    trace_id: uuid.UUID
    status: Literal["completed", "failed"]
    output: str | None = None
    """The final answer of a `completed` run (also stored as `Trace.output`)."""
    error: str | None = None
    """Why a `failed` run failed (also stored as `Trace.error`)."""
    provider_failure: ProviderFailureKind | None = None
    """Set only when the run failed because of a `ProviderAPIError` with this `kind` (D10)."""


async def run_agent(
    *,
    session: AsyncSession,
    provider: Provider,
    config: AgentConfig,
    input: str,
    evaluation_run_id: uuid.UUID | None = None,
    test_case_id: uuid.UUID | None = None,
) -> RunOutcome:
    """Run the agent on `input`, then store its spans through `ingest_spans`.

    `evaluation_run_id` and `test_case_id`, when given, are set on the root span, which is
    how the stored trace is linked to an evaluation (M2 spec 6.1). Flushes; never commits.
    """
    with RunTracer() as run_tracer:
        graph = build_graph(provider, config, run_tracer.tracer)
        with run_tracer.tracer.start_as_current_span(ROOT_SPAN_NAME) as root:
            root.set_attributes(
                {
                    attr.SPAN_KIND: "chain",
                    attr.AGENT_VERSION_ID: str(config.agent_version_id),
                    attr.INPUT: json.dumps(input),
                }
            )
            if evaluation_run_id is not None:
                root.set_attribute(attr.EVALUATION_RUN_ID, str(evaluation_run_id))
            if test_case_id is not None:
                root.set_attribute(attr.TEST_CASE_ID, str(test_case_id))
            # D6: our spans are the only instrumentation. LangSmith env vars
            # (LANGSMITH_TRACING, LANGCHAIN_TRACING_V2) must never send run data anywhere.
            with tracing_context(enabled=False):
                state = await graph.ainvoke(
                    initial_state(input),
                    config={"recursion_limit": recursion_limit(config.params.max_steps)},
                )
            failure = state["failure"]
            if failure is not None:
                root.set_attribute(attr.ERROR, failure)
                root.set_status(Status(StatusCode.ERROR, failure))
            else:
                root.set_attribute(attr.OUTPUT, json.dumps(state["final_text"]))
                root.set_status(Status(StatusCode.OK))
        records = run_tracer.records()
    (trace_id,) = await ingest_spans(session, records)
    if failure is not None:
        return RunOutcome(
            trace_id=trace_id,
            status="failed",
            error=failure,
            provider_failure=state["provider_failure"],
        )
    return RunOutcome(trace_id=trace_id, status="completed", output=state["final_text"])
