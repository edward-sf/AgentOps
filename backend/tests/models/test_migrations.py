from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

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
