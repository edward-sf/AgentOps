"""The evaluation run queue (D1, D6, D7). Data really commits: see `committed_session_factory`."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.errors import ConflictError
from app.evaluation import queue
from app.models import EvaluationRun
from app.services import evaluation_runs as svc
from tests.factories import make_agent_version, make_dataset, make_evaluation_run

STALE = timedelta(seconds=60)
T0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)

Factory = async_sessionmaker[AsyncSession]


async def make_runs(factory: Factory, *fields: dict[str, Any]) -> list[uuid.UUID]:
    """Commit one run per `fields` dict, sharing a version and dataset."""
    async with factory() as s:
        version = await make_agent_version(s)
        dataset = await make_dataset(s)
        runs = [await make_evaluation_run(s, version=version, dataset=dataset, **f) for f in fields]
        await s.commit()
        return [r.id for r in runs]


async def load(factory: Factory, run_id: uuid.UUID) -> EvaluationRun:
    async with factory() as s:
        run = await s.get(EvaluationRun, run_id)
        assert run is not None
        return run


async def claim(factory: Factory) -> uuid.UUID | None:
    async with factory() as s:
        return await queue.claim_next(s, stale_after=STALE)


def ago(seconds: int) -> Any:
    """A timestamp relative to the database clock."""
    return func.now() - timedelta(seconds=seconds)


async def test_claim_oldest_pending(committed_session_factory: Factory) -> None:
    newer, older = await make_runs(
        committed_session_factory,
        {"created_at": T0 + timedelta(minutes=5)},
        {"created_at": T0},
    )

    assert await claim(committed_session_factory) == older

    run = await load(committed_session_factory, older)
    assert run.status == "running"
    assert run.started_at is not None
    assert run.heartbeat_at is not None
    assert (await load(committed_session_factory, newer)).status == "pending"
    assert await claim(committed_session_factory) == newer
    assert await claim(committed_session_factory) is None


async def test_claim_none_when_empty(committed_session_factory: Factory) -> None:
    assert await claim(committed_session_factory) is None


async def test_claim_ignores_finished_runs(committed_session_factory: Factory) -> None:
    await make_runs(
        committed_session_factory,
        {"status": "completed", "completed_at": T0},
        {"status": "failed", "completed_at": T0},
        {"status": "cancelled", "completed_at": T0},
    )
    assert await claim(committed_session_factory) is None


async def test_claim_skips_fresh_running(committed_session_factory: Factory) -> None:
    await make_runs(
        committed_session_factory,
        {"status": "running", "started_at": T0, "heartbeat_at": ago(5)},
    )
    assert await claim(committed_session_factory) is None


async def test_claim_reclaims_stale_running(committed_session_factory: Factory) -> None:
    [run_id] = await make_runs(
        committed_session_factory,
        {"status": "running", "started_at": T0, "heartbeat_at": ago(120), "total_cases": 3},
    )
    before = await load(committed_session_factory, run_id)

    assert await claim(committed_session_factory) == run_id

    after = await load(committed_session_factory, run_id)
    assert after.status == "running"
    assert after.started_at == T0  # set on the first claim only
    assert after.total_cases == 3
    assert before.heartbeat_at is not None and after.heartbeat_at is not None
    assert after.heartbeat_at > before.heartbeat_at
    # Fresh again: a second worker can't take it.
    assert await claim(committed_session_factory) is None


async def test_claim_skips_cancel_requested_pending(committed_session_factory: Factory) -> None:
    await make_runs(committed_session_factory, {"cancel_requested": True})
    assert await claim(committed_session_factory) is None


async def test_claim_reclaims_stale_cancel_requested_running(
    committed_session_factory: Factory,
) -> None:
    """A dead worker's cancelled run is reclaimed so a worker can finish it as `cancelled`,
    rather than staying `running` forever."""
    [run_id] = await make_runs(
        committed_session_factory,
        {"status": "running", "heartbeat_at": ago(120), "cancel_requested": True},
    )
    assert await claim(committed_session_factory) == run_id


async def test_concurrent_claims_distinct(committed_session_factory: Factory) -> None:
    first, second = await make_runs(
        committed_session_factory, {"created_at": T0}, {"created_at": T0 + timedelta(seconds=1)}
    )
    claimed = await asyncio.gather(
        claim(committed_session_factory), claim(committed_session_factory)
    )
    assert sorted(map(str, claimed)) == sorted([str(first), str(second)])


async def test_concurrent_claims_one_run(committed_session_factory: Factory) -> None:
    [run_id] = await make_runs(committed_session_factory, {})
    for _ in range(5):  # repeat: the interleaving differs from one attempt to the next
        async with committed_session_factory() as s:
            await s.execute(
                EvaluationRun.__table__.update()
                .where(EvaluationRun.id == run_id)
                .values(status="pending", heartbeat_at=None)
            )
            await s.commit()
        claimed = await asyncio.gather(*(claim(committed_session_factory) for _ in range(4)))
        assert sorted(claimed, key=lambda c: c is None) == [run_id, None, None, None]


async def test_claim_does_not_wait_on_a_locked_run(committed_session_factory: Factory) -> None:
    """SKIP LOCKED: a run held by another transaction is passed over, not waited on."""
    locked, free = await make_runs(
        committed_session_factory, {"created_at": T0}, {"created_at": T0 + timedelta(seconds=1)}
    )
    async with committed_session_factory() as holder:
        await holder.execute(
            select(EvaluationRun.id).where(EvaluationRun.id == locked).with_for_update()
        )
        assert await asyncio.wait_for(claim(committed_session_factory), timeout=5) == free
        await holder.rollback()


async def test_heartbeat_returns_cancel_flag(committed_session_factory: Factory) -> None:
    [run_id] = await make_runs(
        committed_session_factory, {"status": "running", "heartbeat_at": ago(30)}
    )
    before = await load(committed_session_factory, run_id)

    async with committed_session_factory() as s:
        assert await queue.heartbeat(s, run_id) is False
    after = await load(committed_session_factory, run_id)
    assert before.heartbeat_at is not None and after.heartbeat_at is not None
    assert after.heartbeat_at > before.heartbeat_at

    async with committed_session_factory() as s:
        await svc.cancel_run(s, run_id)
        await s.commit()
    async with committed_session_factory() as s:
        assert await queue.heartbeat(s, run_id) is True


async def test_heartbeat_unknown_run_raises(committed_session_factory: Factory) -> None:
    async with committed_session_factory() as s:
        with pytest.raises(LookupError):
            await queue.heartbeat(s, uuid.uuid4())


async def test_finish_sets_completed_at(committed_session_factory: Factory) -> None:
    done, broken = await make_runs(
        committed_session_factory,
        {"status": "running", "heartbeat_at": ago(1)},
        {"status": "running", "heartbeat_at": ago(1)},
    )
    async with committed_session_factory() as s:
        await queue.finish(s, done, "completed")
        await queue.finish(s, broken, "failed", error="provider rejected the API key")

    run = await load(committed_session_factory, done)
    assert run.status == "completed" and run.completed_at is not None and run.error is None
    run = await load(committed_session_factory, broken)
    assert run.status == "failed" and run.completed_at is not None
    assert run.error == "provider rejected the API key"


async def test_finish_refuses_a_run_that_is_not_running(
    committed_session_factory: Factory,
) -> None:
    [run_id] = await make_runs(committed_session_factory, {"status": "cancelled"})
    async with committed_session_factory() as s:
        with pytest.raises(LookupError):
            await queue.finish(s, run_id, "completed")
    assert (await load(committed_session_factory, run_id)).status == "cancelled"


# --- cancel against the queue (Review Focus 5) ------------------------------------------


async def test_cancelled_pending_run_is_never_claimed(committed_session_factory: Factory) -> None:
    [run_id] = await make_runs(committed_session_factory, {})
    async with committed_session_factory() as s:
        run = await svc.cancel_run(s, run_id)
        await s.commit()
    assert run.status == "cancelled" and run.completed_at is not None
    assert await claim(committed_session_factory) is None


async def test_cancel_waiting_on_a_claim_sets_the_flag(committed_session_factory: Factory) -> None:
    """The claim holds the row lock when the cancel arrives. The conditional UPDATE waits,
    re-reads the now `running` row, and sets `cancel_requested`: the cancel isn't lost and
    the run doesn't become `cancelled` under the worker."""
    [run_id] = await make_runs(committed_session_factory, {})
    async with committed_session_factory() as claimer:
        locked = await claimer.scalar(
            select(EvaluationRun.id)
            .where(EvaluationRun.id == run_id, EvaluationRun.status == "pending")
            .with_for_update(skip_locked=True)
        )
        assert locked == run_id

        async def cancel() -> EvaluationRun:
            async with committed_session_factory() as s:
                run = await svc.cancel_run(s, run_id)
                await s.commit()
                return run

        cancelling = asyncio.create_task(cancel())
        await asyncio.sleep(0.3)
        assert not cancelling.done()  # blocked on the claim's row lock

        await claimer.execute(
            EvaluationRun.__table__.update()
            .where(EvaluationRun.id == run_id)
            .values(status="running", heartbeat_at=func.now(), started_at=func.now())
        )
        await claimer.commit()
        run = await asyncio.wait_for(cancelling, timeout=5)

    assert run.status == "running"
    assert run.cancel_requested is True
    assert run.completed_at is None
    async with committed_session_factory() as s:
        assert await queue.heartbeat(s, run_id) is True


async def test_cancel_finished_run_conflicts(committed_session_factory: Factory) -> None:
    [run_id] = await make_runs(committed_session_factory, {"status": "running"})
    async with committed_session_factory() as s:
        await queue.finish(s, run_id, "completed")
    async with committed_session_factory() as s:
        with pytest.raises(ConflictError):
            await svc.cancel_run(s, run_id)
