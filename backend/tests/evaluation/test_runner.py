"""The evaluation runner (M2 spec 6.2, D3-D10). Every model call is scripted.

Data really commits (see `committed_session_factory`). The concurrency, cancel and abort
tests synchronise on events, not sleeps, so they don't depend on timing.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.providers import ProviderNotConfiguredError, UnsupportedProviderError
from app.agents.providers.base import ModelRequest, ModelResponse, ProviderAPIError, UserTurn
from app.evaluation import queue
from app.evaluation.runner import execute_run
from app.models import AgentVersion, Dataset, EvaluationResult, EvaluationRun, TestCase, Trace
from tests.agents.fakes import ScriptedProvider, end_turn, response
from tests.factories import make_agent_version, make_dataset, make_evaluation_run, make_trace

Factory = async_sessionmaker[AsyncSession]
STALE = timedelta(seconds=60)
WAIT = 10.0
"""Seconds a test waits on an event before failing instead of hanging."""

Step = ModelResponse | Exception | Callable[[], Awaitable[ModelResponse]]


class ByInput:
    """Answers each case from its own `ScriptedProvider`, chosen by the case's input.

    A script entry may also be an async callable, awaited in place of a response, so a test
    can hold a case in flight. Inputs without a script answer `end_turn("ok <input>")`.
    """

    name = "anthropic"

    def __init__(self, scripts: dict[str, list[Step]] | None = None) -> None:
        self._scripts = dict(scripts or {})
        self._providers: dict[str, ScriptedProvider] = {}
        self.inputs: list[str] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        first = request.turns[0]
        assert isinstance(first, UserTurn)
        self.inputs.append(first.text)
        script = self._scripts.get(first.text, [end_turn(f"ok {first.text}")])
        if first.text not in self._providers:
            steps = [s for s in script if not callable(s)]
            self._providers[first.text] = ScriptedProvider(steps)
        hold = next((s for s in script if callable(s)), None)
        if hold is not None:
            return await hold()
        return await self._providers[first.text].complete(request)


def question(n: int) -> str:
    """The input `make_dataset` gives case `n`."""
    return f"Question {n}?"


async def setup_run(
    factory: Factory,
    *,
    cases: int = 3,
    version_fields: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit a version, a dataset and a pending run, then claim it. Returns (run, dataset)."""
    async with factory() as s:
        version = await make_agent_version(s, **(version_fields or {}))
        dataset = await make_dataset(s, cases=cases)
        run = await make_evaluation_run(s, version=version, dataset=dataset)
        await s.commit()
        run_id, dataset_id = run.id, dataset.id
    async with factory() as s:
        assert await queue.claim_next(s, stale_after=STALE) == run_id
    return run_id, dataset_id


async def execute(
    factory: Factory,
    run_id: uuid.UUID,
    provider: Any,
    *,
    concurrency: int = 2,
    stale_after: timedelta = STALE,
) -> None:
    await asyncio.wait_for(
        execute_run(
            run_id,
            session_factory=factory,
            provider_factory=lambda name: provider,
            concurrency=concurrency,
            stale_after=stale_after,
        ),
        timeout=WAIT,
    )


async def load_run(factory: Factory, run_id: uuid.UUID) -> EvaluationRun:
    async with factory() as s:
        run = await s.get(EvaluationRun, run_id)
        assert run is not None
        return run


async def results_by_key(factory: Factory, run_id: uuid.UUID) -> dict[str, EvaluationResult]:
    async with factory() as s:
        rows = await s.scalars(
            select(EvaluationResult).where(EvaluationResult.evaluation_run_id == run_id)
        )
        return {r.case_key: r for r in rows}


async def cases_by_key(factory: Factory, dataset_id: uuid.UUID) -> dict[str, TestCase]:
    async with factory() as s:
        rows = await s.scalars(select(TestCase).where(TestCase.dataset_id == dataset_id))
        return {c.key: c for c in rows}


async def set_cancel(factory: Factory, run_id: uuid.UUID) -> None:
    async with factory() as s:
        await s.execute(
            update(EvaluationRun).where(EvaluationRun.id == run_id).values(cancel_requested=True)
        )
        await s.commit()


class Beats:
    """Counts `queue.heartbeat` calls, so a test can wait for the runner to see a change."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.started = 0
        self._returned: defaultdict[int, asyncio.Event] = defaultdict(asyncio.Event)
        real = queue.heartbeat

        async def counted(session: AsyncSession, run_id: uuid.UUID) -> bool:
            n = self.started
            self.started += 1
            try:
                return await real(session, run_id)
            finally:
                self._returned[n].set()

        monkeypatch.setattr(queue, "heartbeat", counted)

    async def next(self) -> None:
        """Wait for a heartbeat that starts after this call, and has returned. The runner
        acts on what that beat saw before any other task runs."""
        await asyncio.wait_for(self._returned[self.started].wait(), WAIT)


# --- outcomes -------------------------------------------------------------------------------


async def test_all_cases_completed(committed_session_factory: Factory) -> None:
    run_id, dataset_id = await setup_run(committed_session_factory)

    await execute(committed_session_factory, run_id, ByInput())

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "completed"
    assert run.error is None
    assert run.completed_at is not None
    assert run.total_cases == 3
    results = await results_by_key(committed_session_factory, run_id)
    cases = await cases_by_key(committed_session_factory, dataset_id)
    assert sorted(results) == ["case_001", "case_002", "case_003"]
    async with committed_session_factory() as s:
        for key, result in results.items():
            case = cases[key]
            assert result.status == "completed"
            assert result.error is None
            assert result.output == f"ok {case.input}"
            assert result.test_case_id == case.id
            assert result.case_input == case.input
            assert result.case_expected_behavior == case.expected_behavior
            assert result.case_expected_output == case.expected_output
            assert result.case_tags == case.tags
            assert result.trace_id is not None
            trace = await s.get(Trace, result.trace_id)
            assert trace is not None
            assert trace.evaluation_run_id == run_id
            assert trace.test_case_id == case.id
            assert trace.output == result.output


async def test_agent_failure_is_failed_result(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory)
    provider = ByInput({question(2): [response("max_tokens", text="It is")]})

    await execute(committed_session_factory, run_id, provider)

    assert (await load_run(committed_session_factory, run_id)).status == "completed"
    results = await results_by_key(committed_session_factory, run_id)
    failed = results["case_002"]
    assert failed.status == "failed"
    assert failed.trace_id is not None
    assert failed.error == "answer truncated at max_tokens"
    assert failed.output is None
    assert {results[k].status for k in ("case_001", "case_003")} == {"completed"}


async def test_unavailable_is_error_and_continues(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory)
    overloaded = ProviderAPIError("overloaded", kind="unavailable")
    provider = ByInput({question(2): [overloaded]})

    await execute(committed_session_factory, run_id, provider)

    assert (await load_run(committed_session_factory, run_id)).status == "completed"
    results = await results_by_key(committed_session_factory, run_id)
    error = results["case_002"]
    assert error.status == "error"
    assert error.trace_id is not None
    assert error.error is not None and "overloaded" in error.error
    assert {results[k].status for k in ("case_001", "case_003")} == {"completed"}


async def test_request_rejected_is_failed_result(committed_session_factory: Factory) -> None:
    """A `ProviderAPIError` with no `kind` (a 400, say) is the agent's failure (D10)."""
    run_id, _ = await setup_run(committed_session_factory)
    provider = ByInput({question(1): [ProviderAPIError("bad request")]})

    await execute(committed_session_factory, run_id, provider)

    assert (await load_run(committed_session_factory, run_id)).status == "completed"
    result = (await results_by_key(committed_session_factory, run_id))["case_001"]
    assert result.status == "failed"
    assert result.trace_id is not None


async def test_account_failure_aborts(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory, cases=10)
    rejected = ProviderAPIError("invalid x-api-key", kind="account")
    provider = ByInput({question(2): [rejected]})

    await execute(committed_session_factory, run_id, provider, concurrency=1)

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "failed"
    assert run.error is not None and "invalid x-api-key" in run.error
    results = await results_by_key(committed_session_factory, run_id)
    assert sorted(results) == ["case_001", "case_002"]
    assert results["case_002"].status == "error"
    assert results["case_002"].trace_id is not None
    assert run.error == results["case_002"].error
    assert provider.inputs == [question(1), question(2)]


async def test_account_failure_lets_in_flight_cases_finish(
    committed_session_factory: Factory,
) -> None:
    """Review focus 3: a 401 on the first case of 25. No new case starts, and the cases
    already in flight still store their results."""
    run_id, _ = await setup_run(committed_session_factory, cases=25)
    aborted = asyncio.Event()

    async def held() -> ModelResponse:
        await asyncio.wait_for(aborted.wait(), WAIT)
        return end_turn("held")

    rejected = ProviderAPIError("invalid x-api-key", kind="account")
    provider = ByInput(
        {question(1): [rejected], question(2): [held], question(3): [held]},
    )

    async def release_when_first_result_stored() -> None:
        # Cases 2 and 3 are in flight; let them answer only after case 1's error is stored,
        # which is after the runner has seen the account failure. The commit happens in the
        # runner, so polling the database is the only way to observe it from here.
        while "case_001" not in await results_by_key(committed_session_factory, run_id):  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        aborted.set()

    releaser = asyncio.create_task(release_when_first_result_stored())
    await execute(committed_session_factory, run_id, provider, concurrency=3)
    await releaser

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "failed"
    assert run.error is not None and "invalid x-api-key" in run.error
    assert run.total_cases == 25
    results = await results_by_key(committed_session_factory, run_id)
    assert sorted(results) == ["case_001", "case_002", "case_003"]
    assert results["case_001"].status == "error"
    assert results["case_002"].status == results["case_003"].status == "completed"
    assert sorted(provider.inputs) == [question(1), question(2), question(3)]


async def test_unexpected_exception_is_error_without_trace(
    committed_session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    run_id, dataset_id = await setup_run(committed_session_factory)
    provider = ByInput({question(2): [RuntimeError("boom")]})

    with caplog.at_level(logging.ERROR, logger="app.evaluation"):
        await execute(committed_session_factory, run_id, provider)

    assert (await load_run(committed_session_factory, run_id)).status == "completed"
    results = await results_by_key(committed_session_factory, run_id)
    error = results["case_002"]
    assert error.status == "error"
    assert error.trace_id is None
    assert error.error == "RuntimeError: boom"
    assert error.case_input == question(2)
    assert {results[k].status for k in ("case_001", "case_003")} == {"completed"}
    case = (await cases_by_key(committed_session_factory, dataset_id))["case_002"]
    async with committed_session_factory() as s:
        orphans = await s.scalar(
            select(func.count()).select_from(Trace).where(Trace.test_case_id == case.id)
        )
    assert orphans == 0
    logged = [r for r in caplog.records if r.name == "app.evaluation" and r.exc_info]
    assert len(logged) == 1
    exc_info = logged[0].exc_info
    assert exc_info is not None and isinstance(exc_info[1], RuntimeError)


# --- concurrency ----------------------------------------------------------------------------


async def test_concurrency_limit(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory, cases=8)
    in_flight = 0
    peak = 0
    full = asyncio.Event()

    async def counted() -> ModelResponse:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        if in_flight == 3:
            full.set()
        try:
            # Hold every call until three are in flight at once, so a fourth would show.
            await asyncio.wait_for(full.wait(), WAIT)
            await asyncio.sleep(0)
            return end_turn("ok")
        finally:
            in_flight -= 1

    provider = ByInput({question(n): [counted] for n in range(1, 9)})

    await execute(committed_session_factory, run_id, provider, concurrency=3)

    assert peak == 3
    assert len(await results_by_key(committed_session_factory, run_id)) == 8
    assert (await load_run(committed_session_factory, run_id)).status == "completed"


# --- resume, snapshots, deleted cases -------------------------------------------------------


async def make_stale_run(
    factory: Factory, *, cases: int = 3, done: tuple[int, ...] = (1,)
) -> tuple[uuid.UUID, uuid.UUID, AgentVersion, Dataset]:
    """A run whose worker died: `running`, `total_cases` set, results for `done` cases and a
    heartbeat two minutes old. Returns (run id, dataset id, version, dataset)."""
    async with factory() as s:
        version = await make_agent_version(s)
        dataset = await make_dataset(s, cases=cases)
        run = await make_evaluation_run(
            s,
            version=version,
            dataset=dataset,
            status="running",
            started_at=func.now(),
            heartbeat_at=func.now() - timedelta(seconds=120),
            total_cases=cases,
        )
        by_key = {
            c.key: c
            for c in await s.scalars(select(TestCase).where(TestCase.dataset_id == dataset.id))
        }
        for n in done:
            case = by_key[f"case_{n:03d}"]
            trace = await make_trace(
                s, version=version, evaluation_run_id=run.id, test_case_id=case.id
            )
            s.add(
                EvaluationResult(
                    evaluation_run_id=run.id,
                    test_case_id=case.id,
                    trace_id=trace.id,
                    status="completed",
                    output="before the crash",
                    case_key=case.key,
                    case_input=case.input,
                    case_expected_behavior=case.expected_behavior,
                    case_expected_output=case.expected_output,
                    case_tags=case.tags,
                )
            )
        await s.commit()
        return run.id, dataset.id, version, dataset


async def test_resume_skips_finished_cases(committed_session_factory: Factory) -> None:
    """Review focus 2: a reclaimed run runs only the missing cases."""
    run_id, _, _, _ = await make_stale_run(committed_session_factory)
    before = (await results_by_key(committed_session_factory, run_id))["case_001"]
    async with committed_session_factory() as s:
        assert await queue.claim_next(s, stale_after=STALE) == run_id
    provider = ByInput()

    await execute(committed_session_factory, run_id, provider)

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "completed"
    assert run.total_cases == 3
    results = await results_by_key(committed_session_factory, run_id)
    assert sorted(results) == ["case_001", "case_002", "case_003"]
    after = results["case_001"]
    assert (after.id, after.output, after.trace_id, after.created_at) == (
        before.id,
        "before the crash",
        before.trace_id,
        before.created_at,
    )
    assert sorted(provider.inputs) == [question(2), question(3)]


async def test_resume_ignores_cases_added_after_start(committed_session_factory: Factory) -> None:
    """D8: the case set is fixed when the run starts executing."""
    run_id, _, _, dataset = await make_stale_run(committed_session_factory)
    async with committed_session_factory() as s:
        s.add(
            TestCase(
                dataset_id=dataset.id,
                key="case_004",
                input="Added later?",
                expected_behavior=["x"],
                created_at=func.now() + timedelta(seconds=1),
            )
        )
        await s.commit()
        assert await queue.claim_next(s, stale_after=STALE) == run_id

    await execute(committed_session_factory, run_id, ByInput())

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "completed"
    assert run.total_cases == 3
    assert sorted(await results_by_key(committed_session_factory, run_id)) == [
        "case_001",
        "case_002",
        "case_003",
    ]


async def test_deleted_unrun_case_adjusts_total(committed_session_factory: Factory) -> None:
    run_id, dataset_id, _, _ = await make_stale_run(committed_session_factory)
    async with committed_session_factory() as s:
        await s.execute(
            delete(TestCase).where(TestCase.dataset_id == dataset_id, TestCase.key == "case_003")
        )
        await s.commit()
        assert await queue.claim_next(s, stale_after=STALE) == run_id

    await execute(committed_session_factory, run_id, ByInput())

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "completed"
    assert run.total_cases == 2
    assert sorted(await results_by_key(committed_session_factory, run_id)) == [
        "case_001",
        "case_002",
    ]


async def test_snapshot_survives_edit(committed_session_factory: Factory) -> None:
    """Review focus 4: editing a case after its run leaves the result's snapshot alone."""
    run_id, dataset_id = await setup_run(committed_session_factory)
    await execute(committed_session_factory, run_id, ByInput())

    async with committed_session_factory() as s:
        await s.execute(
            update(TestCase)
            .where(TestCase.dataset_id == dataset_id, TestCase.key == "case_001")
            .values(input="Edited?", expected_behavior=["something else"], tags=["new"])
        )
        await s.commit()

    result = (await results_by_key(committed_session_factory, run_id))["case_001"]
    assert result.case_input == question(1)
    assert result.case_expected_behavior == ["does the thing"]
    assert result.case_tags == []


# --- cancel ---------------------------------------------------------------------------------


async def test_cancel_during_run(
    committed_session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, _ = await setup_run(committed_session_factory, cases=6)
    beats = Beats(monkeypatch)

    async def cancel_then_answer() -> ModelResponse:
        # The first case has finished. Request a cancel, and stay in flight until a timer
        # heartbeat has seen it.
        await set_cancel(committed_session_factory, run_id)
        await beats.next()
        return end_turn("in flight")

    provider = ByInput({question(2): [cancel_then_answer]})

    await execute(
        committed_session_factory,
        run_id,
        provider,
        concurrency=1,
        stale_after=timedelta(milliseconds=150),
    )

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "cancelled"
    assert run.completed_at is not None
    results = await results_by_key(committed_session_factory, run_id)
    assert sorted(results) == ["case_001", "case_002"]
    assert results["case_002"].output == "in flight"


async def test_cancel_before_first_case(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory)
    await set_cancel(committed_session_factory, run_id)
    provider = ByInput()

    await execute(committed_session_factory, run_id, provider)

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "cancelled"
    assert run.total_cases == 3
    assert await results_by_key(committed_session_factory, run_id) == {}
    assert provider.inputs == []


# --- heartbeat ------------------------------------------------------------------------------


async def test_heartbeat_runs_during_a_long_case(
    committed_session_factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The heartbeat is a timer, so a case that outlasts `stale_after` doesn't make the run
    reclaimable."""
    stale_after = timedelta(milliseconds=150)
    run_id, _ = await setup_run(committed_session_factory, cases=1)
    beats = Beats(monkeypatch)
    reclaimed: list[uuid.UUID | None] = []

    async def long_case() -> ModelResponse:
        # Four timer heartbeats at stale_after / 3: the case has now run for longer than
        # stale_after, and the run's heartbeat is still fresh.
        for _ in range(4):
            await beats.next()
        async with committed_session_factory() as s:
            reclaimed.append(await queue.claim_next(s, stale_after=stale_after))
        return end_turn("slow")

    provider = ByInput({question(1): [long_case]})

    await execute(committed_session_factory, run_id, provider, stale_after=stale_after)

    assert reclaimed == [None]
    assert (await load_run(committed_session_factory, run_id)).status == "completed"


# --- configuration errors -------------------------------------------------------------------


def raising(exc: Exception) -> Callable[[str], Any]:
    def factory(name: str) -> Any:
        raise exc

    return factory


@pytest.mark.parametrize(
    ("version_fields", "factory_error", "message"),
    [
        pytest.param(
            {"parameters": {"temperature": 0.5}}, None, "temperature", id="invalid_parameters"
        ),
        pytest.param(
            {"provider": "openai"},
            UnsupportedProviderError("openai"),
            "unsupported provider: openai",
            id="unsupported_provider",
        ),
    ],
)
async def test_configuration_error_fails_run(
    committed_session_factory: Factory,
    version_fields: dict[str, Any],
    factory_error: Exception | None,
    message: str,
) -> None:
    run_id, _ = await setup_run(committed_session_factory, version_fields=version_fields)
    provider = ByInput()
    provider_factory = raising(factory_error) if factory_error else (lambda name: provider)

    await asyncio.wait_for(
        execute_run(
            run_id,
            session_factory=committed_session_factory,
            provider_factory=provider_factory,
            concurrency=2,
            stale_after=STALE,
        ),
        WAIT,
    )

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "failed"
    assert run.error is not None and message in run.error
    assert run.completed_at is not None
    assert await results_by_key(committed_session_factory, run_id) == {}
    assert provider.inputs == []


async def test_missing_key_fails_run(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory)

    await asyncio.wait_for(
        execute_run(
            run_id,
            session_factory=committed_session_factory,
            provider_factory=raising(ProviderNotConfiguredError("ANTHROPIC_API_KEY is not set")),
            concurrency=2,
            stale_after=STALE,
        ),
        WAIT,
    )

    run = await load_run(committed_session_factory, run_id)
    assert (run.status, run.error) == ("failed", "ANTHROPIC_API_KEY is not set")
    assert await results_by_key(committed_session_factory, run_id) == {}


async def test_unknown_tool_fails_run(committed_session_factory: Factory) -> None:
    run_id, _ = await setup_run(committed_session_factory, version_fields={"tools": ["nope"]})
    provider = ByInput()

    await execute(committed_session_factory, run_id, provider)

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "failed"
    assert run.error is not None and "nope" in run.error
    assert await results_by_key(committed_session_factory, run_id) == {}
    assert provider.inputs == []


# --- a run lost to another worker -----------------------------------------------------------


async def test_run_finished_elsewhere_stops_and_keeps_its_outcome(
    committed_session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Another worker reclaimed the run and finished it while this one was mid-case. The
    heartbeat notices: no new case starts, the in-flight one stores its result, and the
    other worker's outcome stands."""
    run_id, _ = await setup_run(committed_session_factory, cases=3)
    beats = Beats(monkeypatch)

    async def finished_elsewhere() -> ModelResponse:
        async with committed_session_factory() as s:
            await queue.finish(s, run_id, "failed", "finished by another worker")
        await beats.next()
        return end_turn("in flight")

    provider = ByInput({question(1): [finished_elsewhere]})

    with caplog.at_level(logging.WARNING, logger="app.evaluation"):
        await execute(
            committed_session_factory,
            run_id,
            provider,
            concurrency=1,
            stale_after=timedelta(milliseconds=150),
        )

    run = await load_run(committed_session_factory, run_id)
    assert (run.status, run.error) == ("failed", "finished by another worker")
    assert sorted(await results_by_key(committed_session_factory, run_id)) == ["case_001"]
    assert provider.inputs == [question(1)]
    assert any(r.name == "app.evaluation" and r.levelno == logging.WARNING for r in caplog.records)


async def test_finish_after_run_ended_elsewhere_is_a_warning(
    committed_session_factory: Factory, caplog: pytest.LogCaptureFixture
) -> None:
    """`finish` refuses a run that is no longer `running`: the runner logs and returns."""
    run_id, _ = await setup_run(committed_session_factory, cases=1)

    async def finished_elsewhere() -> ModelResponse:
        async with committed_session_factory() as s:
            await queue.finish(s, run_id, "cancelled")
        return end_turn("done")

    provider = ByInput({question(1): [finished_elsewhere]})

    with caplog.at_level(logging.WARNING, logger="app.evaluation"):
        await execute(committed_session_factory, run_id, provider)

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "cancelled"
    assert any(r.name == "app.evaluation" and r.levelno == logging.WARNING for r in caplog.records)


async def test_heartbeat_lookup_error_stops_run(
    committed_session_factory: Factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`heartbeat` raises `LookupError` for a run that's gone: stop, don't finish, don't crash."""
    run_id, _ = await setup_run(committed_session_factory, cases=3)
    real = queue.heartbeat
    gone = asyncio.Event()

    async def heartbeat(session: AsyncSession, run: uuid.UUID) -> bool:
        if gone.is_set():
            gone_seen.set()
            raise LookupError(f"evaluation run {run} not found")
        return await real(session, run)

    gone_seen = asyncio.Event()
    monkeypatch.setattr(queue, "heartbeat", heartbeat)

    async def vanish() -> ModelResponse:
        gone.set()
        await asyncio.wait_for(gone_seen.wait(), WAIT)
        return end_turn("in flight")

    provider = ByInput({question(1): [vanish]})

    with caplog.at_level(logging.WARNING, logger="app.evaluation"):
        await execute(
            committed_session_factory,
            run_id,
            provider,
            concurrency=1,
            stale_after=timedelta(milliseconds=150),
        )

    run = await load_run(committed_session_factory, run_id)
    assert run.status == "running"
    assert sorted(await results_by_key(committed_session_factory, run_id)) == ["case_001"]
    assert provider.inputs == [question(1)]
    assert any(r.name == "app.evaluation" and r.levelno == logging.WARNING for r in caplog.records)
