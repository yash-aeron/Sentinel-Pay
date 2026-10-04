from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    postgres_user: str = "sentinel"
    # Local-dev placeholder only; .env overrides it and .env is gitignored.
    # Any real deployment must supply POSTGRES_PASSWORD.
    postgres_password: str = "local-dev-only-change-me"  # noqa: S105
    postgres_db: str = "sentinelpay"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    test_postgres_db: str = "sentinelpay_test"

    redis_url: str = "redis://127.0.0.1:6379/0"

    mongo_url: str = (
        "mongodb://sentinel:local-dev-only-change-me@127.0.0.1:27017/?authSource=admin"
    )
    mongo_db: str = "sentinelpay"

    api_key_dev: str = "sk_local_dev_key"
    analyst_token: str = "analyst_local_dev_token"  # noqa: S105 - dev-only default

    # Phase 2
    risk_approve_max: float = 0.45
    risk_review_max: float = 0.75
    risk_model_path: str = "backend/ml/artifacts/model.joblib"
    risk_model_meta_path: str = "backend/ml/artifacts/model_meta.json"
    risk_degraded_review_floor_minor: int = 10_000

    # Phase 4
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b-instruct"
    ollama_timeout_seconds: int = 60
    chroma_path: str = "backend/.chroma"

    @property
    def database_url(self) -> str:
        return self._url(self.postgres_db)

    @property
    def test_database_url(self) -> str:
        return self._url(self.test_postgres_db)

    @property
    def admin_database_url(self) -> str:
        """Connection to the maintenance database, used to create/drop the test DB."""
        return self._url("postgres")

    def _url(self, database: str) -> str:
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
