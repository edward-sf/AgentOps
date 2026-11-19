import json
import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.ingest import ingest_spans
from tests.factories import make_agent_version, span_record

T0 = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)


def _trace_id() -> str:
    return uuid.uuid4().hex


async def _ingest_trace(
    session: AsyncSession,
    version_id: uuid.UUID,
    *,
    started_at: datetime = T0,
    failed: bool = False,
) -> uuid.UUID:
    root = span_record(
        otel_trace_id=_trace_id(),
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=1),
        status="error" if failed else "ok",
        attributes={
            "agentops.span.kind": "chain",
            "agentops.agent_version_id": str(version_id),
            **({"agentops.error": "boom"} if failed else {}),
        },
    )
    [trace_id] = await ingest_spans(session, [root])
    return trace_id


async def test_get_trace_returns_tree(client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    otel_trace_id = _trace_id()
    root = span_record(
        otel_trace_id=otel_trace_id,
        otel_span_id="1000000000000001",
        attributes={
            "agentops.span.kind": "chain",
            "agentops.agent_version_id": str(version.id),
            "agentops.input": json.dumps("What's the weather in Paris?"),
            "agentops.output": json.dumps("Sunny."),
        },
    )
    # The tool span starts later but is ingested first: order must come from started_at.
    tool = span_record(
        otel_trace_id=otel_trace_id,
        otel_span_id="1000000000000003",
        parent_otel_span_id="1000000000000001",
        name="tool.get_weather",
        started_at=T0 + timedelta(milliseconds=500),
        attributes={
            "agentops.span.kind": "tool",
            "agentops.input": json.dumps({"city": "Paris"}),
            "agentops.output": json.dumps({"forecast": "sunny"}),
        },
    )
    llm = span_record(
        otel_trace_id=otel_trace_id,
        otel_span_id="1000000000000002",
        parent_otel_span_id="1000000000000001",
        name="chat claude-sonnet-4-5",
        started_at=T0 + timedelta(milliseconds=100),
        attributes={"agentops.span.kind": "llm", "gen_ai.usage.input_tokens": 12},
    )
    [trace_id] = await ingest_spans(db_session, [tool, llm, root])

    r = await client.get(f"/api/v1/traces/{trace_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(trace_id)
    assert body["otel_trace_id"] == otel_trace_id
    assert body["agent_version_id"] == str(version.id)
    assert body["status"] == "completed"
    assert body["input"] == "What's the weather in Paris?"
    assert body["output"] == "Sunny."
    assert len(body["spans"]) == 1
    top = body["spans"][0]
    assert top["kind"] == "chain"
    assert top["parent_otel_span_id"] is None
    assert top["input"] == "What's the weather in Paris?"
    assert top["output"] == "Sunny."
    assert [c["kind"] for c in top["children"]] == ["llm", "tool"]
    llm_node, tool_node = top["children"]
    assert llm_node["attributes"]["gen_ai.usage.input_tokens"] == 12
    assert llm_node["children"] == []
    assert tool_node["input"] == {"city": "Paris"}
    assert tool_node["output"] == {"forecast": "sunny"}
    assert tool_node["status"] == "ok" and tool_node["events"] == []


async def test_get_trace_orphan_span_is_root(client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    otel_trace_id = _trace_id()
    root = span_record(
        otel_trace_id=otel_trace_id,
        attributes={"agentops.span.kind": "chain", "agentops.agent_version_id": str(version.id)},
    )
    orphan = span_record(
        otel_trace_id=otel_trace_id,
        otel_span_id="2000000000000002",
        parent_otel_span_id="ffffffffffffffff",
        name="orphan",
        started_at=T0 + timedelta(milliseconds=10),
        attributes={"agentops.span.kind": "tool"},
    )
    [trace_id] = await ingest_spans(db_session, [root, orphan])

    r = await client.get(f"/api/v1/traces/{trace_id}")
    assert r.status_code == 200
    spans = r.json()["spans"]
    assert [s["name"] for s in spans] == ["agent.run", "orphan"]
    assert spans[1]["parent_otel_span_id"] == "ffffffffffffffff"
    assert spans[0]["children"] == []


async def test_get_trace_404(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/traces/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json() == {"detail": "Trace not found"}


async def test_list_traces_filters_and_orders(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    v1 = await make_agent_version(db_session)
    v2 = await make_agent_version(db_session)
    older = await _ingest_trace(db_session, v1.id, started_at=T0)
    newer = await _ingest_trace(db_session, v1.id, started_at=T0 + timedelta(minutes=5))
    other = await _ingest_trace(db_session, v2.id, started_at=T0, failed=True)

    r = await client.get("/api/v1/traces", params={"agent_version_id": str(v1.id)})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["items"]] == [str(newer), str(older)]
    assert body["total"] == 2
    assert "spans" not in body["items"][0]

    r = await client.get("/api/v1/traces", params={"status": "failed"})
    ids = [i["id"] for i in r.json()["items"]]
    assert str(other) in ids and str(older) not in ids and str(newer) not in ids
    assert all(i["status"] == "failed" for i in r.json()["items"])

    r = await client.get(
        "/api/v1/traces", params={"agent_version_id": str(v2.id), "status": "failed"}
    )
    item = r.json()["items"][0]
    assert r.json()["total"] == 1 and item["id"] == str(other) and item["error"] == "boom"

    r = await client.get("/api/v1/traces", params={"status": "bogus"})
    assert r.status_code == 422


async def test_list_traces_pagination(client: AsyncClient, db_session: AsyncSession) -> None:
    version = await make_agent_version(db_session)
    ids = [
        await _ingest_trace(db_session, version.id, started_at=T0 + timedelta(minutes=i))
        for i in range(3)
    ]
    r = await client.get(
        "/api/v1/traces", params={"agent_version_id": str(version.id), "limit": 1, "offset": 1}
    )
    body = r.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 3 and body["limit"] == 1 and body["offset"] == 1
    assert [i["id"] for i in body["items"]] == [str(ids[1])]

    default = (await client.get("/api/v1/traces")).json()
    assert default["limit"] == 50 and default["offset"] == 0

    assert (await client.get("/api/v1/traces?limit=201")).status_code == 422
    assert (await client.get("/api/v1/traces?offset=-1")).status_code == 422


async def test_get_trace_parent_cycle_keeps_every_span(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    version = await make_agent_version(db_session)
    otel_trace_id = _trace_id()
    root = span_record(
        otel_trace_id=otel_trace_id,
        attributes={"agentops.span.kind": "chain", "agentops.agent_version_id": str(version.id)},
    )
    a = span_record(
        otel_trace_id=otel_trace_id,
        otel_span_id="3000000000000001",
        parent_otel_span_id="3000000000000002",
        name="a",
        started_at=T0 + timedelta(milliseconds=1),
    )
    b = span_record(
        otel_trace_id=otel_trace_id,
        otel_span_id="3000000000000002",
        parent_otel_span_id="3000000000000001",
        name="b",
        started_at=T0 + timedelta(milliseconds=2),
    )
    [trace_id] = await ingest_spans(db_session, [root, a, b])

    r = await client.get(f"/api/v1/traces/{trace_id}")
    assert r.status_code == 200
    spans = r.json()["spans"]
    assert [s["name"] for s in spans] == ["agent.run", "a"]
    assert [c["name"] for c in spans[1]["children"]] == ["b"]
