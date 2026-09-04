from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "mock-erp"
    db_schema: str = "mockerp"
    auto_seed: bool = True
    seed: int = 42
    seed_customers: int = 60
    seed_invoices: int = 400


@lru_cache
def get_settings() -> Settings:
    return Settings()
