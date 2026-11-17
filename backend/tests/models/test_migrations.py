from sqlalchemy import create_engine, inspect

from alembic import command
from app.config import Settings
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
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
    finally:
        # Session-scoped fixtures expect the schema at head whatever happens above.
        command.upgrade(cfg, "head")
    sync_engine = create_engine(url)
    try:
        assert set(inspect(sync_engine).get_table_names()) >= EXPECTED_TABLES
    finally:
        sync_engine.dispose()
