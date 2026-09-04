from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "case"
    db_schema: str = "cases"
    mock_erp_url: str = "http://localhost:8002"
    iam_url: str = "http://localhost:8001"
    ingest_enabled: bool = True
    ingest_interval_seconds: int = 60
    ingest_overdue_days: int = 7
    ingest_max_per_run: int = 500
    default_tenant_slug: str = "acme"
    default_tenant_id: str | None = None  # bypasses the IAM lookup when set


@lru_cache
def get_settings() -> Settings:
    return Settings()
