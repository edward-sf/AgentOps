import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class Trace(UUIDPrimaryKey, Base):
    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("otel_trace_id", name="uq_traces_otel_trace_id"),
        CheckConstraint("status IN ('running', 'completed', 'failed')", name="status"),
    )

    otel_trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    evaluation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_runs.id", ondelete="RESTRICT"),
        index=True,
    )
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    input: Mapped[str | None] = mapped_column(Text)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Span(UUIDPrimaryKey, Base):
    __tablename__ = "spans"
    __table_args__ = (
        UniqueConstraint("trace_id", "otel_span_id", name="uq_spans_trace_id_otel_span_id"),
        CheckConstraint("kind IN ('llm', 'tool', 'chain', 'other')", name="kind"),
        CheckConstraint("status IN ('unset', 'ok', 'error')", name="status"),
    )

    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    otel_span_id: Mapped[str] = mapped_column(Text, nullable=False)
    parent_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spans.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="other")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unset")
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    input: Mapped[Any | None] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Metric(UUIDPrimaryKey, Base):
    __tablename__ = "metrics"

    trace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("traces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spans.id", ondelete="CASCADE"),
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
