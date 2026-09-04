from __future__ import annotations

from typing import Literal, Protocol

from recoup_llm.config import LLMSettings
from recoup_llm.types import AssistantTurn, Message, ToolSchema

Task = Literal["fast", "strong"]


class LLMClient(Protocol):
    provider: str
    model: str

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        task: str = "",
        temperature: float | None = None,
    ) -> AssistantTurn: ...


class ModelRouter:
    """Picks a client by tier. Tenants can override tiers via ``overrides`` (from model_configs)."""

    def __init__(self, fast: LLMClient, strong: LLMClient) -> None:
        self._clients: dict[str, LLMClient] = {"fast": fast, "strong": strong}

    def for_tier(self, tier: Task) -> LLMClient:
        return self._clients[tier]

    def describe(self) -> dict[str, dict[str, str]]:
        return {
            tier: {"provider": c.provider, "model": c.model} for tier, c in self._clients.items()
        }


def build_client(
    settings: LLMSettings,
    *,
    model: str,
    provider: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMClient:
    provider = provider or settings.provider
    if provider == "heuristic":
        from recoup_llm.heuristic import HeuristicLLM

        return HeuristicLLM()
    from recoup_llm.langchain_client import LangChainLLM

    primary = LangChainLLM(
        settings,
        provider=provider,
        model=model,
        api_key=api_key if api_key is not None else settings.api_key,
        base_url=base_url if base_url is not None else settings.base_url,
    )
    if settings.fallback_provider and provider != settings.fallback_provider:
        fallback = build_client(
            settings,
            provider=settings.fallback_provider,
            model=settings.fallback_model or model,
            api_key=settings.fallback_api_key or "",
            base_url=settings.fallback_base_url or "",
        )
        from recoup_llm.langchain_client import WithFallback

        wrapped: LLMClient = WithFallback(primary, fallback, max_failures=settings.max_retries)
        return wrapped
    return primary


def build_router(
    settings: LLMSettings, *, overrides: dict[str, dict[str, str]] | None = None
) -> ModelRouter:
    """``overrides`` = {"fast": {"provider": ..., "model": ...}, "strong": {...}} per tenant."""
    ov = overrides or {}

    def mk(tier: str, default_model: str) -> LLMClient:
        o = ov.get(tier, {})
        return build_client(
            settings,
            model=o.get("model") or default_model,
            provider=o.get("provider"),
            api_key=o.get("api_key"),
            base_url=o.get("base_url"),
        )

    return ModelRouter(
        fast=mk("fast", settings.model_fast), strong=mk("strong", settings.model_strong)
    )
