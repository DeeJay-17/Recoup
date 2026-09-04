from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "iam"
    db_schema: str = "iam"
    # Dev bootstrap: created on first start if no tenant exists (disabled when empty)
    bootstrap_tenant_slug: str = "acme"
    bootstrap_tenant_name: str = "Acme Industrial Supply"
    bootstrap_admin_email: str = "admin@acme-demo.com"
    bootstrap_password: str = "password"


@lru_cache
def get_settings() -> Settings:
    return Settings()
