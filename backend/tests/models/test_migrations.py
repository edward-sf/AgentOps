from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import Connection, create_engine, inspect, text

from alembic import command
from app.config import Settings
from app.models import Base
from tests.conftest import alembic_config

EXPECTED_TABLES = {
    "agents",
    "agent_versions",
    "datasets",
    "test_cases",
    "evaluation_runs",
    "traces",
    "spans",
    "evaluation_results",
    "metrics",
}


def test_upgrade_downgrade_upgrade_roundtrip() -> None:
    url = Settings().test_database_url
    cfg = alembic_config(url)
    sync_engine = create_engine(url)
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        # Nothing but the version table may survive a full downgrade.
        assert set(inspect(sync_engine).get_table_names()) <= {"alembic_version"}
        command.upgrade(cfg, "head")
    finally:
        # Session-scoped fixtures expect the schema at head whatever happens above.
        command.upgrade(cfg, "head")
        sync_engine.dispose()
    sync_engine = create_engine(url)
    try:
        assert set(inspect(sync_engine).get_table_names()) >= EXPECTED_TABLES
    finally:
        sync_engine.dispose()


def test_migrated_schema_matches_models() -> None:
    url = Settings().test_database_url
    command.upgrade(alembic_config(url), "head")
    sync_engine = create_engine(url)
    try:
        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        sync_engine.dispose()


def test_spans_columns_after_0002() -> None:
    url = Settings().test_database_url
    command.upgrade(alembic_config(url), "head")
    sync_engine = create_engine(url)
    try:
        columns = {c["name"]: c for c in inspect(sync_engine).get_columns("spans")}
        fks = inspect(sync_engine).get_foreign_keys("spans")
    finally:
        sync_engine.dispose()
    assert "parent_span_id" not in columns
    assert columns["parent_otel_span_id"]["nullable"] is True
    assert str(columns["parent_otel_span_id"]["type"]) == "TEXT"
    assert columns["events"]["nullable"] is False
    assert "'[]'" in str(columns["events"]["default"])
    # Only the trace FK remains: parent links are resolved by otel span ID (D1).
    assert [fk["constrained_columns"] for fk in fks] == [["trace_id"]]


def test_evaluation_columns_after_0003() -> None:
    url = Settings().test_database_url
    command.upgrade(alembic_config(url), "head")
    sync_engine = create_engine(url)
    try:
        insp = inspect(sync_engine)
        runs = {c["name"]: c for c in insp.get_columns("evaluation_runs")}
        results = {c["name"]: c for c in insp.get_columns("evaluation_results")}
    finally:
        sync_engine.dispose()
    assert runs["total_cases"]["nullable"] is True
    assert runs["heartbeat_at"]["nullable"] is True
    assert runs["cancel_requested"]["nullable"] is False
    assert "false" in str(runs["cancel_requested"]["default"])
    for name in ("status", "case_key", "case_input", "case_expected_behavior", "case_tags"):
        assert results[name]["nullable"] is False, name
    for name in ("error", "output", "case_expected_output", "passed"):
        assert results[name]["nullable"] is True, name


def _insert_unscored_result(conn: Connection) -> None:
    """One `error` result with `passed IS NULL`, and its parents."""
    conn.execute(
        text(
            """
            WITH a AS (INSERT INTO agents (name) VALUES ('migration-test') RETURNING id),
            v AS (
                INSERT INTO agent_versions (agent_id, version, provider, model, system_prompt)
                SELECT id, 1, 'anthropic', 'm', 'p' FROM a RETURNING id
            ),
            d AS (INSERT INTO datasets (name) VALUES ('migration-test') RETURNING id),
            c AS (
                INSERT INTO test_cases (dataset_id, key, input)
                SELECT id, 'case_001', 'hi' FROM d RETURNING id
            ),
            r AS (
                INSERT INTO evaluation_runs (agent_version_id, dataset_id)
                SELECT v.id, d.id FROM v, d RETURNING id
            )
            INSERT INTO evaluation_results (
                evaluation_run_id, test_case_id, status, error,
                case_key, case_input, case_expected_behavior, case_tags
            )
            SELECT r.id, c.id, 'error', 'boom', 'case_001', 'hi', '[]', '[]' FROM r, c
            """
        )
    )


def _delete_migration_test_rows(conn: Connection) -> None:
    runs = (
        "SELECT r.id FROM evaluation_runs r JOIN datasets d ON d.id = r.dataset_id "
        "WHERE d.name = 'migration-test'"
    )
    conn.execute(text(f"DELETE FROM evaluation_results WHERE evaluation_run_id IN ({runs})"))
    conn.execute(text(f"DELETE FROM evaluation_runs WHERE id IN ({runs})"))
    conn.execute(text("DELETE FROM datasets WHERE name = 'migration-test'"))
    conn.execute(text("DELETE FROM agents WHERE name = 'migration-test'"))


def test_downgrade_0003_refuses_unscored_results() -> None:
    url = Settings().test_database_url
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    sync_engine = create_engine(url)
    try:
        with sync_engine.begin() as conn:
            _insert_unscored_result(conn)
        with pytest.raises(RuntimeError, match="passed IS NULL"):
            command.downgrade(cfg, "0002")
        # The refusal left the schema at head.
        with sync_engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert version == "0003"
    finally:
        with sync_engine.begin() as conn:
            _delete_migration_test_rows(conn)
        command.upgrade(cfg, "head")
        sync_engine.dispose()
