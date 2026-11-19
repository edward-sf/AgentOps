"""Load sample agents and datasets idempotently: `python -m app.seed datasets/sample.json`."""

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.schemas.agent import AgentCreate, AgentVersionCreate
from app.schemas.dataset import DatasetCreate, TestCaseCreate
from app.services import agents as agent_svc
from app.services import datasets as dataset_svc


@dataclass
class SeedReport:
    created: int = 0
    existing: int = 0


class _SeedAgent(BaseModel):
    name: str
    description: str | None = None
    versions: list[AgentVersionCreate] = Field(default_factory=list)


class _SeedDataset(BaseModel):
    name: str
    description: str | None = None
    test_cases: list[TestCaseCreate] = Field(default_factory=list)


class _SeedFile(BaseModel):
    agents: list[_SeedAgent] = Field(default_factory=list)
    datasets: list[_SeedDataset] = Field(default_factory=list)


async def seed(session: AsyncSession, path: Path) -> SeedReport:
    """Create whatever from `path` is missing. Existing rows are left untouched.

    An agent's versions are only seeded with the agent; versions are immutable and numbered
    server-side, so they cannot be matched individually on a re-run.
    """
    data = _SeedFile.model_validate(json.loads(await asyncio.to_thread(path.read_text)))
    report = SeedReport()

    for seed_agent in data.agents:
        if await agent_svc.get_agent_by_name(session, seed_agent.name) is not None:
            report.existing += 1 + len(seed_agent.versions)
            continue
        agent = await agent_svc.create_agent(
            session, AgentCreate(name=seed_agent.name, description=seed_agent.description)
        )
        report.created += 1
        for version in seed_agent.versions:
            await agent_svc.create_version(session, agent.id, version)
            report.created += 1

    for seed_dataset in data.datasets:
        dataset = await dataset_svc.get_dataset_by_name(session, seed_dataset.name)
        if dataset is None:
            dataset = await dataset_svc.create_dataset(
                session,
                DatasetCreate(name=seed_dataset.name, description=seed_dataset.description),
            )
            report.created += 1
        else:
            report.existing += 1
        for case in seed_dataset.test_cases:
            if await dataset_svc.get_test_case_by_key(session, dataset.id, case.key) is None:
                await dataset_svc.create_test_case(session, dataset.id, case)
                report.created += 1
            else:
                report.existing += 1
    return report


async def _main(path: Path) -> SeedReport:
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker[AsyncSession](engine, expire_on_commit=False)() as session:
            report = await seed(session, path)
            await session.commit()
            return report
    finally:
        await engine.dispose()


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m app.seed <path-to-seed.json>")
    report = asyncio.run(_main(Path(sys.argv[1])))
    print(f"seed: created={report.created} existing={report.existing}")


if __name__ == "__main__":
    main()
