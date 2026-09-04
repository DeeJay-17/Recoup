from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "analytics"
    db_schema: str = "analytics"
    consumer_group: str = "analytics"
    consumer_enabled: bool = True
    keep_raw_events: bool = True  # fact_events is the replay log for rebuilding the read model


@lru_cache
def get_settings() -> Settings:
    return Settings()
