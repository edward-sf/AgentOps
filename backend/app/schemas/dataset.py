import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DatasetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CaseKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]


class DatasetCreate(BaseModel):
    name: DatasetName
    description: str | None = None


class DatasetUpdate(BaseModel):
    name: DatasetName | None = None
    description: str | None = None


class DatasetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class TestCaseCreate(BaseModel):
    __test__ = False  # not a pytest class

    key: CaseKey
    input: Annotated[str, StringConstraints(min_length=1)]
    expected_behavior: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    tags: list[str] = Field(default_factory=list)


class TestCaseUpdate(BaseModel):
    __test__ = False

    key: CaseKey | None = None
    input: Annotated[str, StringConstraints(min_length=1)] | None = None
    expected_behavior: list[str] | None = None
    expected_output: str | None = None
    tags: list[str] | None = None


class TestCaseRead(BaseModel):
    __test__ = False

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    dataset_id: uuid.UUID
    key: str
    input: str
    expected_behavior: list[str]
    expected_output: str | None
    tags: list[str]
    created_at: datetime
    updated_at: datetime
