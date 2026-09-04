from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "orchestrator"
    db_schema: str = "agents"
    case_url: str = "http://localhost:8003"
    tool_gateway_url: str = "http://localhost:8006"
    knowledge_url: str = "http://localhost:8009"
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "recoup-cases"
    worker_enabled: bool = True
    consumer_enabled: bool = True
    autostart_on_case_created: bool = True
    max_steps_per_run: int = 20
    max_tokens_per_run: int = 150_000
    max_agent_iterations: int = 16
    max_repairs: int = 2
    customer_wait_hours_default: int = 72
    approval_wait_days: int = 30
    # LangGraph checkpoints: postgres URL (psycopg form) or empty for in-memory
    langgraph_checkpoint_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
