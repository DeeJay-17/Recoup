from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


def dereference_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline ``$ref``/``$defs`` so providers without JSON-Schema reference support (Gemini
    function calling) see a plain nested schema. Also drops ``title`` noise."""
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def walk(node: Any, depth: int = 0) -> Any:
        if depth > 40:
            return node
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                target = defs.get(ref, {})
                merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(merged, depth + 1)
            out = {}
            for k, v in node.items():
                if k in ("$defs", "definitions", "title"):
                    continue
                out[k] = walk(v, depth + 1)
            # anyOf [X, null] -> nullable X (Gemini dislikes anyOf)
            if "anyOf" in out and isinstance(out["anyOf"], list):
                non_null = [o for o in out["anyOf"] if o != {"type": "null"}]
                if len(non_null) == 1 and len(out["anyOf"]) == 2:
                    base = dict(non_null[0])
                    base["nullable"] = True
                    for k in ("description", "default"):
                        if k in out:
                            base[k] = out[k]
                    return base
            return out
        if isinstance(node, list):
            return [walk(x, depth + 1) for x in node]
        return node

    return dict(walk(schema))


class ToolSchema(BaseModel):
    """OpenAI-style function schema; LangChain translates it for every provider."""

    name: str
    description: str
    parameters: dict[str, Any]

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dereference_schema(self.parameters),
            },
        }


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)  # assistant only
    tool_call_id: str | None = None  # tool only
    name: str | None = None  # tool only


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 6),
        )


class AssistantTurn(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    provider: str = ""

    def as_message(self) -> Message:
        return Message(role="assistant", content=self.content, tool_calls=self.tool_calls)
