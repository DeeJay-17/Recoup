from functools import lru_cache

from recoup_common.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "communication"
    db_schema: str = "comm"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_use_tls: bool = False
    mailpit_api_url: str = "http://localhost:8025"
    # The AR mailbox customers write to. Anything arriving here that we didn't send is inbound.
    inbound_mailbox: str = "ar@acme-demo.com"
    from_name: str = "Acme Accounts Receivable"
    message_id_domain: str = "recoup.local"
    inbound_poll_enabled: bool = True
    inbound_poll_interval_seconds: int = 15
    case_url: str = "http://localhost:8003"
    iam_url: str = "http://localhost:8001"
    default_tenant_slug: str = "acme"
    default_tenant_id: str | None = None
    attachment_text_max_chars: int = 4000


@lru_cache
def get_settings() -> Settings:
    return Settings()
