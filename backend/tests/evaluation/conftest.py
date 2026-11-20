"""Fixtures for the queue and runner tests, which need real commits across connections."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

TRUNCATED_TABLES = (
    "evaluation_results",
    "evaluation_runs",
    "spans",
    "traces",
    "test_cases",
    "datasets",
    "agent_versions",
    "agents",
)


async def _truncate(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(TRUNCATED_TABLES)} CASCADE"))


@pytest.fixture
async def committed_session_factory(
    engine: AsyncEngine,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An `async_sessionmaker` on the test engine whose data really commits.

    The rollback-only `db_session` can't be seen from a second connection, and the queue's
    `FOR UPDATE SKIP LOCKED` only means something across connections. The tables are
    truncated before (in case an earlier run was killed mid-test) and after each test, so
    nothing leaks into the `db_session` tests that share this database. Tests run one at a
    time, so the `TRUNCATE` lock never waits on another test's open transaction.
    """
    await _truncate(engine)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await _truncate(engine)
