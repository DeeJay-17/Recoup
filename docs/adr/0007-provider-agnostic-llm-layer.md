# ADR-0007: Provider-agnostic LLM layer with a deterministic stand-in

**Status:** accepted (implemented in phase 3) · **Date:** 2026-09-04

## Context
Users bring their own LLM provider (this deployment uses Google Gemini; others use OpenAI,
Anthropic, or self-hosted OpenAI-compatible servers). CI and demos must run without any key.
Agents need tool calling and validated structured outputs on every provider.

## Decision
One small interface, `recoup_llm.LLMClient.chat(messages, tools, task)`, implemented by
`LangChainLLM` (LangChain `init_chat_model` + `bind_tools`, so any provider LangChain supports
works with config only) and by `HeuristicLLM` (rule-based policies registered per agent).
Structured output never depends on provider-specific JSON modes: the agent calls a
`submit_<agent>` tool whose schema is the Pydantic output model, validated by the loop.
Model choice is a two-tier router (`fast`, `strong`) configured by env and overridable per
tenant, with an optional fallback provider after repeated failures.

## Consequences
+ Swapping Gemini for another provider is a `.env` change; tenants can BYO keys.
+ The full pipeline runs deterministically with `LLM_PROVIDER=heuristic`, which doubles as an
  eval baseline.
− The lowest common denominator is "tool calls"; provider-specific features (native JSON
  schema, caching hints) are not used yet.
