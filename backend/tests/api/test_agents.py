from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Dataset, EvaluationRun

VERSION_BODY = {"provider": "anthropic", "model": "claude-sonnet-5-5", "system_prompt": "p"}


async def test_create_and_get_agent(client: AsyncClient) -> None:
    r = await client.post("/api/v1/agents", json={"name": "weather-bot", "description": "d"})
    assert r.status_code == 201
    agent = r.json()
    assert (await client.get(f"/api/v1/agents/{agent['id']}")).json()["name"] == "weather-bot"


async def test_duplicate_agent_name_is_409(client: AsyncClient) -> None:
    await client.post("/api/v1/agents", json={"name": "dup"})
    r = await client.post("/api/v1/agents", json={"name": "dup"})
    assert r.status_code == 409 and r.json() == {"detail": "Agent name already exists"}


async def test_rename_to_existing_name_is_409(client: AsyncClient) -> None:
    await client.post("/api/v1/agents", json={"name": "a"})
    b = (await client.post("/api/v1/agents", json={"name": "b"})).json()
    r = await client.patch(f"/api/v1/agents/{b['id']}", json={"name": "a"})
    assert r.status_code == 409 and r.json() == {"detail": "Agent name already exists"}


async def test_patch_agent_updates_fields_and_updated_at(client: AsyncClient) -> None:
    a = (await client.post("/api/v1/agents", json={"name": "p", "description": "old"})).json()
    r = await client.patch(f"/api/v1/agents/{a['id']}", json={"description": "new"})
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "new" and body["name"] == "p"
    assert body["updated_at"] > a["updated_at"]
    assert body["created_at"] == a["created_at"]


async def test_list_agents_paginates(client: AsyncClient) -> None:
    for i in range(3):
        await client.post("/api/v1/agents", json={"name": f"a{i}"})
    body = (await client.get("/api/v1/agents?limit=2&offset=1")).json()
    assert body["total"] == 3 and body["limit"] == 2 and body["offset"] == 1
    assert len(body["items"]) == 2


@pytest.mark.parametrize("qs", ["limit=0", "limit=201", "offset=-1"])
async def test_list_agents_rejects_bad_pagination(client: AsyncClient, qs: str) -> None:
    assert (await client.get(f"/api/v1/agents?{qs}")).status_code == 422


async def test_unknown_agent_is_404(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/agents/{uuid4()}")
    assert r.status_code == 404 and r.json() == {"detail": "Agent not found"}


async def test_versions_are_numbered_and_immutable(client: AsyncClient) -> None:
    a = (await client.post("/api/v1/agents", json={"name": "v"})).json()
    v1 = (await client.post(f"/api/v1/agents/{a['id']}/versions", json=VERSION_BODY)).json()
    v2 = (await client.post(f"/api/v1/agents/{a['id']}/versions", json=VERSION_BODY)).json()
    assert (v1["version"], v2["version"]) == (1, 2)
    assert (await client.patch(f"/api/v1/agents/{a['id']}/versions/1", json={})).status_code == 405
    assert (await client.delete(f"/api/v1/agents/{a['id']}/versions/1")).status_code == 405
    assert (await client.get(f"/api/v1/agents/{a['id']}/versions/2")).json()["id"] == v2["id"]
    listing = (await client.get(f"/api/v1/agents/{a['id']}/versions")).json()
    assert listing["total"] == 2 and [i["version"] for i in listing["items"]] == [1, 2]


async def test_unknown_version_is_404(client: AsyncClient) -> None:
    a = (await client.post("/api/v1/agents", json={"name": "nv"})).json()
    assert (await client.get(f"/api/v1/agents/{a['id']}/versions/9")).status_code == 404
    r = await client.post(f"/api/v1/agents/{uuid4()}/versions", json=VERSION_BODY)
    assert r.status_code == 404


async def test_unknown_provider_is_422(client: AsyncClient) -> None:
    a = (await client.post("/api/v1/agents", json={"name": "prov"})).json()
    r = await client.post(
        f"/api/v1/agents/{a['id']}/versions", json={**VERSION_BODY, "provider": "mistral"}
    )
    assert r.status_code == 422


async def test_delete_agent_cascades_versions(client: AsyncClient) -> None:
    a = (await client.post("/api/v1/agents", json={"name": "del"})).json()
    await client.post(f"/api/v1/agents/{a['id']}/versions", json=VERSION_BODY)
    assert (await client.delete(f"/api/v1/agents/{a['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/agents/{a['id']}")).status_code == 404
    assert (await client.get(f"/api/v1/agents/{a['id']}/versions")).status_code == 404


async def test_delete_agent_with_history_is_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    agent_id = (await client.post("/api/v1/agents", json={"name": "hist"})).json()["id"]
    version = (await client.post(f"/api/v1/agents/{agent_id}/versions", json=VERSION_BODY)).json()
    dataset = Dataset(name="hist-ds")
    db_session.add(dataset)
    await db_session.flush()
    db_session.add(EvaluationRun(agent_version_id=version["id"], dataset_id=dataset.id))
    await db_session.flush()

    r = await client.delete(f"/api/v1/agents/{agent_id}")
    assert r.status_code == 409
    assert r.json() == {"detail": "Agent is referenced by evaluation history"}
    assert (await client.get(f"/api/v1/agents/{agent_id}")).status_code == 200
    assert (await client.get(f"/api/v1/agents/{agent_id}/versions/1")).status_code == 200


async def test_openapi_documents_agent_endpoints(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert set(paths["/api/v1/agents"]) == {"get", "post"}
    assert set(paths["/api/v1/agents/{agent_id}"]) == {"get", "patch", "delete"}
    assert set(paths["/api/v1/agents/{agent_id}/versions"]) == {"get", "post"}
    assert set(paths["/api/v1/agents/{agent_id}/versions/{version}"]) == {"get"}
