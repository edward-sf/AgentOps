import uuid
from typing import Literal

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    agent_version_id: uuid.UUID
    input: str = Field(min_length=1)


class RunRead(BaseModel):
    trace_id: uuid.UUID
    status: Literal["completed", "failed"]
