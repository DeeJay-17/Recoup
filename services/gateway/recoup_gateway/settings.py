from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "gateway"
    iam_url: str = "http://localhost:8001"
    mock_erp_url: str = "http://localhost:8002"
    case_url: str = "http://localhost:8003"
    policy_url: str = "http://localhost:8004"
    comm_url: str = "http://localhost:8005"
    tool_gateway_url: str = "http://localhost:8006"
    orchestrator_url: str = "http://localhost:8007"
    knowledge_url: str = "http://localhost:8009"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    rate_limit_per_minute: int = 600
    upstream_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
