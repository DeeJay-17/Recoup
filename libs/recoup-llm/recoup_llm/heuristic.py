"""Deterministic stand-in for an LLM.

Domain code registers a ``HeuristicPolicy`` per task (e.g. "triage"): a function that looks at
the conversation so far (including tool results) and returns the next assistant turn. This
keeps the whole agent pipeline runnable without a provider key and gives evals a baseline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from recoup_llm.types import AssistantTurn, Message, ToolSchema

HeuristicPolicy = Callable[[list[Message], list[ToolSchema]], AssistantTurn]


class HeuristicLLM:
    provider = "heuristic"
    model = "heuristic"
    _registry: dict[str, HeuristicPolicy] = {}

    @classmethod
    def register(cls, task: str, policy: HeuristicPolicy) -> None:
        cls._registry[task] = policy

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        task: str = "",
        temperature: float | None = None,
    ) -> AssistantTurn:
        policy = self._registry.get(task)
        if policy is None:
            raise RuntimeError(f"no heuristic policy registered for task '{task}'")
        turn = policy(messages, tools or [])
        turn.model, turn.provider = "heuristic", "heuristic"
        return turn


def last_tool_results(messages: list[Message]) -> dict[str, Any]:
    """name -> parsed JSON of the most recent tool result with that name."""
    import json

    out: dict[str, Any] = {}
    for m in messages:
        if m.role == "tool" and m.name:
            try:
                out[m.name] = json.loads(m.content)
            except ValueError:
                out[m.name] = m.content
    return out
