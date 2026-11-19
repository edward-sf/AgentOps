import json
from uuid import uuid4

from asgi_lifespan import LifespanManager
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from starlette.types import Message

from app.config import Settings
from app.main import create_app
from app.models import Agent


async def test_post_is_committed_before_the_response_is_sent(
    settings: Settings, engine: AsyncEngine
) -> None:
    """Real get_session, no override. Drive the ASGI app directly so we can look at the
    database at the moment the response body is sent (httpx only returns after teardown)."""
    name = f"commit-timing-{uuid4()}"
    app = create_app(settings)
    seen_at_send: list[bool] = []
    status: list[int] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": json.dumps({"name": name}).encode()}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body" and not message.get("more_body"):
            async with AsyncSession(engine) as fresh:
                found = (await fresh.execute(select(Agent).where(Agent.name == name))).scalar()
                seen_at_send.append(found is not None)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/agents",
        "raw_path": b"/api/v1/agents",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), (b"host", b"test")],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    try:
        async with LifespanManager(app):
            await app(scope, receive, send)
        assert status == [201]
        assert seen_at_send == [True]
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(Agent).where(Agent.name == name))
            await cleanup.commit()
