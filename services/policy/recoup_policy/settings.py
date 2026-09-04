from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "policy"
    db_schema: str = "policy"
    # When no rule matches a side-effecting action, fall back to this (safe by default).
    default_decision: str = "REQUIRE_APPROVAL"
    default_required_role: str = "analyst"


@lru_cache
def get_settings() -> Settings:
    return Settings()
