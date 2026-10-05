from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    # /metrics exposes operational counters; behind the analyst token unless
    # an edge proxy already gates the path.
    metrics_protected: bool = True

    postgres_user: str = "sentinel"
    # Local-dev placeholder only; .env overrides it and .env is gitignored.
    # Any real deployment must supply POSTGRES_PASSWORD.
    postgres_password: str = "local-dev-only-change-me"  # noqa: S105
    postgres_db: str = "sentinelpay"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    test_postgres_db: str = "sentinelpay_test"

    redis_url: str = "redis://127.0.0.1:6379/0"
    # Dedicated database so a test flush can never wipe local velocity state.
    redis_test_db: int = 15

    mongo_url: str = (
        "mongodb://sentinel:local-dev-only-change-me@127.0.0.1:27017/?authSource=admin"
    )
    mongo_db: str = "sentinelpay"
    mongo_test_db: str = "sentinelpay_test"

    api_key_dev: str = "sk_local_dev_key"
    analyst_token: str = "analyst_local_dev_token"  # noqa: S105 - dev-only default

    # Phase 2
    risk_enabled: bool = True
    risk_approve_max: float = 0.45
    risk_review_max: float = 0.75
    # Paths are relative to the repository root unless absolute.
    risk_model_path: str = "backend/ml/artifacts/model.joblib"
    risk_model_meta_path: str = "backend/ml/artifacts/model_meta.json"

    # Phase 5
    # Per-minute, per-client budgets for the endpoints that move money or
    # burn model tokens. Generous for the demo; real limits need real auth.
    rate_limit_payments_per_minute: int = 240
    rate_limit_copilot_per_minute: int = 30

    # Phase 4
    copilot_enabled: bool = True
    policies_path: str = "policies"
    # "minilm" (Chroma ONNX, downloads once) or "hashing" (offline, tests).
    copilot_embeddings: str = "minilm"
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

    @property
    def redis_test_url(self) -> str:
        """Same server, dedicated database number. Flushing it is always safe."""
        parts = urlsplit(self.redis_url)
        return urlunsplit(
            (parts.scheme, parts.netloc, f"/{self.redis_test_db}", parts.query, parts.fragment)
        )

    @property
    def risk_model_file(self) -> Path:
        return self._resolve_path(self.risk_model_path)

    @property
    def risk_model_meta_file(self) -> Path:
        return self._resolve_path(self.risk_model_meta_path)

    @property
    def policies_dir(self) -> Path:
        return self._resolve_path(self.policies_path)

    @property
    def chroma_dir(self) -> Path:
        return self._resolve_path(self.chroma_path)

    @staticmethod
    def _resolve_path(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else REPO_ROOT / path

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
