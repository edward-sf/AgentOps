import uuid

from fastapi import APIRouter, Response

from app.api.deps import SessionDep
from app.schemas.agent import (
    AgentCreate,
    AgentRead,
    AgentUpdate,
    AgentVersionCreate,
    AgentVersionRead,
)
from app.schemas.common import Page, PageParams
from app.services import agents as svc

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.post("", status_code=201)
async def create_agent(data: AgentCreate, session: SessionDep) -> AgentRead:
    return AgentRead.model_validate(await svc.create_agent(session, data))


@router.get("")
async def list_agents(session: SessionDep, page: PageParams) -> Page[AgentRead]:
    items, total = await svc.list_agents(session, page.limit, page.offset)
    return Page[AgentRead](
        items=[AgentRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{agent_id}")
async def get_agent(agent_id: uuid.UUID, session: SessionDep) -> AgentRead:
    return AgentRead.model_validate(await svc.get_agent(session, agent_id))


@router.patch("/{agent_id}")
async def update_agent(agent_id: uuid.UUID, data: AgentUpdate, session: SessionDep) -> AgentRead:
    return AgentRead.model_validate(await svc.update_agent(session, agent_id, data))


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: uuid.UUID, session: SessionDep) -> Response:
    await svc.delete_agent(session, agent_id)
    return Response(status_code=204)


@router.post("/{agent_id}/versions", status_code=201)
async def create_version(
    agent_id: uuid.UUID, data: AgentVersionCreate, session: SessionDep
) -> AgentVersionRead:
    return AgentVersionRead.model_validate(await svc.create_version(session, agent_id, data))


@router.get("/{agent_id}/versions")
async def list_versions(
    agent_id: uuid.UUID, session: SessionDep, page: PageParams
) -> Page[AgentVersionRead]:
    items, total = await svc.list_versions(session, agent_id, page.limit, page.offset)
    return Page[AgentVersionRead](
        items=[AgentVersionRead.model_validate(i) for i in items],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{agent_id}/versions/{version}")
async def get_version(agent_id: uuid.UUID, version: int, session: SessionDep) -> AgentVersionRead:
    return AgentVersionRead.model_validate(await svc.get_version(session, agent_id, version))
