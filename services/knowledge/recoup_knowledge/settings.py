from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "knowledge"
    db_schema: str = "knowledge"
    case_url: str = "http://localhost:8003"
    comm_url: str = "http://localhost:8005"
    mock_erp_url: str = "http://localhost:8002"
    iam_url: str = "http://localhost:8001"
    default_tenant_slug: str = "acme"
    default_tenant_id: str | None = None
    chunk_chars: int = 1200
    chunk_overlap: int = 150
    search_k_vector: int = 20
    search_k_fts: int = 20
    rerank: str = "rrf"  # rrf | llm
    consumer_enabled: bool = True
    memory_min_confidence: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
