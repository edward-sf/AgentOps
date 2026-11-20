import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.common import Page, PageParams
from app.schemas.evaluation_run import (
    EvaluationResultRead,
    EvaluationResultStatus,
    EvaluationRunCreate,
    EvaluationRunRead,
    EvaluationRunStatus,
)
from app.services import evaluation_runs as svc

router = APIRouter(prefix="/api/v1/evaluation-runs", tags=["evaluation-runs"])


@router.post("", status_code=202)
async def create_evaluation_run(
    data: EvaluationRunCreate, session: SessionDep
) -> EvaluationRunRead:
    """Queue a `pending` run for a worker. Nothing executes in the request (D1)."""
    run = await svc.create_run(session, data)
    return await svc.to_read(session, run)


@router.get("")
async def list_evaluation_runs(
    session: SessionDep,
    page: PageParams,
    agent_version_id: uuid.UUID | None = None,
    dataset_id: uuid.UUID | None = None,
    status: EvaluationRunStatus | None = None,
) -> Page[EvaluationRunRead]:
    """Newest first."""
    items, total = await svc.list_runs(
        session,
        agent_version_id=agent_version_id,
        dataset_id=dataset_id,
        status=status,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[EvaluationRunRead](items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/{run_id}")
async def get_evaluation_run(run_id: uuid.UUID, session: SessionDep) -> EvaluationRunRead:
    return await svc.get_run(session, run_id)


@router.get("/{run_id}/results")
async def list_evaluation_results(
    run_id: uuid.UUID,
    session: SessionDep,
    page: PageParams,
    status: EvaluationResultStatus | None = None,
) -> Page[EvaluationResultRead]:
    """Ordered by `case_key`."""
    items, total = await svc.list_results(
        session, run_id, status=status, limit=page.limit, offset=page.offset
    )
    return Page[EvaluationResultRead](
        items=[EvaluationResultRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("/{run_id}/cancel")
async def cancel_evaluation_run(run_id: uuid.UUID, session: SessionDep) -> EvaluationRunRead:
    """A `pending` run becomes `cancelled`; a `running` one gets `cancel_requested` and the
    worker stops it (D7). 409 if the run has already finished."""
    run = await svc.cancel_run(session, run_id)
    return await svc.to_read(session, run)
