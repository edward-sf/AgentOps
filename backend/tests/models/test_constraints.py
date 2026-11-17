import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Agent,
    AgentVersion,
    Dataset,
    EvaluationResult,
    EvaluationRun,
    Metric,
    Span,
    TestCase,
    Trace,
)


def uid() -> str:
    return uuid.uuid4().hex[:8]


async def make_agent(s: AsyncSession, name: str | None = None) -> Agent:
    agent = Agent(name=name or f"agent-{uid()}")
    s.add(agent)
    await s.flush()
    return agent


async def make_version(s: AsyncSession, agent: Agent, version: int = 1) -> AgentVersion:
    v = AgentVersion(
        agent_id=agent.id, version=version, provider="anthropic", model="m", system_prompt="p"
    )
    s.add(v)
    await s.flush()
    return v


async def make_dataset(s: AsyncSession) -> Dataset:
    ds = Dataset(name=f"ds-{uid()}")
    s.add(ds)
    await s.flush()
    return ds


async def make_case(s: AsyncSession, ds: Dataset, key: str = "case_001") -> TestCase:
    tc = TestCase(dataset_id=ds.id, key=key, input="hi")
    s.add(tc)
    await s.flush()
    return tc


async def count(s: AsyncSession, model: type) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_agent_name_unique(db_session: AsyncSession) -> None:
    await make_agent(db_session, "a")
    with pytest.raises(IntegrityError, match="uq_agents_name"):
        async with db_session.begin_nested():
            db_session.add(Agent(name="a"))
            await db_session.flush()


async def test_agent_version_unique_per_agent(db_session: AsyncSession) -> None:
    a1 = await make_agent(db_session)
    a2 = await make_agent(db_session)
    await make_version(db_session, a1, 1)
    await make_version(db_session, a2, 1)
    with pytest.raises(IntegrityError, match="uq_agent_versions_agent_id_version"):
        async with db_session.begin_nested():
            await make_version(db_session, a1, 1)


async def test_provider_check_rejects_unknown(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                AgentVersion(
                    agent_id=agent.id,
                    version=1,
                    provider="mistral",
                    model="m",
                    system_prompt="p",
                )
            )
            await db_session.flush()


async def test_deleting_agent_cascades_versions(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    await make_version(db_session, agent)
    assert await count(db_session, AgentVersion) == 1
    await db_session.execute(delete(Agent).where(Agent.id == agent.id))
    assert await count(db_session, AgentVersion) == 0


async def test_deleting_version_referenced_by_run_is_restricted(
    db_session: AsyncSession,
) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    ds = await make_dataset(db_session)
    db_session.add(EvaluationRun(agent_version_id=version.id, dataset_id=ds.id))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(delete(Agent).where(Agent.id == agent.id))


async def test_deleting_test_case_referenced_by_result_is_restricted(
    db_session: AsyncSession,
) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    ds = await make_dataset(db_session)
    case = await make_case(db_session, ds)
    run = EvaluationRun(agent_version_id=version.id, dataset_id=ds.id)
    db_session.add(run)
    await db_session.flush()
    db_session.add(EvaluationResult(evaluation_run_id=run.id, test_case_id=case.id, passed=True))
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(delete(TestCase).where(TestCase.id == case.id))


async def test_deleting_trace_cascades_spans_and_metrics(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    trace = Trace(otel_trace_id=uid(), agent_version_id=version.id)
    db_session.add(trace)
    await db_session.flush()
    parent = Span(trace_id=trace.id, otel_span_id="s1", name="root", kind="chain")
    db_session.add(parent)
    await db_session.flush()
    db_session.add(Span(trace_id=trace.id, otel_span_id="s2", parent_span_id=parent.id, name="c"))
    db_session.add(Metric(trace_id=trace.id, name="latency_ms", value=12.5, unit="ms"))
    await db_session.flush()
    await db_session.execute(delete(Trace).where(Trace.id == trace.id))
    assert await count(db_session, Span) == 0
    assert await count(db_session, Metric) == 0


async def test_test_case_key_unique_per_dataset(db_session: AsyncSession) -> None:
    ds1 = await make_dataset(db_session)
    ds2 = await make_dataset(db_session)
    await make_case(db_session, ds1)
    await make_case(db_session, ds2)
    with pytest.raises(IntegrityError, match="uq_test_cases_dataset_id_key"):
        async with db_session.begin_nested():
            await make_case(db_session, ds1)
