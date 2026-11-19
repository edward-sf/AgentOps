from fastapi import APIRouter

from app.api.deps import ProviderFactoryDep, SessionDep
from app.schemas.run import RunCreate, RunRead
from app.services import runs as svc

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.post("", status_code=201)
async def create_run(
    data: RunCreate, session: SessionDep, provider_factory: ProviderFactoryDep
) -> RunRead:
    """Run an AgentVersion synchronously. A failed run is still 201, with `status: failed`."""
    outcome = await svc.start_run(session, provider_factory, data)
    return RunRead(trace_id=outcome.trace_id, status=outcome.status)
