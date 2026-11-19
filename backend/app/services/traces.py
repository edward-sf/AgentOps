import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Span, Trace
from app.schemas.trace import SpanNode, TraceDetail, TraceRead


async def list_traces(
    session: AsyncSession,
    *,
    agent_version_id: uuid.UUID | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Trace], int]:
    conditions = []
    if agent_version_id is not None:
        conditions.append(Trace.agent_version_id == agent_version_id)
    if status is not None:
        conditions.append(Trace.status == status)
    total = await session.scalar(select(func.count()).select_from(Trace).where(*conditions)) or 0
    result = await session.scalars(
        select(Trace)
        .where(*conditions)
        .order_by(Trace.started_at.desc(), Trace.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result), total


async def get_trace_detail(session: AsyncSession, trace_id: uuid.UUID) -> TraceDetail | None:
    trace = await session.get(Trace, trace_id)
    if trace is None:
        return None
    spans = await session.scalars(select(Span).where(Span.trace_id == trace_id))
    return TraceDetail(
        **TraceRead.model_validate(trace).model_dump(), spans=build_span_tree(list(spans))
    )


def build_span_tree(spans: Sequence[Span]) -> list[SpanNode]:
    """Nest spans by `parent_otel_span_id` within one trace; siblings by `started_at`.

    A span whose parent isn't among `spans` (an orphan) is a root. Spans caught in a
    parent cycle are promoted to roots too, so no stored span is ever dropped.
    """
    ordered = sorted(spans, key=lambda s: (s.started_at, s.otel_span_id))
    by_id = {s.otel_span_id: s for s in ordered}
    children: dict[str, list[Span]] = {}
    roots: list[Span] = []
    for span in ordered:
        parent = span.parent_otel_span_id
        if parent is None or parent not in by_id or parent == span.otel_span_id:
            roots.append(span)
        else:
            children.setdefault(parent, []).append(span)

    placed: set[str] = set()

    def node(span: Span) -> SpanNode:
        placed.add(span.otel_span_id)
        kids = [
            node(c) for c in children.get(span.otel_span_id, []) if c.otel_span_id not in placed
        ]
        return SpanNode.model_validate(span).model_copy(update={"children": kids})

    result = [node(root) for root in roots]
    for span in ordered:  # Only spans in a parent cycle are still unplaced.
        if span.otel_span_id not in placed:
            result.append(node(span))
    result.sort(key=lambda n: (n.started_at, n.otel_span_id))
    return result
