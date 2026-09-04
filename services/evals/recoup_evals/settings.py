from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "evals"
    db_schema: str = "evals"
    case_url: str = "http://localhost:8003"
    mock_erp_url: str = "http://localhost:8002"
    orchestrator_url: str = "http://localhost:8007"
    tool_gateway_url: str = "http://localhost:8006"
    policy_url: str = "http://localhost:8004"
    iam_url: str = "http://localhost:8001"
    default_tenant_slug: str = "acme"
    default_tenant_id: str | None = None
    run_concurrency: int = 4
    case_timeout_seconds: int = 900
    poll_interval_seconds: float = 3.0
    # Gates used by CI (`scripts/eval.py gate`)
    gate_root_cause_accuracy: float = 0.85
    gate_credit_accuracy: float = 0.90
    gate_unauthorized_mutations: int = 0
    gate_max_cost_per_case: float = 0.40
    gate_regression_points: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
