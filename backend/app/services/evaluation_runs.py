"""Create, read and cancel evaluation runs (M2 spec 6.2 step 1, 6.4, D7).

Nothing here executes a case: the worker does (D1). Submitting a run never builds a provider,
so a missing API key is the worker's concern (D4).
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.config import AgentConfig, InvalidParametersError
from app.agents.providers import SUPPORTED_PROVIDERS, UnsupportedProviderError
from app.agents.tools import UnknownToolError
from app.errors import ConflictError, NotFoundError, UnprocessableError
from app.models import AgentVersion, Dataset, EvaluationResult, EvaluationRun, TestCase
from app.schemas.evaluation_run import (
    EvaluationRunCreate,
    EvaluationRunRead,
    ResultCounts,
)

CANCELLABLE_STATUSES = ("pending", "running")


async def create_run(session: AsyncSession, data: EvaluationRunCreate) -> EvaluationRun:
    """Validate the version and dataset, then create a `pending` run.

    Raises 404 for an unknown version or dataset, and 422 for a version that can't run
    (unknown tool, unsupported provider, invalid parameters) or a dataset with no cases.
    Nothing is written when it raises.
    """
    version = await session.get(AgentVersion, data.agent_version_id)
    if version is None:
        raise NotFoundError("Agent version not found")
    if await session.get(Dataset, data.dataset_id) is None:
        raise NotFoundError("Dataset not found")
    if version.provider not in SUPPORTED_PROVIDERS:
        raise UnprocessableError(str(UnsupportedProviderError(version.provider)))
    try:
        AgentConfig.from_version(version)
    except (InvalidParametersError, UnknownToolError) as exc:
        raise UnprocessableError(str(exc)) from exc
    has_cases = await session.scalar(
        select(select(TestCase.id).where(TestCase.dataset_id == data.dataset_id).exists())
    )
    if not has_cases:
        raise UnprocessableError("Dataset has no test cases")

    run = EvaluationRun(agent_version_id=version.id, dataset_id=data.dataset_id)
    session.add(run)
    await session.flush()
    await session.refresh(run)  # server defaults: id, status, cancel_requested, created_at
    return run


async def list_runs(
    session: AsyncSession,
    *,
    agent_version_id: uuid.UUID | None,
    dataset_id: uuid.UUID | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[EvaluationRunRead], int]:
    """Newest first."""
    conditions = []
    if agent_version_id is not None:
        conditions.append(EvaluationRun.agent_version_id == agent_version_id)
    if dataset_id is not None:
        conditions.append(EvaluationRun.dataset_id == dataset_id)
    if status is not None:
        conditions.append(EvaluationRun.status == status)
    total = (
        await session.scalar(select(func.count()).select_from(EvaluationRun).where(*conditions))
        or 0
    )
    runs = list(
        await session.scalars(
            select(EvaluationRun)
            .where(*conditions)
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return await _with_counts(session, runs), total


async def get_run(session: AsyncSession, run_id: uuid.UUID) -> EvaluationRunRead:
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise NotFoundError("Evaluation run not found")
    return await to_read(session, run)


async def list_results(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[EvaluationResult], int]:
    """The run's results ordered by `case_key`. Raises 404 for an unknown run."""
    if await session.get(EvaluationRun, run_id) is None:
        raise NotFoundError("Evaluation run not found")
    conditions = [EvaluationResult.evaluation_run_id == run_id]
    if status is not None:
        conditions.append(EvaluationResult.status == status)
    total = (
        await session.scalar(select(func.count()).select_from(EvaluationResult).where(*conditions))
        or 0
    )
    result = await session.scalars(
        select(EvaluationResult)
        .where(*conditions)
        .order_by(EvaluationResult.case_key, EvaluationResult.id)
        .limit(limit)
        .offset(offset)
    )
    return list(result), total


async def cancel_run(session: AsyncSession, run_id: uuid.UUID) -> EvaluationRun:
    """Cancel a run (D7): `pending` becomes `cancelled`, `running` gets `cancel_requested`.

    One conditional UPDATE does both, so it can't race the worker's claim into a lost
    cancel. If a claim holds the row lock, Postgres waits for it, then re-checks the WHERE
    and re-evaluates the CASEs against the row the claim committed: a run the worker has
    just claimed gets `cancel_requested` and stays `running`. A pending run also gets
    `cancel_requested`, which the claim query already skips.

    Raises 404 for an unknown run and 409 for a finished one.
    """
    is_pending = EvaluationRun.status == "pending"
    cancelled_id = await session.scalar(
        update(EvaluationRun)
        .where(EvaluationRun.id == run_id, EvaluationRun.status.in_(CANCELLABLE_STATUSES))
        .values(
            status=case((is_pending, "cancelled"), else_=EvaluationRun.status),
            completed_at=case((is_pending, func.now()), else_=EvaluationRun.completed_at),
            cancel_requested=True,
        )
        .returning(EvaluationRun.id)
        .execution_options(synchronize_session=False)
    )
    if cancelled_id is None:
        status = await session.scalar(
            select(EvaluationRun.status).where(EvaluationRun.id == run_id)
        )
        if status is None:
            raise NotFoundError("Evaluation run not found")
        raise ConflictError(f"Evaluation run is already {status}")
    run = await session.get(EvaluationRun, run_id, populate_existing=True)
    assert run is not None
    return run


async def to_read(session: AsyncSession, run: EvaluationRun) -> EvaluationRunRead:
    """The run as the API returns it, with its result counts."""
    [read] = await _with_counts(session, [run])
    return read


async def _with_counts(
    session: AsyncSession, runs: Sequence[EvaluationRun]
) -> list[EvaluationRunRead]:
    """Each run with its result counts by status, zeros included, in one query."""
    counts: dict[uuid.UUID, dict[str, int]] = {run.id: {} for run in runs}
    if runs:
        rows = await session.execute(
            select(EvaluationResult.evaluation_run_id, EvaluationResult.status, func.count())
            .where(EvaluationResult.evaluation_run_id.in_(counts))
            .group_by(EvaluationResult.evaluation_run_id, EvaluationResult.status)
        )
        for run_id, status, n in rows:
            counts[run_id][status] = n
    return [
        EvaluationRunRead.model_validate(
            {
                **{f: getattr(run, f) for f in EvaluationRunRead.model_fields if f != "counts"},
                "counts": ResultCounts(**counts[run.id]),
            }
        )
        for run in runs
    ]
