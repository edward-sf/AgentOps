import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.errors import NotFoundError
from app.schemas.common import Page, PageParams
from app.schemas.trace import TraceDetail, TraceRead, TraceStatus
from app.services import traces as svc

router = APIRouter(prefix="/api/v1/traces", tags=["traces"])


@router.get("")
async def list_traces(
    session: SessionDep,
    page: PageParams,
    agent_version_id: uuid.UUID | None = None,
    status: TraceStatus | None = None,
) -> Page[TraceRead]:
    items, total = await svc.list_traces(
        session,
        agent_version_id=agent_version_id,
        status=status,
        limit=page.limit,
        offset=page.offset,
    )
    return Page[TraceRead](
        items=[TraceRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{trace_id}")
async def get_trace(trace_id: uuid.UUID, session: SessionDep) -> TraceDetail:
    detail = await svc.get_trace_detail(session, trace_id)
    if detail is None:
        raise NotFoundError("Trace not found")
    return detail
