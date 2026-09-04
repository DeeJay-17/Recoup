"""LangChain-backed client: one code path for Gemini, OpenAI(-compatible), Anthropic, Azure, ..."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from recoup_llm.config import LLMSettings
from recoup_llm.types import AssistantTurn, Message, ToolCall, ToolSchema, Usage


class LLMError(Exception):
    pass


def _to_lc(messages: list[Message]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for m in messages:
        if m.role == "system":
            out.append(SystemMessage(content=m.content))
        elif m.role == "user":
            out.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            out.append(
                AIMessage(
                    content=m.content,
                    tool_calls=[{"id": t.id, "name": t.name, "args": t.args} for t in m.tool_calls],
                )
            )
        elif m.role == "tool":
            out.append(
                ToolMessage(content=m.content, tool_call_id=m.tool_call_id or "", name=m.name)
            )
    return out


def make_chat_model(
    settings: LLMSettings,
    *,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    temperature: float | None = None,
) -> BaseChatModel:
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {
        "temperature": settings.temperature if temperature is None else temperature,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if provider == "google_genai":
        if api_key:
            kwargs["google_api_key"] = api_key
        kwargs["max_output_tokens"] = settings.max_output_tokens
    elif provider in ("openai", "azure_openai", "groq", "mistralai", "ollama", "openrouter"):
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["max_tokens"] = settings.max_output_tokens
        if provider == "openrouter":  # OpenAI-compatible
            provider = "openai"
            kwargs.setdefault("base_url", "https://openrouter.ai/api/v1")
    elif provider == "anthropic":
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        kwargs["max_tokens"] = settings.max_output_tokens
    else:
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
    chat: BaseChatModel = init_chat_model(model, model_provider=provider, **kwargs)
    return chat


class LangChainLLM:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        provider: str,
        model: str,
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.model = model
        self._model = make_chat_model(
            settings, provider=provider, model=model, api_key=api_key, base_url=base_url
        )
        self._bound: dict[tuple[str, ...], Any] = {}

    def _with_tools(self, tools: list[ToolSchema] | None) -> Any:
        if not tools:
            return self._model
        key = tuple(t.name for t in tools)
        if key not in self._bound:
            self._bound[key] = self._model.bind_tools([t.as_openai() for t in tools])
        return self._bound[key]

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        task: str = "",
        temperature: float | None = None,
    ) -> AssistantTurn:
        runnable = self._with_tools(tools)
        try:
            ai = await asyncio.wait_for(
                runnable.ainvoke(_to_lc(messages)), timeout=self.settings.timeout_seconds + 5
            )
        except Exception as e:
            raise LLMError(f"{self.provider}/{self.model}: {type(e).__name__}: {e}") from e
        if not isinstance(ai, AIMessage):
            raise LLMError(f"unexpected response type {type(ai).__name__}")
        usage = Usage()
        meta = getattr(ai, "usage_metadata", None) or {}
        if meta:
            usage.input_tokens = int(meta.get("input_tokens", 0))
            usage.output_tokens = int(meta.get("output_tokens", 0))
            pin, pout = self.settings.price_for(self.model)
            usage.cost_usd = round(
                usage.input_tokens * pin / 1e6 + usage.output_tokens * pout / 1e6, 6
            )
        content = ai.content if isinstance(ai.content, str) else _flatten(ai.content)
        calls = [
            ToolCall(
                id=str(t.get("id") or f"call_{i}"),
                name=str(t["name"]),
                args=dict(t.get("args") or {}),
            )
            for i, t in enumerate(ai.tool_calls or [])
        ]
        return AssistantTurn(
            content=content, tool_calls=calls, usage=usage, model=self.model, provider=self.provider
        )


def _flatten(parts: list[Any]) -> str:
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and p.get("type") == "text":
            out.append(str(p.get("text", "")))
    return "".join(out)


class WithFallback:
    """Try the primary; after N consecutive failures route to the fallback until it recovers."""

    def __init__(self, primary: Any, fallback: Any, *, max_failures: int = 2) -> None:
        self.primary = primary
        self.fallback = fallback
        self.max_failures = max_failures
        self._failures = 0
        self.provider: str = str(primary.provider)
        self.model: str = str(primary.model)

    async def chat(self, messages: list[Message], **kw: Any) -> AssistantTurn:
        if self._failures < self.max_failures:
            try:
                turn: AssistantTurn = await self.primary.chat(messages, **kw)
                self._failures = 0
                return turn
            except LLMError:
                self._failures += 1
                if self._failures < self.max_failures:
                    raise
        fb: AssistantTurn = await self.fallback.chat(messages, **kw)
        self._failures = max(0, self._failures - 1)  # gradually probe the primary again
        return fb
