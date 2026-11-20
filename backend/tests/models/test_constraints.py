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
from tests import factories


def uid() -> str:
    return uuid.uuid4().hex[:8]


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


def snapshot(case: TestCase) -> dict[str, object]:
    """The case-snapshot columns a result must carry (spec D2)."""
    return {
        "case_key": case.key,
        "case_input": case.input,
        "case_expected_behavior": list(case.expected_behavior or []),
        "case_expected_output": case.expected_output,
        "case_tags": list(case.tags or []),
    }


async def result_parents(s: AsyncSession) -> tuple[EvaluationRun, TestCase]:
    dataset = await factories.make_dataset(s, cases=1)
    run = await factories.make_evaluation_run(s, dataset=dataset)
    case = (await s.execute(select(TestCase).where(TestCase.dataset_id == dataset.id))).scalar_one()
    return run, case


async def count(s: AsyncSession, model: type) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_agent_name_unique(db_session: AsyncSession) -> None:
    version = await factories.make_agent_version(db_session)
    agent = await db_session.get(Agent, version.agent_id)
    assert agent is not None
    with pytest.raises(IntegrityError, match="uq_agents_name"):
        async with db_session.begin_nested():
            # Inline: the factory always picks a fresh name; this is the duplicate under test.
            db_session.add(Agent(name=agent.name))
            await db_session.flush()


async def test_agent_version_unique_per_agent(db_session: AsyncSession) -> None:
    v1 = await factories.make_agent_version(db_session)
    await factories.make_agent_version(db_session)  # version 1 of a second agent is fine
    with pytest.raises(IntegrityError, match="uq_agent_versions_agent_id_version"):
        async with db_session.begin_nested():
            # Inline: the factory always makes a new agent; this needs a 2nd row for v1's agent.
            db_session.add(
                AgentVersion(
                    agent_id=v1.agent_id,
                    version=1,
                    provider="anthropic",
                    model="m",
                    system_prompt="p",
                )
            )
            await db_session.flush()


async def test_provider_check_rejects_unknown(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_agent_versions_provider"):
        async with db_session.begin_nested():
            await factories.make_agent_version(db_session, provider="mistral")


async def test_deleting_agent_cascades_versions(db_session: AsyncSession) -> None:
    version = await factories.make_agent_version(db_session)
    assert await count(db_session, AgentVersion) == 1
    await db_session.execute(delete(Agent).where(Agent.id == version.agent_id))
    assert await count(db_session, AgentVersion) == 0


async def test_deleting_version_referenced_by_run_is_restricted(
    db_session: AsyncSession,
) -> None:
    version = await factories.make_agent_version(db_session)
    ds = await make_dataset(db_session)
    db_session.add(EvaluationRun(agent_version_id=version.id, dataset_id=ds.id))
    await db_session.flush()
    with pytest.raises(IntegrityError, match="fk_evaluation_runs_agent_version_id_agent_versions"):
        async with db_session.begin_nested():
            await db_session.execute(delete(Agent).where(Agent.id == version.agent_id))


async def test_deleting_test_case_referenced_by_result_is_restricted(
    db_session: AsyncSession,
) -> None:
    version = await factories.make_agent_version(db_session)
    ds = await make_dataset(db_session)
    case = await make_case(db_session, ds)
    run = EvaluationRun(agent_version_id=version.id, dataset_id=ds.id)
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        EvaluationResult(
            evaluation_run_id=run.id,
            test_case_id=case.id,
            status="error",
            error="boom",
            **snapshot(case),
        )
    )
    await db_session.flush()
    with pytest.raises(IntegrityError, match="fk_evaluation_results_test_case_id_test_cases"):
        async with db_session.begin_nested():
            await db_session.execute(delete(TestCase).where(TestCase.id == case.id))


async def test_deleting_trace_cascades_spans_and_metrics(db_session: AsyncSession) -> None:
    trace = await factories.make_trace(db_session)
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
    ds = await make_dataset(db_session)
    case = await make_case(db_session, ds)
    await factories.make_trace(db_session, test_case_id=case.id)
    with pytest.raises(IntegrityError, match="fk_traces_test_case_id_test_cases"):
        async with db_session.begin_nested():
            await db_session.execute(delete(TestCase).where(TestCase.id == case.id))


async def test_span_kind_check_rejects_unknown(db_session: AsyncSession) -> None:
    trace = await factories.make_trace(db_session)
    with pytest.raises(IntegrityError, match="ck_spans_kind"):
        async with db_session.begin_nested():
            db_session.add(Span(trace_id=trace.id, otel_span_id="s1", name="n", kind="bogus"))
            await db_session.flush()


async def test_trace_status_check_rejects_unknown(db_session: AsyncSession) -> None:
    version = await factories.make_agent_version(db_session)
    with pytest.raises(IntegrityError, match="ck_traces_status"):
        async with db_session.begin_nested():
            await factories.make_trace(db_session, version=version, status="cancelled")


async def test_run_status_check_rejects_unknown(db_session: AsyncSession) -> None:
    version = await factories.make_agent_version(db_session)
    ds = await make_dataset(db_session)
    with pytest.raises(IntegrityError, match="ck_evaluation_runs_status"):
        async with db_session.begin_nested():
            db_session.add(
                EvaluationRun(agent_version_id=version.id, dataset_id=ds.id, status="bogus")
            )
            await db_session.flush()


async def test_result_error_may_omit_trace(db_session: AsyncSession) -> None:
    run, case = await result_parents(db_session)
    result = EvaluationResult(
        evaluation_run_id=run.id,
        test_case_id=case.id,
        status="error",
        error="provider unavailable",
        **snapshot(case),
    )
    db_session.add(result)
    await db_session.flush()
    assert result.trace_id is None
    assert result.passed is None


@pytest.mark.parametrize("status", ["completed", "failed"])
async def test_result_completed_requires_trace(db_session: AsyncSession, status: str) -> None:
    run, case = await result_parents(db_session)
    with pytest.raises(IntegrityError, match="ck_evaluation_results_trace_required"):
        async with db_session.begin_nested():
            db_session.add(
                EvaluationResult(
                    evaluation_run_id=run.id, test_case_id=case.id, status=status, **snapshot(case)
                )
            )
            await db_session.flush()


async def test_result_completed_with_trace_is_stored(db_session: AsyncSession) -> None:
    run, case = await result_parents(db_session)
    trace = await factories.make_trace(db_session, evaluation_run_id=run.id, test_case_id=case.id)
    result = EvaluationResult(
        evaluation_run_id=run.id,
        test_case_id=case.id,
        trace_id=trace.id,
        status="completed",
        output=trace.output,
        **snapshot(case),
    )
    db_session.add(result)
    await db_session.flush()
    await db_session.refresh(result)
    assert result.case_key == "case_001"
    assert result.case_expected_behavior == ["does the thing"]
    assert result.case_tags == []
    assert result.scores == {}


async def test_result_status_check(db_session: AsyncSession) -> None:
    run, case = await result_parents(db_session)
    with pytest.raises(IntegrityError, match="ck_evaluation_results_status"):
        async with db_session.begin_nested():
            db_session.add(
                EvaluationResult(
                    evaluation_run_id=run.id, test_case_id=case.id, status="ok", **snapshot(case)
                )
            )
            await db_session.flush()


async def test_result_passed_nullable(db_session: AsyncSession) -> None:
    run, case = await result_parents(db_session)
    trace = await factories.make_trace(db_session)
    result = EvaluationResult(
        evaluation_run_id=run.id,
        test_case_id=case.id,
        trace_id=trace.id,
        status="failed",
        error="step limit",
        passed=None,
        **snapshot(case),
    )
    db_session.add(result)
    await db_session.flush()
    await db_session.refresh(result)
    assert result.passed is None


async def test_run_queue_column_defaults(db_session: AsyncSession) -> None:
    run = await factories.make_evaluation_run(db_session)
    await db_session.refresh(run)
    assert run.status == "pending"
    assert run.total_cases is None
    assert run.heartbeat_at is None
    assert run.cancel_requested is False


async def test_run_claim_index(db_session: AsyncSession) -> None:
    definition = (
        await db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
            {"name": "ix_evaluation_runs_status_created_at"},
        )
    ).scalar_one()
    assert "(status, created_at)" in definition


def test_status_tuples_match_checks() -> None:
    from app.models.evaluation import RESULT_STATUSES, RUN_STATUSES

    assert RESULT_STATUSES == ("completed", "failed", "error")
    assert RUN_STATUSES == ("pending", "running", "completed", "failed", "cancelled")


async def test_updated_at_is_advanced_by_database_trigger(db_session: AsyncSession) -> None:
    version = await factories.make_agent_version(db_session)
    agent_id = version.agent_id
    before = (
        await db_session.execute(select(Agent.updated_at).where(Agent.id == agent_id))
    ).scalar_one()
    await db_session.execute(
        update(Agent).where(Agent.id == agent_id).values(description="changed")
    )
    after = (
        await db_session.execute(select(Agent.updated_at).where(Agent.id == agent_id))
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
        "ck_evaluation_results_status",
        "ck_evaluation_results_trace_required",
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
