from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EvaluationResult, EvaluationRun, Trace
from tests.factories import DEFAULT_TRACE_ID, make_agent_version

DS = "/api/v1/datasets"


@pytest.fixture
async def dataset(client: AsyncClient) -> dict[str, Any]:
    r = await client.post(DS, json={"name": "ds", "description": "d"})
    assert r.status_code == 201
    body: dict[str, Any] = r.json()
    return body


async def test_create_and_get_dataset(client: AsyncClient) -> None:
    r = await client.post(DS, json={"name": "weather", "description": "d"})
    assert r.status_code == 201
    ds = r.json()
    assert (await client.get(f"{DS}/{ds['id']}")).json()["name"] == "weather"


async def test_duplicate_dataset_name_is_409(client: AsyncClient) -> None:
    await client.post(DS, json={"name": "dup"})
    r = await client.post(DS, json={"name": "dup"})
    assert r.status_code == 409 and r.json() == {"detail": "Dataset name already exists"}


async def test_rename_to_existing_name_is_409(client: AsyncClient) -> None:
    await client.post(DS, json={"name": "a"})
    b = (await client.post(DS, json={"name": "b"})).json()
    r = await client.patch(f"{DS}/{b['id']}", json={"name": "a"})
    assert r.status_code == 409 and r.json() == {"detail": "Dataset name already exists"}


async def test_patch_dataset_updates_fields(client: AsyncClient) -> None:
    d = (await client.post(DS, json={"name": "p", "description": "old"})).json()
    r = await client.patch(f"{DS}/{d['id']}", json={"description": "new"})
    body = r.json()
    assert r.status_code == 200 and body["description"] == "new" and body["name"] == "p"
    assert body["updated_at"] > d["updated_at"]


async def test_list_datasets_paginates(client: AsyncClient) -> None:
    for i in range(3):
        await client.post(DS, json={"name": f"d{i}"})
    body = (await client.get(f"{DS}?limit=2&offset=1")).json()
    assert body["total"] == 3 and body["limit"] == 2 and body["offset"] == 1
    assert len(body["items"]) == 2


async def test_unknown_dataset_is_404(client: AsyncClient) -> None:
    r = await client.get(f"{DS}/{uuid4()}")
    assert r.status_code == 404 and r.json() == {"detail": "Dataset not found"}


async def test_create_get_patch_delete_test_case(
    client: AsyncClient, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    r = await client.post(
        base, json={"key": "case_001", "input": "Find the weather", "tags": ["a"]}
    )
    assert r.status_code == 201
    case = r.json()
    assert case["expected_behavior"] == [] and case["expected_output"] is None
    assert (await client.get(f"{base}/{case['id']}")).json()["key"] == "case_001"
    r = await client.patch(f"{base}/{case['id']}", json={"input": "changed"})
    assert r.status_code == 200 and r.json()["input"] == "changed"
    assert r.json()["updated_at"] > case["updated_at"]
    assert (await client.delete(f"{base}/{case['id']}")).status_code == 204
    assert (await client.get(f"{base}/{case['id']}")).status_code == 404


async def test_duplicate_case_key_is_409(client: AsyncClient, dataset: dict[str, Any]) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    await client.post(base, json={"key": "k", "input": "q"})
    r = await client.post(base, json={"key": "k", "input": "q"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Test case key already exists in this dataset"}


async def test_rename_case_key_to_existing_409(
    client: AsyncClient, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    await client.post(base, json={"key": "a", "input": "q"})
    b = (await client.post(base, json={"key": "b", "input": "q"})).json()
    r = await client.patch(f"{base}/{b['id']}", json={"key": "a"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Test case key already exists in this dataset"}
    assert (await client.get(f"{base}/{b['id']}")).json()["key"] == "b"


async def test_case_in_unknown_dataset_is_404(client: AsyncClient) -> None:
    r = await client.post(f"{DS}/{uuid4()}/test-cases", json={"key": "k", "input": "q"})
    assert r.status_code == 404
    assert (await client.get(f"{DS}/{uuid4()}/test-cases")).status_code == 404


async def test_case_is_scoped_to_its_dataset(client: AsyncClient, dataset: dict[str, Any]) -> None:
    case = (
        await client.post(f"{DS}/{dataset['id']}/test-cases", json={"key": "k", "input": "q"})
    ).json()
    other = (await client.post(DS, json={"name": "other"})).json()
    r = await client.get(f"{DS}/{other['id']}/test-cases/{case['id']}")
    assert r.status_code == 404 and r.json() == {"detail": "Test case not found"}


async def test_list_test_cases_paginates(client: AsyncClient, dataset: dict[str, Any]) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    for i in range(3):
        await client.post(base, json={"key": f"c{i}", "input": "q"})
    body = (await client.get(f"{base}?limit=2")).json()
    assert body["total"] == 3 and len(body["items"]) == 2 and body["limit"] == 2


async def test_bulk_create_test_cases(client: AsyncClient, dataset: dict[str, Any]) -> None:
    items = [{"key": f"case_{i:03}", "input": "q", "expected_behavior": ["b"]} for i in range(3)]
    r = await client.post(f"{DS}/{dataset['id']}/test-cases:bulk", json=items)
    assert r.status_code == 201
    assert [c["key"] for c in r.json()] == ["case_000", "case_001", "case_002"]
    assert (await client.get(f"{DS}/{dataset['id']}/test-cases")).json()["total"] == 3


async def test_bulk_duplicate_in_payload_inserts_nothing(
    client: AsyncClient, dataset: dict[str, Any]
) -> None:
    items = [{"key": "k", "input": "q"}, {"key": "k", "input": "q"}]
    r = await client.post(f"{DS}/{dataset['id']}/test-cases:bulk", json=items)
    assert r.status_code == 409
    assert r.json() == {"detail": "Duplicate test case key in request: k"}
    assert (await client.get(f"{DS}/{dataset['id']}/test-cases")).json()["total"] == 0


async def test_bulk_duplicate_against_existing_inserts_nothing(
    client: AsyncClient, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    await client.post(base, json={"key": "k", "input": "q"})
    items = [{"key": "new", "input": "q"}, {"key": "k", "input": "q"}]
    r = await client.post(f"{base}:bulk", json=items)
    assert r.status_code == 409
    assert r.json() == {"detail": "Test case key already exists in this dataset"}
    body = (await client.get(base)).json()
    assert body["total"] == 1 and body["items"][0]["key"] == "k"


async def test_bulk_unknown_dataset_is_404(client: AsyncClient) -> None:
    r = await client.post(f"{DS}/{uuid4()}/test-cases:bulk", json=[{"key": "k", "input": "q"}])
    assert r.status_code == 404


@pytest.mark.parametrize("payload", [[], [{"key": "has space", "input": "q"}]])
async def test_bulk_rejects_invalid_payload(
    client: AsyncClient, dataset: dict[str, Any], payload: list[Any]
) -> None:
    r = await client.post(f"{DS}/{dataset['id']}/test-cases:bulk", json=payload)
    assert r.status_code == 422


@pytest.mark.parametrize("key", ["has space", "", "x" * 101, "a/b"])
async def test_invalid_case_key_is_422(
    client: AsyncClient, dataset: dict[str, Any], key: str
) -> None:
    r = await client.post(f"{DS}/{dataset['id']}/test-cases", json={"key": key, "input": "q"})
    assert r.status_code == 422


async def test_empty_input_is_422(client: AsyncClient, dataset: dict[str, Any]) -> None:
    r = await client.post(f"{DS}/{dataset['id']}/test-cases", json={"key": "k", "input": ""})
    assert r.status_code == 422


async def _record_result(db_session: AsyncSession, dataset_id: str, case_id: str) -> None:
    version = await make_agent_version(db_session)
    run = EvaluationRun(agent_version_id=version.id, dataset_id=dataset_id)
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        EvaluationResult(
            evaluation_run_id=run.id,
            test_case_id=case_id,
            status="error",
            error="boom",
            case_key="k",
            case_input="q",
            case_expected_behavior=[],
            case_tags=[],
        )
    )
    await db_session.flush()


async def test_delete_case_referenced_by_result_is_409(
    client: AsyncClient, db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    case = (await client.post(base, json={"key": "k", "input": "q"})).json()
    await _record_result(db_session, dataset["id"], case["id"])
    r = await client.delete(f"{base}/{case['id']}")
    assert r.status_code == 409
    assert r.json() == {"detail": "Test case is referenced by evaluation history"}
    assert (await client.get(f"{base}/{case['id']}")).status_code == 200


async def test_delete_dataset_with_history_is_409(
    client: AsyncClient, db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    case = (await client.post(base, json={"key": "k", "input": "q"})).json()
    await _record_result(db_session, dataset["id"], case["id"])
    r = await client.delete(f"{DS}/{dataset['id']}")
    assert r.status_code == 409
    assert r.json() == {"detail": "Dataset is referenced by evaluation history"}
    assert (await client.get(f"{base}/{case['id']}")).status_code == 200


async def test_delete_dataset_cascades_unreferenced_cases(
    client: AsyncClient, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    await client.post(base, json={"key": "k", "input": "q"})
    assert (await client.delete(f"{DS}/{dataset['id']}")).status_code == 204
    assert (await client.get(f"{DS}/{dataset['id']}")).status_code == 404
    assert (await client.get(base)).status_code == 404


async def test_openapi_documents_dataset_endpoints(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert set(paths[DS]) == {"get", "post"}
    assert set(paths[f"{DS}/{{dataset_id}}"]) == {"get", "patch", "delete"}
    assert set(paths[f"{DS}/{{dataset_id}}/test-cases"]) == {"get", "post"}
    assert set(paths[f"{DS}/{{dataset_id}}/test-cases:bulk"]) == {"post"}
    assert set(paths[f"{DS}/{{dataset_id}}/test-cases/{{case_id}}"]) == {"get", "patch", "delete"}


async def test_bulk_over_1000_items_is_422(client: AsyncClient, dataset: dict[str, Any]) -> None:
    items = [{"key": f"c{i}", "input": "q"} for i in range(1001)]
    r = await client.post(f"{DS}/{dataset['id']}/test-cases:bulk", json=items)
    assert r.status_code == 422


async def test_delete_case_referenced_only_by_trace_is_409(
    client: AsyncClient, db_session: AsyncSession, dataset: dict[str, Any]
) -> None:
    base = f"{DS}/{dataset['id']}/test-cases"
    case = (await client.post(base, json={"key": "k", "input": "q"})).json()
    version = await make_agent_version(db_session)
    db_session.add(
        Trace(otel_trace_id=DEFAULT_TRACE_ID, agent_version_id=version.id, test_case_id=case["id"])
    )
    await db_session.flush()
    r = await client.delete(f"{base}/{case['id']}")
    assert r.status_code == 409
    assert r.json() == {"detail": "Test case is referenced by evaluation history"}
    assert (await client.get(f"{base}/{case['id']}")).status_code == 200
