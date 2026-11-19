import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import NotFoundError, integrity_conflict
from app.models import Agent, AgentVersion
from app.schemas.agent import AgentCreate, AgentUpdate, AgentVersionCreate


async def create_agent(session: AsyncSession, data: AgentCreate) -> Agent:
    agent = Agent(name=data.name, description=data.description)
    try:
        async with session.begin_nested():
            session.add(agent)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(agent)  # server defaults: id, created_at, updated_at
    return agent


async def list_agents(session: AsyncSession, limit: int, offset: int) -> tuple[list[Agent], int]:
    total = await session.scalar(select(func.count()).select_from(Agent)) or 0
    result = await session.scalars(
        select(Agent).order_by(Agent.created_at, Agent.id).limit(limit).offset(offset)
    )
    return list(result), total


async def get_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise NotFoundError("Agent not found")
    return agent


async def get_agent_by_name(session: AsyncSession, name: str) -> Agent | None:
    return await session.scalar(select(Agent).where(Agent.name == name))


async def update_agent(session: AsyncSession, agent_id: uuid.UUID, data: AgentUpdate) -> Agent:
    agent = await get_agent(session, agent_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is None:
        changes.pop("name", None)  # name is not nullable
    try:
        async with session.begin_nested():
            for field, value in changes.items():
                setattr(agent, field, value)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(agent)  # updated_at is set by a DB trigger
    return agent


async def delete_agent(session: AsyncSession, agent_id: uuid.UUID) -> None:
    agent = await get_agent(session, agent_id)
    try:
        async with session.begin_nested():
            # Core DELETE: the database cascades to versions, then RESTRICT guards history.
            await session.execute(delete(Agent).where(Agent.id == agent.id))
    except IntegrityError as exc:
        raise integrity_conflict(exc, entity="Agent") from exc
    session.expunge(agent)


async def create_version(
    session: AsyncSession, agent_id: uuid.UUID, data: AgentVersionCreate
) -> AgentVersion:
    # Lock the agent row so concurrent creators serialise and never pick the same number.
    locked = await session.scalar(select(Agent.id).where(Agent.id == agent_id).with_for_update())
    if locked is None:
        raise NotFoundError("Agent not found")
    next_version = await session.scalar(
        select(func.coalesce(func.max(AgentVersion.version), 0) + 1).where(
            AgentVersion.agent_id == agent_id
        )
    )
    version = AgentVersion(agent_id=agent_id, version=next_version, **data.model_dump())
    try:
        async with session.begin_nested():
            session.add(version)
            await session.flush()
    except IntegrityError as exc:
        raise integrity_conflict(exc) from exc
    await session.refresh(version)
    return version


async def list_versions(
    session: AsyncSession, agent_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[AgentVersion], int]:
    await get_agent(session, agent_id)
    total = (
        await session.scalar(
            select(func.count()).select_from(AgentVersion).where(AgentVersion.agent_id == agent_id)
        )
        or 0
    )
    result = await session.scalars(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version)
        .limit(limit)
        .offset(offset)
    )
    return list(result), total


async def get_version(session: AsyncSession, agent_id: uuid.UUID, version: int) -> AgentVersion:
    await get_agent(session, agent_id)
    found = await session.scalar(
        select(AgentVersion).where(
            AgentVersion.agent_id == agent_id, AgentVersion.version == version
        )
    )
    if found is None:
        raise NotFoundError("Agent version not found")
    return found
