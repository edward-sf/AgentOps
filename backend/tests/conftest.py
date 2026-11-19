from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from alembic import command
from app.config import Settings
from app.db import get_session
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parent.parent


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


@pytest.fixture
def settings() -> Settings:
    base = Settings()
    return base.model_copy(update={"database_url": base.test_database_url})


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[str]:
    """Bring the test database to head. Sync, because env.py calls asyncio.run."""
    url = Settings().test_database_url
    command.upgrade(alembic_config(url), "head")
    yield url


@pytest.fixture(scope="session")
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(migrated_database)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One connection per test; the outer transaction is rolled back at teardown."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


@pytest.fixture
async def app(settings: Settings, db_session: AsyncSession) -> AsyncIterator[FastAPI]:
    application = create_app(settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_session] = override_get_session
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
        yield c
