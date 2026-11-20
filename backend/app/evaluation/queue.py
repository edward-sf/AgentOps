"""`evaluation_runs` as the worker's queue (M2 spec D1, D6, D7).

Each function opens and commits its own transaction on the given session, so call it with no
transaction in progress. Every timestamp is the database's `now()`, never the worker's clock,
so workers on different hosts agree on what is stale.
"""

import uuid
from datetime import timedelta
from typing import Literal

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvaluationRun

FinishStatus = Literal["completed", "failed", "cancelled"]


async def claim_next(session: AsyncSession, *, stale_after: timedelta) -> uuid.UUID | None:
    """Claim the oldest claimable run and return its id, or `None` if there is none.

    Claimable means `pending` without `cancel_requested`, or `running` with a heartbeat older
    than `stale_after` (its worker is presumed dead). A stale run is reclaimed even when
    `cancel_requested` is set, so a worker can finish it as `cancelled` instead of leaving it
    `running` forever.

    `FOR UPDATE SKIP LOCKED` makes concurrent claimers pass over a row another one has
    locked, so each run goes to exactly one worker. The claim sets `status='running'` and
    `heartbeat_at`, and `started_at` on the first claim only.
    """
    claimable = or_(
        and_(EvaluationRun.status == "pending", EvaluationRun.cancel_requested.is_(False)),
        and_(
            EvaluationRun.status == "running",
            or_(
                EvaluationRun.heartbeat_at.is_(None),
                EvaluationRun.heartbeat_at < func.now() - stale_after,
            ),
        ),
    )
    try:
        run_id = await session.scalar(
            select(EvaluationRun.id)
            .where(claimable)
            .order_by(EvaluationRun.created_at, EvaluationRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run_id is not None:
            await session.execute(
                update(EvaluationRun)
                .where(EvaluationRun.id == run_id)
                .values(
                    status="running",
                    heartbeat_at=func.now(),
                    started_at=func.coalesce(EvaluationRun.started_at, func.now()),
                )
                .execution_options(synchronize_session=False)
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return run_id


async def heartbeat(session: AsyncSession, run_id: uuid.UUID) -> bool:
    """Refresh the run's `heartbeat_at` and return its `cancel_requested`.

    Raises `LookupError` if the run doesn't exist.
    """
    try:
        cancel_requested = await session.scalar(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id)
            .values(heartbeat_at=func.now())
            .returning(EvaluationRun.cancel_requested)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if cancel_requested is None:
        raise LookupError(f"evaluation run {run_id} not found")
    return cancel_requested


async def finish(
    session: AsyncSession, run_id: uuid.UUID, status: FinishStatus, error: str | None = None
) -> None:
    """End a `running` run with `status`, `error` and `completed_at`.

    Only a `running` run can be finished, so a worker never overwrites a run that has
    already ended. Raises `LookupError` if no `running` run has this id.
    """
    try:
        finished = await session.scalar(
            update(EvaluationRun)
            .where(EvaluationRun.id == run_id, EvaluationRun.status == "running")
            .values(status=status, error=error, completed_at=func.now())
            .returning(EvaluationRun.id)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if finished is None:
        raise LookupError(f"no running evaluation run {run_id}")
