import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

EvaluationRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
EvaluationResultStatus = Literal["completed", "failed", "error"]


class EvaluationRunCreate(BaseModel):
    agent_version_id: uuid.UUID
    dataset_id: uuid.UUID


class ResultCounts(BaseModel):
    """How many results the run has, by status. Every status is present, zero included."""

    completed: int = 0
    failed: int = 0
    error: int = 0


class EvaluationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_version_id: uuid.UUID
    dataset_id: uuid.UUID
    status: EvaluationRunStatus
    error: str | None
    total_cases: int | None
    """Set by the runner when it starts executing the run; null until then."""
    cancel_requested: bool
    started_at: datetime | None
    completed_at: datetime | None
    heartbeat_at: datetime | None
    created_at: datetime
    counts: ResultCounts


class EvaluationResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    evaluation_run_id: uuid.UUID
    test_case_id: uuid.UUID
    trace_id: uuid.UUID | None
    status: EvaluationResultStatus
    error: str | None
    output: str | None
    # The test case as it was when it ran (M2 spec, decision 2).
    case_key: str
    case_input: str
    case_expected_behavior: list[str]
    case_expected_output: str | None
    case_tags: list[str]
    # Filled by M3's scoring; empty in M2.
    passed: bool | None
    score: float | None
    scores: dict[str, Any]
    explanation: str | None
    created_at: datetime
