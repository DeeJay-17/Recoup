"""Typed tool registry. A tool = Pydantic args + Pydantic result + async handler + metadata.

The manifest (JSON Schema per tool) is what the orchestrator hands to the LLM for function
calling. ``side_effect`` tools must carry an idempotency key; ``requires_policy_check`` tools are
evaluated by the Policy Service and may need an ``approval_ref`` before they run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from recoup_tool_gateway.context import ToolContext

Handler = Callable[["ToolContext", Any], Awaitable[BaseModel]]
PolicyFacts = Callable[["ToolContext", Any], Awaitable[dict[str, Any]]]
Visibility = Callable[[dict[str, Any] | None, dict[str, Any] | None], bool]


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    result_model: type[BaseModel]
    handler: Handler
    side_effect: bool = False
    requires_policy_check: bool = False
    idempotent: bool = True
    action_type: str | None = None  # policy action type; required when requires_policy_check
    policy_facts: PolicyFacts | None = None
    visible: Visibility | None = None  # (case, customer) -> bool ; least-privilege manifest
    tags: list[str] = field(default_factory=list)

    def manifest_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_model.model_json_schema(),
            "returns": self.result_model.model_json_schema(),
            "side_effect": self.side_effect,
            "requires_policy_check": self.requires_policy_check,
            "idempotent": self.idempotent,
            "action_type": self.action_type,
            "tags": self.tags,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool '{spec.name}' already registered")
        if spec.requires_policy_check and not spec.action_type:
            raise ValueError(f"tool '{spec.name}' requires_policy_check but has no action_type")
        if spec.side_effect and not spec.requires_policy_check and spec.name != "add_case_note":
            raise ValueError(f"side-effect tool '{spec.name}' must be policy-checked")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def manifest(
        self, *, case: dict[str, Any] | None = None, customer: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        out = []
        for t in self.all():
            if t.visible is not None and not t.visible(case, customer):
                continue
            out.append(t.manifest_entry())
        return out


registry = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    result: type[BaseModel],
    side_effect: bool = False,
    requires_policy_check: bool = False,
    idempotent: bool = True,
    action_type: str | None = None,
    policy_facts: PolicyFacts | None = None,
    visible: Visibility | None = None,
    tags: list[str] | None = None,
) -> Callable[[Handler], Handler]:
    """Decorator. The handler's second parameter annotation is the args model."""

    def deco(fn: Handler) -> Handler:
        import inspect

        params = list(inspect.signature(fn, eval_str=True).parameters.values())
        if len(params) != 2:
            raise TypeError(f"tool '{name}' handler must be (ctx, args)")
        args_model = params[1].annotation
        if not (isinstance(args_model, type) and issubclass(args_model, BaseModel)):
            raise TypeError(f"tool '{name}' args must be a Pydantic model")
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                args_model=args_model,
                result_model=result,
                handler=fn,
                side_effect=side_effect,
                requires_policy_check=requires_policy_check,
                idempotent=idempotent,
                action_type=action_type,
                policy_facts=policy_facts,
                visible=visible,
                tags=tags or [],
            )
        )
        return fn

    return deco
