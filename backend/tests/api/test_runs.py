"""POST /api/v1/runs. The provider factory is always overridden: no test calls a model API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.providers.base import ModelRequest, ModelResponse, Provider
from app.api.deps import get_provider_factory
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Trace
from tests.agents.fakes import ScriptedProvider, end_turn, response, tool_use
from tests.factories import make_agent_version


def use_provider(app: FastAPI, provider: Provider) -> list[str]:
    """Make every run use `provider`. Returns the provider names the factory was asked for."""
    asked: list[str] = []

    def factory(name: str) -> Provider:
        asked.append(name)
        return provider

    app.dependency_overrides[get_provider_factory] = lambda: factory
    return asked


async def trace_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Trace)) or 0


def walk(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in nodes:
        out.append(node)
        out.extend(walk(node["children"]))
    return out


async def test_run_completes(app: FastAPI, client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    asked = use_provider(app, ScriptedProvider([tool_use(), end_turn()]))

    r = await client.post(
        "/api/v1/runs",
        json={"agent_version_id": str(version.id), "input": "Find the weather in Seattle"},
    )

    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"trace_id", "status"}
    assert body["status"] == "completed"
    assert asked == ["anthropic"]

    t = await client.get(f"/api/v1/traces/{body['trace_id']}")
    assert t.status_code == 200
    trace = t.json()
    assert trace["status"] == "completed"
    assert trace["agent_version_id"] == str(version.id)
    assert trace["input"] == "Find the weather in Seattle"
    [root] = trace["spans"]
    assert "tool" in [child["kind"] for child in root["children"]]


async def test_failed_run_is_201(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    version = await make_agent_version(db_session)
    use_provider(app, ScriptedProvider([tool_use(), response("max_tokens", text="It is")]))

    r = await client.post(
        "/api/v1/runs", json={"agent_version_id": str(version.id), "input": "Seattle?"}
    )

    assert r.status_code == 201, r.text
    assert r.json()["status"] == "failed"
    trace = (await client.get(f"/api/v1/traces/{r.json()['trace_id']}")).json()
    assert trace["status"] == "failed"
    assert trace["error"] == "answer truncated at max_tokens"


class TransactionProbe(ScriptedProvider):
    """Records, at each model call, whether the request's session has a transaction open."""

    def __init__(self, script: list[ModelResponse | Exception], session: AsyncSession) -> None:
        super().__init__(script)
        self._session = session
        self.in_transaction: list[bool] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.in_transaction.append(self._session.in_transaction())
        return await super().complete(request)


async def test_no_transaction_open_during_model_calls(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    version = await make_agent_version(db_session)
    probe = TransactionProbe([tool_use(), end_turn()], db_session)
    use_provider(app, probe)

    r = await client.post(
        "/api/v1/runs", json={"agent_version_id": str(version.id), "input": "Seattle?"}
    )

    assert r.status_code == 201, r.text
    assert probe.in_transaction == [False, False]


async def assert_rejected(
    client: AsyncClient,
    session: AsyncSession,
    payload: dict[str, Any],
    status: int,
    detail: str,
) -> None:
    r = await client.post("/api/v1/runs", json=payload)
    assert r.status_code == status, r.text
    assert r.json() == {"detail": detail}
    assert await trace_count(session) == 0


@asynccontextmanager
async def real_factory_client(
    settings: Settings, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    """An app with the real provider factory (no override), on the test session."""
    application = create_app(settings)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_session] = override_get_session
    async with (
        LifespanManager(application),
        AsyncClient(transport=ASGITransport(application), base_url="http://test") as client,
    ):
        yield client


@pytest.fixture
def no_model(app: FastAPI) -> ScriptedProvider:
    """A provider that fails the test if the run ever reaches the model."""
    provider = ScriptedProvider([])
    use_provider(app, provider)
    return provider


async def test_unknown_version_404(
    client: AsyncClient, db_session: AsyncSession, no_model: ScriptedProvider
) -> None:
    await assert_rejected(
        client,
        db_session,
        {"agent_version_id": "00000000-0000-0000-0000-000000000000", "input": "hi"},
        404,
        "Agent version not found",
    )
    assert no_model.requests == []


async def test_unknown_tool_422(
    client: AsyncClient, db_session: AsyncSession, no_model: ScriptedProvider
) -> None:
    version = await make_agent_version(db_session, tools=["nope"])
    await assert_rejected(
        client,
        db_session,
        {"agent_version_id": str(version.id), "input": "hi"},
        422,
        "unknown tool: nope",
    )
    assert no_model.requests == []


async def test_openai_provider_422(settings: Settings, db_session: AsyncSession) -> None:
    """The real factory: an `openai` version is refused even when a key is set."""
    version = await make_agent_version(db_session, provider="openai")
    keyed = settings.model_copy(update={"anthropic_api_key": SecretStr("sk-test-not-used")})
    async with real_factory_client(keyed, db_session) as client:
        await assert_rejected(
            client,
            db_session,
            {"agent_version_id": str(version.id), "input": "hi"},
            422,
            "unsupported provider: openai",
        )


async def test_invalid_parameters_422(
    client: AsyncClient, db_session: AsyncSession, no_model: ScriptedProvider
) -> None:
    version = await make_agent_version(db_session, parameters={"temprature": 0})
    await assert_rejected(
        client,
        db_session,
        {"agent_version_id": str(version.id), "input": "hi"},
        422,
        "unsupported parameter(s): temprature; allowed: max_steps, max_tokens",
    )
    assert no_model.requests == []


async def test_missing_key_503(settings: Settings, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    async with real_factory_client(
        settings.model_copy(update={"anthropic_api_key": None}), db_session
    ) as client:
        await assert_rejected(
            client,
            db_session,
            {"agent_version_id": str(version.id), "input": "hi"},
            503,
            "ANTHROPIC_API_KEY is not set",
        )


async def test_empty_key_503(settings: Settings, db_session: AsyncSession) -> None:
    """The real factory: an empty key counts as unset. `model_copy` skips validation."""
    version = await make_agent_version(db_session)
    empty = settings.model_copy(update={"anthropic_api_key": SecretStr("")})
    async with real_factory_client(empty, db_session) as client:
        await assert_rejected(
            client,
            db_session,
            {"agent_version_id": str(version.id), "input": "hi"},
            503,
            "ANTHROPIC_API_KEY is not set",
        )


async def test_empty_input_422(
    client: AsyncClient, db_session: AsyncSession, no_model: ScriptedProvider
) -> None:
    version = await make_agent_version(db_session)
    r = await client.post("/api/v1/runs", json={"agent_version_id": str(version.id), "input": ""})
    assert r.status_code == 422
    assert await trace_count(db_session) == 0
    assert no_model.requests == []
