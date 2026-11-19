import uuid

import pytest
from sqlalchemy import delete, func, select, text, update
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
    with pytest.raises(IntegrityError, match="ck_agent_versions_provider"):
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
    with pytest.raises(IntegrityError, match="fk_evaluation_runs_agent_version_id_agent_versions"):
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
    with pytest.raises(IntegrityError, match="fk_evaluation_results_test_case_id_test_cases"):
        async with db_session.begin_nested():
            await db_session.execute(delete(TestCase).where(TestCase.id == case.id))


async def test_deleting_trace_cascades_spans_and_metrics(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    trace = Trace(otel_trace_id=uid(), agent_version_id=version.id)
    db_session.add(trace)
    await db_session.flush()
    db_session.add(Span(trace_id=trace.id, otel_span_id="s1", name="root", kind="chain"))
    db_session.add(Span(trace_id=trace.id, otel_span_id="s2", parent_otel_span_id="s1", name="c"))
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


async def test_deleting_dataset_cascades_test_cases(db_session: AsyncSession) -> None:
    ds = await make_dataset(db_session)
    await make_case(db_session, ds)
    assert await count(db_session, TestCase) == 1
    await db_session.execute(delete(Dataset).where(Dataset.id == ds.id))
    assert await count(db_session, TestCase) == 0


async def test_deleting_test_case_referenced_by_trace_is_restricted(
    db_session: AsyncSession,
) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    ds = await make_dataset(db_session)
    case = await make_case(db_session, ds)
    db_session.add(Trace(otel_trace_id=uid(), agent_version_id=version.id, test_case_id=case.id))
    await db_session.flush()
    with pytest.raises(IntegrityError, match="fk_traces_test_case_id_test_cases"):
        async with db_session.begin_nested():
            await db_session.execute(delete(TestCase).where(TestCase.id == case.id))


async def test_span_kind_check_rejects_unknown(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    trace = Trace(otel_trace_id=uid(), agent_version_id=version.id)
    db_session.add(trace)
    await db_session.flush()
    with pytest.raises(IntegrityError, match="ck_spans_kind"):
        async with db_session.begin_nested():
            db_session.add(Span(trace_id=trace.id, otel_span_id="s1", name="n", kind="bogus"))
            await db_session.flush()


async def test_trace_status_check_rejects_unknown(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    with pytest.raises(IntegrityError, match="ck_traces_status"):
        async with db_session.begin_nested():
            db_session.add(
                Trace(otel_trace_id=uid(), agent_version_id=version.id, status="cancelled")
            )
            await db_session.flush()


async def test_run_status_check_rejects_unknown(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    version = await make_version(db_session, agent)
    ds = await make_dataset(db_session)
    with pytest.raises(IntegrityError, match="ck_evaluation_runs_status"):
        async with db_session.begin_nested():
            db_session.add(
                EvaluationRun(agent_version_id=version.id, dataset_id=ds.id, status="bogus")
            )
            await db_session.flush()


async def test_updated_at_is_advanced_by_database_trigger(db_session: AsyncSession) -> None:
    agent = await make_agent(db_session)
    before = (
        await db_session.execute(select(Agent.updated_at).where(Agent.id == agent.id))
    ).scalar_one()
    await db_session.execute(
        update(Agent).where(Agent.id == agent.id).values(description="changed")
    )
    after = (
        await db_session.execute(select(Agent.updated_at).where(Agent.id == agent.id))
    ).scalar_one()
    assert after > before


@pytest.mark.parametrize(
    ("table", "column", "referred", "ondelete"),
    [
        ("agent_versions", "agent_id", "agents", "c"),
        ("test_cases", "dataset_id", "datasets", "c"),
        ("evaluation_runs", "agent_version_id", "agent_versions", "r"),
        ("evaluation_runs", "dataset_id", "datasets", "r"),
        ("traces", "agent_version_id", "agent_versions", "r"),
        ("traces", "evaluation_run_id", "evaluation_runs", "r"),
        ("traces", "test_case_id", "test_cases", "r"),
        ("spans", "trace_id", "traces", "c"),
        ("evaluation_results", "evaluation_run_id", "evaluation_runs", "r"),
        ("evaluation_results", "test_case_id", "test_cases", "r"),
        ("evaluation_results", "trace_id", "traces", "r"),
        ("metrics", "trace_id", "traces", "c"),
        ("metrics", "span_id", "spans", "c"),
    ],
)
async def test_foreign_key_delete_rule(
    db_session: AsyncSession, table: str, column: str, referred: str, ondelete: str
) -> None:
    rows = (
        await db_session.execute(
            text(
                """
                SELECT c.confdeltype, c.conname, rt.relname
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_class rt ON rt.oid = c.confrelid
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
                WHERE c.contype = 'f' AND t.relname = :table AND a.attname = :column
                """
            ),
            {"table": table, "column": column},
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].confdeltype == ondelete
    assert rows[0].relname == referred
    assert rows[0].conname == f"fk_{table}_{column}_{referred}"


async def test_no_unexpected_foreign_keys(db_session: AsyncSession) -> None:
    n = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM pg_constraint "
                "WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
            )
        )
    ).scalar_one()
    assert n == 13


async def test_exact_check_and_unique_constraint_names(db_session: AsyncSession) -> None:
    async def names(kind: str) -> set[str]:
        res = await db_session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE contype = :k AND connamespace = 'public'::regnamespace"
            ),
            {"k": kind},
        )
        return {r[0] for r in res}

    assert await names("c") == {
        "ck_agent_versions_provider",
        "ck_evaluation_runs_status",
        "ck_traces_status",
        "ck_spans_kind",
        "ck_spans_status",
    }
    assert await names("u") == {
        "uq_agents_name",
        "uq_agent_versions_agent_id_version",
        "uq_datasets_name",
        "uq_test_cases_dataset_id_key",
        "uq_evaluation_results_run_case",
        "uq_traces_otel_trace_id",
        "uq_spans_trace_id_otel_span_id",
    }
