from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://agentops:agentops@localhost:5432/agentops"
    test_database_url: str = "postgresql+psycopg://agentops:agentops@localhost:5432/agentops_test"
    cors_origins: list[str] = ["http://localhost:3000"]
    anthropic_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
