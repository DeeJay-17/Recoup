from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "realtime"
    heartbeat_seconds: int = 25
    replay_buffer: int = 200  # recent events kept per tenant for late joiners


@lru_cache
def get_settings() -> Settings:
    return Settings()
