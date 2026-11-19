"""span parent by otel span id, span events

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19 00:00:00

Spans link to their parent by the parent's OTel span ID (text, no foreign key),
resolved within the same trace (M1 spec, decision 1). Downgrading restores the
M0 shape; parent links are lost.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("fk_spans_parent_span_id_spans"), "spans", type_="foreignkey")
    op.drop_index(op.f("ix_spans_parent_span_id"), table_name="spans")
    op.drop_column("spans", "parent_span_id")
    op.add_column("spans", sa.Column("parent_otel_span_id", sa.Text(), nullable=True))
    op.add_column(
        "spans",
        sa.Column(
            "events",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("spans", "events")
    op.drop_column("spans", "parent_otel_span_id")
    op.add_column("spans", sa.Column("parent_span_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_spans_parent_span_id_spans"),
        "spans",
        "spans",
        ["parent_span_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_spans_parent_span_id"), "spans", ["parent_span_id"])
