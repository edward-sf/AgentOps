import logging

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


async def test_health_ok_when_database_reachable(client: AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


async def test_health_503_when_database_unreachable(settings: Settings) -> None:
    bad = settings.model_copy(update={"database_url": "postgresql+psycopg://x:x@localhost:1/none"})
    app = create_app(bad)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as c,
    ):
        r = await c.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "database": "error"}


async def test_health_logs_database_error(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    bad = settings.model_copy(update={"database_url": "postgresql+psycopg://x:x@localhost:1/none"})
    app = create_app(bad)
    with caplog.at_level(logging.WARNING, logger="app.health"):
        async with (
            LifespanManager(app),
            AsyncClient(transport=ASGITransport(app), base_url="http://test") as c,
        ):
            r = await c.get("/health")
    assert r.status_code == 503
    records = [rec for rec in caplog.records if rec.name == "app.health"]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is not None
