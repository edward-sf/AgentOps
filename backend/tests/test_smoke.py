"""`make smoke` is tested only through its refusal path and with a scripted provider."""

import io
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app import smoke
from app.agents.providers.base import ProviderAPIError
from app.config import Settings
from tests.agents.fakes import ScriptedProvider, end_turn, tool_use
from tests.api.test_runs import use_provider


async def test_refuses_without_key(settings: Settings, capsys: Any) -> None:
    code = await smoke.run(settings.model_copy(update={"anthropic_api_key": None}))
    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


async def test_refuses_with_empty_key(settings: Settings, capsys: Any) -> None:
    code = await smoke.run(settings.model_copy(update={"anthropic_api_key": SecretStr("")}))
    assert code == 2
    assert "ANTHROPIC_API_KEY is not set" in capsys.readouterr().err


async def seed_weather_bot(client: AsyncClient, parameters: dict[str, Any]) -> None:
    r = await client.post("/api/v1/agents", json={"name": smoke.AGENT_NAME})
    assert r.status_code == 201, r.text
    r = await client.post(
        f"/api/v1/agents/{r.json()['id']}/versions",
        json={
            "provider": "anthropic",
            "model": "claude-sonnet-5-5",
            "system_prompt": "Use get_weather.",
            "parameters": parameters,
            "tools": ["get_weather"],
        },
    )
    assert r.status_code == 201, r.text


async def run_smoke(client: AsyncClient) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = await smoke.smoke(client, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


async def test_prints_tree_and_passes(app: FastAPI, client: AsyncClient) -> None:
    await seed_weather_bot(client, {"max_tokens": 1024})
    use_provider(app, ScriptedProvider([tool_use(), end_turn()]))

    code, out, err = await run_smoke(client)

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0].endswith(" completed")
    assert lines[1].startswith("chain agent.run ok ")
    llm = [line for line in lines if line.lstrip().startswith("llm ")]
    assert len(llm) == 2
    assert all(line.startswith("  ") and line.endswith(" in=100 out=20") for line in llm)
    assert any(line.lstrip().startswith("tool ") for line in lines)


async def test_fails_without_tool_span(app: FastAPI, client: AsyncClient) -> None:
    await seed_weather_bot(client, {"max_tokens": 1024})
    use_provider(app, ScriptedProvider([end_turn()]))

    code, _, err = await run_smoke(client)

    assert code == 1
    assert "without a tool span" in err


async def test_fails_when_run_fails(app: FastAPI, client: AsyncClient) -> None:
    await seed_weather_bot(client, {"max_tokens": 1024})
    use_provider(app, ScriptedProvider([ProviderAPIError("boom")]))

    code, _, err = await run_smoke(client)

    assert code == 1
    assert "did not complete" in err


async def test_pre_m1_seed_gets_reseed_hint(app: FastAPI, client: AsyncClient) -> None:
    await seed_weather_bot(client, {"temperature": 0, "max_tokens": 1024})
    use_provider(app, ScriptedProvider([]))

    code, _, err = await run_smoke(client)

    assert code == 1
    assert "temperature" in err
    assert err.splitlines()[-1] == smoke.RESEED_HINT
