"""evaluation engine: run queue columns, result status and case snapshot

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19 00:00:00

Runs gain the columns the worker's queue needs (M2 spec 6.3, decisions 6-8).
Results gain a status, the agent's output and a snapshot of the test case as it
ran (decisions 2 and 3), and `passed` becomes nullable until M3 scores them.

Downgrading refuses while any result is unscored (`passed IS NULL`): the M1 shape
has nowhere to put it. Delete those results first.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("evaluation_runs", sa.Column("total_cases", sa.Integer(), nullable=True))
    op.add_column(
        "evaluation_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "evaluation_runs",
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_index(
        "ix_evaluation_runs_status_created_at", "evaluation_runs", ["status", "created_at"]
    )

    # Existing results predate statuses and snapshots. M1 never wrote any, but a
    # row that exists is backfilled from its case so NOT NULL holds.
    for name, type_ in [
        ("status", sa.Text()),
        ("error", sa.Text()),
        ("output", sa.Text()),
        ("case_key", sa.Text()),
        ("case_input", sa.Text()),
        ("case_expected_behavior", _JSONB),
        ("case_expected_output", sa.Text()),
        ("case_tags", _JSONB),
    ]:
        op.add_column("evaluation_results", sa.Column(name, type_, nullable=True))
    op.execute(
        """
        UPDATE evaluation_results r SET
            status = CASE WHEN r.trace_id IS NULL THEN 'error' ELSE 'completed' END,
            case_key = c.key,
            case_input = c.input,
            case_expected_behavior = c.expected_behavior,
            case_expected_output = c.expected_output,
            case_tags = c.tags
        FROM test_cases c
        WHERE c.id = r.test_case_id
        """
    )
    for name in ("status", "case_key", "case_input", "case_expected_behavior", "case_tags"):
        op.alter_column("evaluation_results", name, nullable=False)
    op.alter_column("evaluation_results", "passed", existing_type=sa.Boolean(), nullable=True)
    op.create_check_constraint(
        op.f("ck_evaluation_results_status"),
        "evaluation_results",
        "status IN ('completed', 'failed', 'error')",
    )
    op.create_check_constraint(
        op.f("ck_evaluation_results_trace_required"),
        "evaluation_results",
        "status = 'error' OR trace_id IS NOT NULL",
    )


def downgrade() -> None:
    unscored = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM evaluation_results WHERE passed IS NULL"))
        .scalar_one()
    )
    if unscored:
        raise RuntimeError(
            f"cannot downgrade 0003: {unscored} evaluation result(s) have passed IS NULL, "
            "which the pre-0003 schema does not allow. Delete or score them first."
        )
    op.drop_constraint(
        op.f("ck_evaluation_results_trace_required"), "evaluation_results", type_="check"
    )
    op.drop_constraint(op.f("ck_evaluation_results_status"), "evaluation_results", type_="check")
    op.alter_column("evaluation_results", "passed", existing_type=sa.Boolean(), nullable=False)
    for name in (
        "case_tags",
        "case_expected_output",
        "case_expected_behavior",
        "case_input",
        "case_key",
        "output",
        "error",
        "status",
    ):
        op.drop_column("evaluation_results", name)

    op.drop_index("ix_evaluation_runs_status_created_at", table_name="evaluation_runs")
    op.drop_column("evaluation_runs", "cancel_requested")
    op.drop_column("evaluation_runs", "heartbeat_at")
    op.drop_column("evaluation_runs", "total_cases")
