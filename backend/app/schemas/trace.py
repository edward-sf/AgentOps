import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TraceStatus = Literal["running", "completed", "failed"]


class TraceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    otel_trace_id: str
    agent_version_id: uuid.UUID
    evaluation_run_id: uuid.UUID | None
    test_case_id: uuid.UUID | None
    status: str
    input: str | None
    output: str | None
    error: str | None
    started_at: datetime
    ended_at: datetime | None


class SpanNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    otel_span_id: str
    parent_otel_span_id: str | None
    name: str
    kind: str
    status: str
    attributes: dict[str, Any]
    events: list[dict[str, Any]]
    input: Any | None
    output: Any | None
    error: str | None
    started_at: datetime
    ended_at: datetime | None
    children: list["SpanNode"] = Field(default_factory=list)


class TraceDetail(TraceRead):
    spans: list[SpanNode]
