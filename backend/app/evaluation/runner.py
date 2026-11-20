"""Execute one claimed evaluation run (M2 spec 6.2 steps 3-6, D3-D10).

`execute_run` resolves the run's AgentVersion and provider, fixes the case set, and runs the
cases that have no result yet with at most `concurrency` in flight. Each case reads its row,
ends that transaction, calls the model with no transaction open, then stores its trace and
its result in one transaction on its own session.

A heartbeat timer refreshes the run every `stale_after / 3` on a control session of its
own, which no case uses, so a long case never makes a live run look stale. The heartbeat
also sees a cancel request, or that the run is no longer this worker's to finish. Either
stops new cases; cases in flight always finish and store their results.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.config import AgentConfig, InvalidParametersError
from app.agents.providers import ProviderNotConfiguredError, UnsupportedProviderError
from app.agents.providers.base import Provider
from app.agents.runtime import RunOutcome, run_agent
from app.agents.tools import UnknownToolError
from app.api.deps import ProviderFactory
from app.evaluation import queue
from app.models import AgentVersion, EvaluationResult, EvaluationRun, TestCase

logger = logging.getLogger("app.evaluation")

CONFIGURATION_ERRORS = (
    InvalidParametersError,
    UnknownToolError,
    UnsupportedProviderError,
    ProviderNotConfiguredError,
)
"""Errors that fail the whole run before any case starts (D4)."""

DUPLICATE_RESULT = "uq_evaluation_results_run_case"


@dataclass
class _RunState:
    """What the cases and the heartbeat have learned. Any reason set stops new cases."""

    stop: asyncio.Event
    abort_error: str | None = None
    """Set by an account-level provider failure (D10): the run ends `failed`."""
    cancelled: bool = False
    lost: str | None = None
    """Why this worker may no longer finish the run (another worker has it, or it's gone)."""
    deleted: int = 0
    """Cases deleted after the case set was fixed, before they ran (D8)."""

    def abort(self, error: str) -> None:
        if self.abort_error is None:
            self.abort_error = error
        self.stop.set()

    def cancel(self) -> None:
        self.cancelled = True
        self.stop.set()

    def lose(self, reason: str) -> None:
        if self.lost is None:
            self.lost = reason
        self.stop.set()


@dataclass(frozen=True)
class _Context:
    run_id: uuid.UUID
    session_factory: async_sessionmaker[AsyncSession]
    provider: Provider
    config: AgentConfig
    state: _RunState


async def execute_run(
    run_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    provider_factory: ProviderFactory,
    concurrency: int,
    stale_after: timedelta,
) -> None:
    """Execute a run that this worker has just claimed, and finish it.

    The run ends `completed` when every case in `total_cases` has a result, `failed` on a
    configuration error or an account-level provider failure, and `cancelled` on a cancel
    request. If the run is no longer `running` (another worker reclaimed and finished it)
    or has gone, this logs a warning and returns without finishing it.

    Never raises for a case or a provider failure. Raises only if the run row can't be read
    or finished, or a result can't be stored (a bug or a lost database).
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be at least 1 (got {concurrency})")
    async with session_factory() as control:
        # The control session is for the run row only: its reads, the heartbeat and finish.
        # Queue functions use the database's now(), so each starts with no transaction open.
        run = await control.get(EvaluationRun, run_id)
        if run is None:
            raise LookupError(f"evaluation run {run_id} not found")
        if run.status != "running":
            await control.commit()
            logger.warning(
                "evaluation run %s is %s, not running; not executing it", run_id, run.status
            )
            return
        version = await control.get(AgentVersion, run.agent_version_id)
        assert version is not None  # the foreign key is RESTRICT
        try:
            config = AgentConfig.from_version(version)
            provider = provider_factory(config.provider)
        except CONFIGURATION_ERRORS as exc:
            await control.commit()
            logger.warning("evaluation run %s can't start: %s", run_id, exc)
            await _finish(control, run_id, "failed", str(exc))
            return

        pending, total = await _fix_case_set(control, run)
        await control.commit()

        state = _RunState(stop=asyncio.Event())
        # One beat before any case starts: a cancel requested before the claim, or a run
        # that's already gone, starts nothing.
        await _beat(control, run_id, state)
        stop_beating = asyncio.Event()
        beating = asyncio.create_task(
            _heartbeat_loop(control, run_id, state, stale_after / 3, stop_beating)
        )
        try:
            if not state.stop.is_set() and pending:
                context = _Context(run_id, session_factory, provider, config, state)
                cases = iter(pending)
                async with asyncio.TaskGroup() as group:
                    for _ in range(min(concurrency, len(pending))):
                        group.create_task(_take_cases(context, cases))
        finally:
            # Let the loop leave between beats, so the control session is never cancelled
            # mid-transaction.
            stop_beating.set()
            await beating

        if state.lost is not None:
            logger.warning(
                "evaluation run %s: stopped without finishing it: %s", run_id, state.lost
            )
            return
        if state.deleted:
            total -= state.deleted
            await _set_total(control, run_id, total)
        if state.abort_error is not None:
            await _finish(control, run_id, "failed", state.abort_error)
        elif state.cancelled:
            await _finish(control, run_id, "cancelled")
        else:
            stored = await _count_results(control, run_id)
            if stored == total:
                await _finish(control, run_id, "completed")
            else:
                # Every case stores a result or raises, so this is a bug. Never report a
                # run `completed` with missing results.
                await _finish(
                    control, run_id, "failed", f"{total - stored} of {total} cases have no result"
                )


async def _fix_case_set(session: AsyncSession, run: EvaluationRun) -> tuple[list[uuid.UUID], int]:
    """Return the run's cases that have no result, oldest key first, and its total (D8).

    The case set is the dataset's cases that existed when the run started executing, so
    cases added later never join it. A reclaimed run keeps its total, less any not-yet-run
    case that has since been deleted. The total is stored on the run.
    """
    has_result = exists().where(
        EvaluationResult.evaluation_run_id == run.id,
        EvaluationResult.test_case_id == TestCase.id,
    )
    in_set = TestCase.dataset_id == run.dataset_id
    if run.started_at is not None:
        in_set = in_set & (TestCase.created_at <= run.started_at)
    pending = list(
        await session.scalars(select(TestCase.id).where(in_set, ~has_result).order_by(TestCase.key))
    )
    total = await _count_results(session, run.id) + len(pending)
    if run.total_cases is not None and total > run.total_cases:
        logger.warning(
            "evaluation run %s: case set grew from %d to %d since it started",
            run.id,
            run.total_cases,
            total,
        )
    if run.total_cases != total:
        await session.execute(
            update(EvaluationRun)
            .where(EvaluationRun.id == run.id)
            .values(total_cases=total)
            .execution_options(synchronize_session=False)
        )
    return pending, total


async def _take_cases(context: _Context, cases: Iterator[uuid.UUID]) -> None:
    """One of `concurrency` lanes: run cases from the shared iterator until it's empty or
    the run is stopping. Checked before each case, so a stop starts nothing new."""
    for case_id in cases:
        if context.state.stop.is_set():
            return
        await _run_case(context, case_id)


async def _run_case(context: _Context, case_id: uuid.UUID) -> None:
    run_id, state = context.run_id, context.state
    async with context.session_factory() as session:
        case = await session.get(TestCase, case_id)
        if case is None:
            await session.commit()
            state.deleted += 1
            logger.warning("evaluation run %s: case %s was deleted; skipping it", run_id, case_id)
            return
        snapshot = _snapshot(case)
        # End the read transaction: no transaction is open while the model is called.
        await session.commit()
        try:
            outcome = await run_agent(
                session=session,
                provider=context.provider,
                config=context.config,
                input=snapshot["case_input"],
                evaluation_run_id=run_id,
                test_case_id=case_id,
            )
            if outcome.provider_failure == "account":
                state.abort(outcome.error or "provider rejected the account")
            session.add(
                EvaluationResult(
                    evaluation_run_id=run_id,
                    test_case_id=case_id,
                    trace_id=outcome.trace_id,
                    status=_result_status(outcome),
                    output=outcome.output,
                    error=outcome.error,
                    **snapshot,
                )
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            if _is_duplicate(exc):
                state.lose(f"case {snapshot['case_key']} already has a result from another worker")
                return
            logger.exception(
                "evaluation run %s: case %s raised; storing an error result",
                run_id,
                snapshot["case_key"],
            )
            session.add(
                EvaluationResult(
                    evaluation_run_id=run_id,
                    test_case_id=case_id,
                    trace_id=None,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    **snapshot,
                )
            )
            try:
                await session.commit()
            except IntegrityError as dup:
                await session.rollback()
                if not _is_duplicate(dup):
                    raise
                state.lose(f"case {snapshot['case_key']} already has a result from another worker")


def _snapshot(case: TestCase) -> dict[str, Any]:
    """The case as it is now: the result keeps these copies (D2)."""
    return {
        "case_key": case.key,
        "case_input": case.input,
        "case_expected_behavior": list(case.expected_behavior),
        "case_expected_output": case.expected_output,
        "case_tags": list(case.tags),
    }


def _result_status(outcome: RunOutcome) -> Literal["completed", "failed", "error"]:
    """A provider failure says nothing about the agent, so it is `error` (D3, D10)."""
    if outcome.provider_failure is not None:
        return "error"
    return outcome.status


def _is_duplicate(exc: BaseException) -> bool:
    """Another worker stored this case's result first (the run was reclaimed)."""
    if not isinstance(exc, IntegrityError):
        return False
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == DUPLICATE_RESULT


async def _heartbeat_loop(
    session: AsyncSession,
    run_id: uuid.UUID,
    state: _RunState,
    interval: timedelta,
    stop: asyncio.Event,
) -> None:
    """Beat every `interval` until `stop` is set, whatever the cases are doing."""
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval.total_seconds())
            return
        except TimeoutError:
            pass
        await _beat(session, run_id, state)


async def _beat(session: AsyncSession, run_id: uuid.UUID, state: _RunState) -> None:
    """Refresh the heartbeat, and stop the run's cases on a cancel request or if the run is
    no longer `running` (another worker reclaimed and finished it, or it is gone).

    A database error is logged and retried on the next beat: if it lasts, the run goes stale
    and another worker resumes it.
    """
    try:
        status = await session.scalar(
            select(EvaluationRun.status).where(EvaluationRun.id == run_id)
        )
        await session.commit()
        cancel_requested = await queue.heartbeat(session, run_id)
    except LookupError as exc:
        state.lose(str(exc))
        return
    except Exception:
        await session.rollback()
        logger.exception("evaluation run %s: heartbeat failed", run_id)
        return
    # Decide only after both awaits, so what this beat saw takes effect before any
    # other task runs.
    if status != "running":
        state.lose(f"run is {status}")
    elif cancel_requested:
        state.cancel()


async def _count_results(session: AsyncSession, run_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(EvaluationResult)
        .where(EvaluationResult.evaluation_run_id == run_id)
    )
    return count or 0


async def _set_total(session: AsyncSession, run_id: uuid.UUID, total: int) -> None:
    await session.execute(
        update(EvaluationRun)
        .where(EvaluationRun.id == run_id)
        .values(total_cases=total)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def _finish(
    session: AsyncSession, run_id: uuid.UUID, status: queue.FinishStatus, error: str | None = None
) -> None:
    """Finish the run, unless it has already ended elsewhere: then keep that outcome."""
    await session.commit()  # queue functions need no transaction open
    try:
        await queue.finish(session, run_id, status, error)
    except LookupError:
        logger.warning(
            "evaluation run %s ended elsewhere before this worker could finish it %s",
            run_id,
            status,
        )
        return
    logger.info("evaluation run %s finished %s", run_id, status)
