from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Settings common to every service. Services subclass and add their own fields.

    Every field maps to an upper-cased environment variable (e.g. ``DATABASE_URL``).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    service_name: str = "recoup-service"
    environment: str = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = "postgresql+asyncpg://recoup:recoup@localhost:5432/recoup"
    db_schema: str = "public"
    db_pool_size: int = 5

    kafka_bootstrap_servers: str | None = None
    outbox_poll_interval_seconds: float = 1.0

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "recoup-iam"
    access_token_ttl_seconds: int = 8 * 3600

    otel_exporter_otlp_endpoint: str | None = None

    redis_url: str = "redis://localhost:6379/0"

    @property
    def is_dev(self) -> bool:
        return self.environment in {"dev", "test", "local"}
