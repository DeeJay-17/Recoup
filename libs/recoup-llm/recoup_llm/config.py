from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Provider ids follow LangChain's ``init_chat_model`` naming so any provider it supports works
# once its integration package is installed: google_genai, openai, anthropic, azure_openai,
# ollama, groq, mistralai, bedrock, ... ``heuristic`` is Recoup's built-in deterministic stand-in.
DEFAULT_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    # model prefix -> (input, output) USD per 1M tokens. Approximate; override via LLM_PRICES.
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-3-flash": (0.50, 3.00),
    "gemini-3.1-pro": (2.00, 12.00),
    "gemini-3.5-flash": (0.50, 3.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "claude-haiku": (1.00, 5.00),
    "claude-sonnet": (3.00, 15.00),
    "heuristic": (0.0, 0.0),
}


class LLMSettings(BaseSettings):
    """Every field is an env var prefixed LLM_ (e.g. LLM_PROVIDER, LLM_API_KEY)."""

    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    provider: str = "google_genai"
    api_key: str | None = None
    base_url: str | None = None  # OpenAI-compatible servers (vLLM, Ollama, OpenRouter, ...)
    model_fast: str = "gemini-2.5-flash"  # triage, tone, extraction
    model_strong: str = "gemini-2.5-pro"  # supervisor, investigator, reconciler, negotiator
    temperature: float = 0.1
    max_output_tokens: int = 4096
    timeout_seconds: float = 90.0
    max_retries: int = 2
    prices: dict[str, tuple[float, float]] = Field(
        default_factory=lambda: dict(DEFAULT_PRICES_PER_MTOK)
    )
    # Fallback provider used after ``max_retries`` failures (e.g. heuristic, or a second provider).
    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_api_key: str | None = None
    fallback_base_url: str | None = None

    def price_for(self, model: str) -> tuple[float, float]:
        for prefix, p in sorted(self.prices.items(), key=lambda kv: -len(kv[0])):
            if model.startswith(prefix):
                return p
        return (0.0, 0.0)
