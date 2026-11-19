import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

AgentName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class AgentCreate(BaseModel):
    name: AgentName
    description: str | None = None


class AgentUpdate(BaseModel):
    name: AgentName | None = None
    description: str | None = None


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class AgentVersionCreate(BaseModel):
    label: str | None = None
    provider: Literal["anthropic", "openai"]
    model: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    system_prompt: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)


class AgentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_id: uuid.UUID
    version: int
    label: str | None
    provider: str
    model: str
    system_prompt: str
    parameters: dict[str, Any]
    tools: list[str]
    created_at: datetime
