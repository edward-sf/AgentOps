import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey

RUN_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
RESULT_STATUSES = ("completed", "failed", "error")
"""`completed`: the agent answered. `failed`: the agent's own run failed. Both have a trace.
`error`: the result says nothing about the agent (provider failure or our own bug); the
trace is optional (M2 spec, decision 3)."""


def _in(values: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class EvaluationRun(UUIDPrimaryKey, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(_in(RUN_STATUSES), name="status"),
        # The worker's claim query filters on status and takes the oldest first.
        Index("ix_evaluation_runs_status_created_at", "status", "created_at"),
    )

    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("datasets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on the first claim: how many cases the run executes (M2 spec, decision 8).
    total_cases: Mapped[int | None] = mapped_column(Integer)
    # Refreshed by the worker while it works; a stale heartbeat makes the run reclaimable.
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvaluationResult(UUIDPrimaryKey, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id", "test_case_id", name="uq_evaluation_results_run_case"
        ),
        CheckConstraint(_in(RESULT_STATUSES), name="status"),
        CheckConstraint("status = 'error' OR trace_id IS NOT NULL", name="trace_required"),
    )

    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluation_runs.id", ondelete="RESTRICT"), nullable=False
    )
    test_case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("traces.id", ondelete="RESTRICT"),
        index=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    # The agent's final answer, copied from the trace.
    output: Mapped[str | None] = mapped_column(Text)
    # The test case as it was when it ran (M2 spec, decision 2). Scoring reads these,
    # never the live case.
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    case_input: Mapped[str] = mapped_column(Text, nullable=False)
    case_expected_behavior: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    case_expected_output: Mapped[str | None] = mapped_column(Text)
    case_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    # Scoring columns, filled by M3. Empty until then.
    passed: Mapped[bool | None] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
