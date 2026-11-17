import uuid

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey


class Dataset(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("name", name="uq_datasets_name"),)

    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class TestCase(UUIDPrimaryKey, Timestamps, Base):
    __test__ = False  # not a pytest class
    __tablename__ = "test_cases"
    __table_args__ = (UniqueConstraint("dataset_id", "key", name="uq_test_cases_dataset_id_key"),)

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expected_behavior: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    expected_output: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
