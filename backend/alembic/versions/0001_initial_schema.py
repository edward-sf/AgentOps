"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-17 01:45:39.094784

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agents",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("name", name="uq_agents_name"),
    )
    op.create_table(
        "datasets",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasets")),
        sa.UniqueConstraint("name", name="uq_datasets_name"),
    )
    op.create_table(
        "agent_versions",
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "tools", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "provider IN ('anthropic', 'openai')", name=op.f("ck_agent_versions_provider")
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name=op.f("fk_agent_versions_agent_id_agents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_id_version"),
    )
    op.create_table(
        "test_cases",
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column(
            "expected_behavior",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("expected_output", sa.Text(), nullable=True),
        sa.Column(
            "tags", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_test_cases_dataset_id_datasets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_cases")),
        sa.UniqueConstraint("dataset_id", "key", name="uq_test_cases_dataset_id_key"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("dataset_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name=op.f("ck_evaluation_runs_status"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            name=op.f("fk_evaluation_runs_agent_version_id_agent_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            name=op.f("fk_evaluation_runs_dataset_id_datasets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_runs")),
    )
    op.create_table(
        "traces",
        sa.Column("otel_trace_id", sa.Text(), nullable=False),
        sa.Column("agent_version_id", sa.UUID(), nullable=False),
        sa.Column("evaluation_run_id", sa.UUID(), nullable=True),
        sa.Column("test_case_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), server_default="running", nullable=False),
        sa.Column("input", sa.Text(), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name=op.f("ck_traces_status")
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            name=op.f("fk_traces_agent_version_id_agent_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_traces_evaluation_run_id_evaluation_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name=op.f("fk_traces_test_case_id_test_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_traces")),
        sa.UniqueConstraint("otel_trace_id", name="uq_traces_otel_trace_id"),
    )
    op.create_table(
        "evaluation_results",
        sa.Column("evaluation_run_id", sa.UUID(), nullable=False),
        sa.Column("test_case_id", sa.UUID(), nullable=False),
        sa.Column("trace_id", sa.UUID(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "scores", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["evaluation_runs.id"],
            name=op.f("fk_evaluation_results_evaluation_run_id_evaluation_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["test_case_id"],
            ["test_cases.id"],
            name=op.f("fk_evaluation_results_test_case_id_test_cases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"],
            ["traces.id"],
            name=op.f("fk_evaluation_results_trace_id_traces"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_results")),
        sa.UniqueConstraint(
            "evaluation_run_id", "test_case_id", name="uq_evaluation_results_run_case"
        ),
    )
    op.create_table(
        "spans",
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("otel_span_id", sa.Text(), nullable=False),
        sa.Column("parent_span_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), server_default="other", nullable=False),
        sa.Column("status", sa.Text(), server_default="unset", nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.CheckConstraint("kind IN ('llm', 'tool', 'chain', 'other')", name=op.f("ck_spans_kind")),
        sa.CheckConstraint("status IN ('unset', 'ok', 'error')", name=op.f("ck_spans_status")),
        sa.ForeignKeyConstraint(
            ["parent_span_id"],
            ["spans.id"],
            name=op.f("fk_spans_parent_span_id_spans"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["traces.id"], name=op.f("fk_spans_trace_id_traces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spans")),
        sa.UniqueConstraint("trace_id", "otel_span_id", name="uq_spans_trace_id_otel_span_id"),
    )
    op.create_table(
        "metrics",
        sa.Column("trace_id", sa.UUID(), nullable=False),
        sa.Column("span_id", sa.UUID(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["span_id"], ["spans.id"], name=op.f("fk_metrics_span_id_spans"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["trace_id"], ["traces.id"], name=op.f("fk_metrics_trace_id_traces"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metrics")),
    )

    for table, column in [
        ("evaluation_runs", "agent_version_id"),
        ("evaluation_runs", "dataset_id"),
        ("traces", "agent_version_id"),
        ("traces", "evaluation_run_id"),
        ("traces", "test_case_id"),
        ("spans", "parent_span_id"),
        ("evaluation_results", "test_case_id"),
        ("evaluation_results", "trace_id"),
        ("metrics", "trace_id"),
        ("metrics", "span_id"),
    ]:
        op.create_index(op.f(f"ix_{table}_{column}"), table, [column])

    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = clock_timestamp();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("agents", "datasets", "test_cases"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_set_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("agents", "datasets", "test_cases"):
        op.execute(f"DROP TRIGGER trg_{table}_set_updated_at ON {table}")
    op.execute("DROP FUNCTION set_updated_at()")
    op.drop_table("metrics")
    op.drop_table("spans")
    op.drop_table("evaluation_results")
    op.drop_table("traces")
    op.drop_table("evaluation_runs")
    op.drop_table("test_cases")
    op.drop_table("agent_versions")
    op.drop_table("datasets")
    op.drop_table("agents")
