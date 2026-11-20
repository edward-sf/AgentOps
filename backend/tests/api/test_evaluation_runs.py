"""Evaluation runs API (M2 spec 6.4). Nothing here executes a case or calls a model API."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_provider_factory
from app.config import Settings
from app.models import EvaluationResult, EvaluationRun, TestCase
from tests.factories import make_agent_version, make_dataset, make_evaluation_run, make_trace

BASE = "/api/v1/evaluation-runs"


@pytest.fixture
def settings() -> Settings:
    """No API key: submitting a run never needs one, since a missing key is the worker's
    concern (D4)."""
    base = Settings()
    return base.model_copy(
        update={"database_url": base.test_database_url, "anthropic_api_key": None}
    )


async def run_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(EvaluationRun)) or 0


async def make_result(
    session: AsyncSession, run: EvaluationRun, case: TestCase, status: str = "completed"
) -> EvaluationResult:
    trace = None
    if status != "error":
        trace = await make_trace(
            session, evaluation_run_id=run.id, test_case_id=case.id, output=f"answer {case.key}"
        )
    result = EvaluationResult(
        evaluation_run_id=run.id,
        test_case_id=case.id,
        trace_id=trace.id if trace else None,
        status=status,
        error="boom" if status == "error" else None,
        output=trace.output if trace else None,
        case_key=case.key,
        case_input=case.input,
        case_expected_behavior=list(case.expected_behavior),
        case_expected_output=case.expected_output,
        case_tags=list(case.tags),
    )
    session.add(result)
    await session.flush()
    return result


async def cases_of(session: AsyncSession, dataset_id: uuid.UUID) -> list[TestCase]:
    rows = await session.scalars(
        select(TestCase).where(TestCase.dataset_id == dataset_id).order_by(TestCase.key)
    )
    return list(rows)


# --- submit -----------------------------------------------------------------------------


async def test_submit_returns_202_pending(
    app: FastAPI, client: AsyncClient, db_session: AsyncSession
) -> None:
    def refuse(name: str) -> Any:
        raise AssertionError("provider_for must not be called at submit time")

    app.dependency_overrides[get_provider_factory] = lambda: refuse
    version = await make_agent_version(db_session)
    dataset = await make_dataset(db_session, cases=2)

    r = await client.post(
        BASE, json={"agent_version_id": str(version.id), "dataset_id": str(dataset.id)}
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["agent_version_id"] == str(version.id)
    assert body["dataset_id"] == str(dataset.id)
    assert body["cancel_requested"] is False
    assert body["total_cases"] is None
    assert body["started_at"] is None and body["completed_at"] is None
    assert body["counts"] == {"completed": 0, "failed": 0, "error": 0}
    assert await run_count(db_session) == 1


async def test_submit_unknown_version_404(client: AsyncClient, db_session: AsyncSession) -> None:
    dataset = await make_dataset(db_session)
    r = await client.post(
        BASE, json={"agent_version_id": str(uuid.uuid4()), "dataset_id": str(dataset.id)}
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Agent version not found"}
    assert await run_count(db_session) == 0


async def test_submit_unknown_dataset_404(client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    r = await client.post(
        BASE, json={"agent_version_id": str(version.id), "dataset_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Dataset not found"}
    assert await run_count(db_session) == 0


async def test_submit_empty_dataset_422(client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    dataset = await make_dataset(db_session, cases=0)
    r = await client.post(
        BASE, json={"agent_version_id": str(version.id), "dataset_id": str(dataset.id)}
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "Dataset has no test cases"}
    assert await run_count(db_session) == 0


async def test_submit_unknown_tool_422(client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session, tools=["get_weather", "launch_rockets"])
    dataset = await make_dataset(db_session)
    r = await client.post(
        BASE, json={"agent_version_id": str(version.id), "dataset_id": str(dataset.id)}
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "unknown tool: launch_rockets"}
    assert await run_count(db_session) == 0


async def test_submit_unsupported_provider_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    version = await make_agent_version(db_session, provider="openai")
    dataset = await make_dataset(db_session)
    r = await client.post(
        BASE, json={"agent_version_id": str(version.id), "dataset_id": str(dataset.id)}
    )
    assert r.status_code == 422
    assert r.json() == {"detail": "unsupported provider: openai"}
    assert await run_count(db_session) == 0


async def test_submit_invalid_parameters_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    version = await make_agent_version(db_session, parameters={"temperature": 0.2})
    dataset = await make_dataset(db_session)
    r = await client.post(
        BASE, json={"agent_version_id": str(version.id), "dataset_id": str(dataset.id)}
    )
    assert r.status_code == 422
    assert "unsupported parameter(s): temperature" in r.json()["detail"]
    assert await run_count(db_session) == 0


# --- list and detail --------------------------------------------------------------------


async def test_list_filters_and_order(client: AsyncClient, db_session: AsyncSession) -> None:
    v1 = await make_agent_version(db_session)
    v2 = await make_agent_version(db_session)
    d1 = await make_dataset(db_session)
    d2 = await make_dataset(db_session)
    t0 = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)
    oldest = await make_evaluation_run(
        db_session, version=v1, dataset=d1, created_at=t0.replace(hour=9)
    )
    middle = await make_evaluation_run(
        db_session, version=v2, dataset=d1, status="running", created_at=t0.replace(hour=10)
    )
    newest = await make_evaluation_run(
        db_session, version=v1, dataset=d2, created_at=t0.replace(hour=11)
    )

    async def ids(**params: str) -> list[str]:
        r = await client.get(BASE, params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == len(body["items"])
        assert body["limit"] == 50 and body["offset"] == 0
        return [item["id"] for item in body["items"]]

    assert await ids() == [str(newest.id), str(middle.id), str(oldest.id)]
    assert await ids(agent_version_id=str(v1.id)) == [str(newest.id), str(oldest.id)]
    assert await ids(dataset_id=str(d1.id)) == [str(middle.id), str(oldest.id)]
    assert await ids(status="running") == [str(middle.id)]
    assert await ids(agent_version_id=str(v1.id), dataset_id=str(d2.id)) == [str(newest.id)]

    r = await client.get(BASE, params={"limit": 1, "offset": 1})
    assert r.json()["total"] == 3
    assert [i["id"] for i in r.json()["items"]] == [str(middle.id)]

    assert (await client.get(BASE, params={"status": "bogus"})).status_code == 422
    assert (await client.get(BASE, params={"limit": 201})).status_code == 422


async def test_list_items_include_counts(client: AsyncClient, db_session: AsyncSession) -> None:
    dataset = await make_dataset(db_session, cases=2)
    run = await make_evaluation_run(db_session, dataset=dataset, status="running", total_cases=2)
    cases = await cases_of(db_session, dataset.id)
    await make_result(db_session, run, cases[0], "failed")

    r = await client.get(BASE)
    [item] = r.json()["items"]
    assert item["counts"] == {"completed": 0, "failed": 1, "error": 0}


async def test_detail_counts_zero_filled(client: AsyncClient, db_session: AsyncSession) -> None:
    dataset = await make_dataset(db_session, cases=3)
    run = await make_evaluation_run(db_session, dataset=dataset, status="running", total_cases=3)

    r = await client.get(f"{BASE}/{run.id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_cases"] == 3
    assert body["counts"] == {"completed": 0, "failed": 0, "error": 0}

    cases = await cases_of(db_session, dataset.id)
    await make_result(db_session, run, cases[0], "completed")
    await make_result(db_session, run, cases[1], "error")
    body = (await client.get(f"{BASE}/{run.id}")).json()
    assert body["counts"] == {"completed": 1, "failed": 0, "error": 1}


async def test_detail_unknown_404(client: AsyncClient) -> None:
    r = await client.get(f"{BASE}/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json() == {"detail": "Evaluation run not found"}


# --- results ----------------------------------------------------------------------------


async def test_results_ordered_by_case_key(client: AsyncClient, db_session: AsyncSession) -> None:
    dataset = await make_dataset(db_session, cases=3)
    run = await make_evaluation_run(db_session, dataset=dataset, status="running", total_cases=3)
    cases = await cases_of(db_session, dataset.id)
    # Inserted out of order.
    for case in (cases[2], cases[0], cases[1]):
        await make_result(db_session, run, case)

    r = await client.get(f"{BASE}/{run.id}/results")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 3
    assert [i["case_key"] for i in body["items"]] == ["case_001", "case_002", "case_003"]
    item = body["items"][0]
    assert item["status"] == "completed"
    assert item["output"] == "answer case_001"
    assert item["error"] is None
    assert item["trace_id"] is not None
    assert item["test_case_id"] == str(cases[0].id)
    assert item["evaluation_run_id"] == str(run.id)
    assert item["case_input"] == "Question 1?"
    assert item["case_expected_behavior"] == ["does the thing"]
    assert item["case_expected_output"] is None
    assert item["case_tags"] == []
    # M3's scoring columns are present but empty.
    assert item["passed"] is None and item["score"] is None and item["explanation"] is None
    assert item["scores"] == {}


async def test_results_filter_status(client: AsyncClient, db_session: AsyncSession) -> None:
    dataset = await make_dataset(db_session, cases=3)
    run = await make_evaluation_run(db_session, dataset=dataset, status="running", total_cases=3)
    cases = await cases_of(db_session, dataset.id)
    await make_result(db_session, run, cases[0], "completed")
    await make_result(db_session, run, cases[1], "error")
    await make_result(db_session, run, cases[2], "error")

    r = await client.get(f"{BASE}/{run.id}/results", params={"status": "error"})
    body = r.json()
    assert body["total"] == 2
    assert [i["case_key"] for i in body["items"]] == ["case_002", "case_003"]
    assert all(i["trace_id"] is None and i["error"] == "boom" for i in body["items"])

    r = await client.get(f"{BASE}/{run.id}/results", params={"status": "pending"})
    assert r.status_code == 422


async def test_results_unknown_run_404(client: AsyncClient) -> None:
    r = await client.get(f"{BASE}/{uuid.uuid4()}/results")
    assert r.status_code == 404
    assert r.json() == {"detail": "Evaluation run not found"}


# --- cancel -----------------------------------------------------------------------------


async def test_cancel_pending(client: AsyncClient, db_session: AsyncSession) -> None:
    run = await make_evaluation_run(db_session)

    r = await client.post(f"{BASE}/{run.id}/cancel")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["completed_at"] is not None
    assert body["counts"] == {"completed": 0, "failed": 0, "error": 0}


async def test_cancel_running_sets_flag(client: AsyncClient, db_session: AsyncSession) -> None:
    run = await make_evaluation_run(
        db_session, status="running", started_at=datetime.now(UTC), total_cases=3
    )

    r = await client.post(f"{BASE}/{run.id}/cancel")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "running"
    assert body["cancel_requested"] is True
    assert body["completed_at"] is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_cancel_finished_409(
    client: AsyncClient, db_session: AsyncSession, status: str
) -> None:
    run = await make_evaluation_run(db_session, status=status, completed_at=datetime.now(UTC))

    r = await client.post(f"{BASE}/{run.id}/cancel")

    assert r.status_code == 409
    assert r.json() == {"detail": f"Evaluation run is already {status}"}
    await db_session.refresh(run)
    assert run.status == status
    assert run.cancel_requested is False


async def test_cancel_unknown_404(client: AsyncClient) -> None:
    r = await client.post(f"{BASE}/{uuid.uuid4()}/cancel")
    assert r.status_code == 404
    assert r.json() == {"detail": "Evaluation run not found"}
