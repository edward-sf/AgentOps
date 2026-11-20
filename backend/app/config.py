from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings from the environment and `.env`.

    `anthropic_api_key` is a `SecretStr`, so it never shows in a repr, `str` or JSON dump. Read
    it with `.get_secret_value()` only where a client is built or the key's presence is checked
    (`provider_for`, `record.py`, `smoke.py`, `eval.py`, `worker.py`). An empty value counts as
    not set.

    `database_url` and `test_database_url` stay `str`: they're passed to SQLAlchemy and Alembic
    in many places, and SQLAlchemy already masks the password in `URL` repr.
    """

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://agentops:agentops@localhost:5432/agentops"
    test_database_url: str = "postgresql+psycopg://agentops:agentops@localhost:5432/agentops_test"
    cors_origins: list[str] = ["http://localhost:3000"]
    anthropic_api_key: SecretStr | None = None
    # Evaluation worker (M2 spec, decisions 5 and 6).
    eval_concurrency: int = Field(default=4, ge=1)
    """How many cases of one run a worker executes at once (`EVAL_CONCURRENCY`)."""
    eval_stale_after_seconds: int = Field(default=60, ge=10)
    """A `running` run whose heartbeat is older than this can be reclaimed
    (`EVAL_STALE_AFTER_SECONDS`)."""
    eval_poll_interval_seconds: float = Field(default=1.0, gt=0)
    """How long an idle worker waits before it polls the queue again
    (`EVAL_POLL_INTERVAL_SECONDS`)."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
