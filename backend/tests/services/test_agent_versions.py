import asyncio
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import Agent
from app.schemas.agent import AgentCreate, AgentVersionCreate
from app.services import agents as svc


async def test_concurrent_create_version_yields_1_and_2(engine: AsyncEngine) -> None:
    data = AgentVersionCreate(provider="anthropic", model="claude-sonnet-5-5", system_prompt="p")
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        agent = await svc.create_agent(setup, AgentCreate(name=f"concurrent-{uuid4().hex}"))
        await setup.commit()
        agent_id = agent.id
    try:

        async def make() -> int:
            async with AsyncSession(engine, expire_on_commit=False) as s:
                v = await svc.create_version(s, agent_id, data)
                await s.commit()
                return v.version

        results = await asyncio.gather(make(), make())
        assert sorted(results) == [1, 2]
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Agent).where(Agent.id == agent_id))
            await cleanup.commit()
