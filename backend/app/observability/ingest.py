"""The only way spans reach the database (M1 spec, decisions 1, 2 and 10).

`ingest_spans` turns finished spans into Trace and Span rows. It accepts spans in
any order, and re-ingesting a batch changes nothing. Trace fields come only from
the root span's data, so an OTLP receiver could feed this same function later.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import StatusCode
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentVersion, Span, Trace
from app.observability import attributes as attr

SpanStatus = Literal["unset", "ok", "error"]

_STATUS_CODES: dict[StatusCode, SpanStatus] = {
    StatusCode.UNSET: "unset",
    StatusCode.OK: "ok",
    StatusCode.ERROR: "error",
}


class IngestError(Exception):
    """The batch breaks the span contract. Nothing from it was stored."""


@dataclass(frozen=True)
class SpanRecord:
    """One finished span. IDs are lowercase hex: 32 chars for traces, 16 for spans.

    Each event is `{"name": str, "timestamp": ISO 8601 str, "attributes": dict}`.
    """

    otel_trace_id: str
    otel_span_id: str
    parent_otel_span_id: str | None
    name: str
    started_at: datetime
    ended_at: datetime
    status: SpanStatus
    status_message: str | None
    attributes: dict[str, Any]
    events: list[dict[str, Any]]


def _from_ns(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, tz=UTC)


def _plain(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """OTel attribute values may be tuples; JSONB wants lists."""
    return {
        key: list(value) if isinstance(value, tuple) else value
        for key, value in (attributes or {}).items()
    }


def from_readable_span(span: ReadableSpan) -> SpanRecord:
    context = span.context
    if context is None or span.start_time is None or span.end_time is None:
        raise IngestError(f"span {span.name!r} has no context or has not ended")
    return SpanRecord(
        otel_trace_id=format(context.trace_id, "032x"),
        otel_span_id=format(context.span_id, "016x"),
        parent_otel_span_id=format(span.parent.span_id, "016x") if span.parent else None,
        name=span.name,
        started_at=_from_ns(span.start_time),
        ended_at=_from_ns(span.end_time),
        status=_STATUS_CODES[span.status.status_code],
        status_message=span.status.description,
        attributes=_plain(span.attributes),
        events=[
            {
                "name": event.name,
                "timestamp": _from_ns(event.timestamp).isoformat(),
                "attributes": _plain(event.attributes),
            }
            for event in span.events
        ],
    )


def _decode(record: SpanRecord, key: str) -> Any:
    raw = record.attributes.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise IngestError(f"span {record.otel_span_id}: {key} must be a JSON-encoded string")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IngestError(f"span {record.otel_span_id}: {key} is not valid JSON") from exc


def _kind(record: SpanRecord) -> str:
    kind = record.attributes.get(attr.SPAN_KIND)
    if kind is None:
        operation = record.attributes.get(attr.GEN_AI_OPERATION)
        return attr.GEN_AI_OPERATION_KINDS.get(str(operation), "other")
    if kind not in attr.SPAN_KINDS:
        raise IngestError(f"span {record.otel_span_id}: unknown {attr.SPAN_KIND} {kind!r}")
    return str(kind)


def _uuid_attribute(root: SpanRecord, key: str) -> uuid.UUID | None:
    raw = root.attributes.get(key)
    if raw is None:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise IngestError(f"root span {root.otel_span_id}: {key} {raw!r} is not a UUID") from exc


def _agent_version_id(root: SpanRecord) -> uuid.UUID:
    version_id = _uuid_attribute(root, attr.AGENT_VERSION_ID)
    if version_id is None:
        raise IngestError(f"root span {root.otel_span_id}: {attr.AGENT_VERSION_ID} is missing")
    return version_id


def _trace_values(root: SpanRecord) -> dict[str, Any]:
    trace_input = _decode(root, attr.INPUT)
    trace_output = _decode(root, attr.OUTPUT)
    error = root.attributes.get(attr.ERROR)
    if error is None and root.status == "error":
        error = root.status_message
    return {
        "otel_trace_id": root.otel_trace_id,
        "agent_version_id": _agent_version_id(root),
        "evaluation_run_id": _uuid_attribute(root, attr.EVALUATION_RUN_ID),
        "test_case_id": _uuid_attribute(root, attr.TEST_CASE_ID),
        "status": "failed" if root.status == "error" else "completed",
        "input": trace_input if isinstance(trace_input, str) else None,
        "output": trace_output if isinstance(trace_output, str) else None,
        "error": None if error is None else str(error),
        "started_at": root.started_at,
        "ended_at": root.ended_at,
    }


def _span_values(record: SpanRecord) -> dict[str, Any]:
    """Row values for a span, minus `trace_id`."""
    return {
        "otel_span_id": record.otel_span_id,
        "parent_otel_span_id": record.parent_otel_span_id,
        "name": record.name,
        "kind": _kind(record),
        "status": record.status,
        "attributes": {
            k: v for k, v in record.attributes.items() if k not in (attr.INPUT, attr.OUTPUT)
        },
        "input": _decode(record, attr.INPUT),
        "output": _decode(record, attr.OUTPUT),
        "error": record.status_message if record.status == "error" else None,
        "events": record.events,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
    }


async def ingest_spans(session: AsyncSession, records: Sequence[SpanRecord]) -> list[uuid.UUID]:
    """Store `records` and return the IDs of the trace rows they touched, in batch order.

    A trace is created from its root span (`parent_otel_span_id is None`). Spans of a
    trace that isn't stored yet need that root in the same batch, or the whole batch
    raises `IngestError` and nothing is stored. Flushes; never commits.
    """
    by_trace: dict[str, dict[str, SpanRecord]] = {}
    for record in records:
        by_trace.setdefault(record.otel_trace_id, {}).setdefault(record.otel_span_id, record)

    # Validate and map everything before writing anything.
    span_values = {
        otel_trace_id: [_span_values(record) for record in spans.values()]
        for otel_trace_id, spans in by_trace.items()
    }
    new_traces: dict[str, dict[str, Any]] = {}
    for otel_trace_id, spans in by_trace.items():
        roots = [s for s in spans.values() if s.parent_otel_span_id is None]
        if len(roots) > 1:
            raise IngestError(f"trace {otel_trace_id} has more than one root span")
        if roots:
            new_traces[otel_trace_id] = _trace_values(roots[0])
    existing = await _trace_ids(session, by_trace.keys())
    for otel_trace_id in by_trace:
        if otel_trace_id not in existing and otel_trace_id not in new_traces:
            raise IngestError(f"trace {otel_trace_id} is not stored and its root span is missing")
    version_ids = {v["agent_version_id"] for v in new_traces.values()}
    if version_ids:
        found = set(
            (
                await session.execute(
                    select(AgentVersion.id).where(AgentVersion.id.in_(version_ids))
                )
            ).scalars()
        )
        if missing := version_ids - found:
            raise IngestError(f"unknown agent version(s): {sorted(map(str, missing))}")

    async with session.begin_nested():
        to_insert = [v for k, v in new_traces.items() if k not in existing]
        if to_insert:
            await session.execute(
                insert(Trace)
                .values(to_insert)
                .on_conflict_do_nothing(constraint="uq_traces_otel_trace_id")
            )
            existing = await _trace_ids(session, by_trace.keys())
        span_rows = [
            {**values, "trace_id": existing[otel_trace_id]}
            for otel_trace_id, rows in span_values.items()
            for values in rows
        ]
        if span_rows:
            await session.execute(
                insert(Span)
                .values(span_rows)
                .on_conflict_do_nothing(constraint="uq_spans_trace_id_otel_span_id")
            )
    await session.flush()
    return [existing[otel_trace_id] for otel_trace_id in by_trace]


async def _trace_ids(session: AsyncSession, otel_trace_ids: Any) -> dict[str, uuid.UUID]:
    rows = await session.execute(
        select(Trace.otel_trace_id, Trace.id).where(Trace.otel_trace_id.in_(list(otel_trace_ids)))
    )
    return {otel_trace_id: trace_id for otel_trace_id, trace_id in rows}
