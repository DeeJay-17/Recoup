from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "tool-gateway"
    db_schema: str = "tools"
    case_url: str = "http://localhost:8003"
    mock_erp_url: str = "http://localhost:8002"
    policy_url: str = "http://localhost:8004"
    comm_url: str = "http://localhost:8005"
    knowledge_url: str = "http://localhost:8009"
    tool_timeout_seconds: float = 30.0
    rate_limit_per_minute: int = 600
    result_max_chars: int = 20000
    tenant_sender_name: str = "Acme Accounts Receivable"
    tenant_company_name: str = "Acme Industrial Supply"


@lru_cache
def get_settings() -> Settings:
    return Settings()
