"""Generic tool-calling specialist implemented as a LangGraph state machine.

    START -> model -> (output set? END : tool calls? tools : model)   tools -> model

The model ends its work by calling ``submit_<agent>`` whose arguments are the agent's Pydantic
output schema. Invalid submissions get a repair message (max ``max_repairs``); real tools go
through the Tool Gateway. Works identically for Gemini, OpenAI-compatible, Anthropic and the
heuristic provider because all of them speak "tool calls".
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ValidationError
from recoup_common.logging import get_logger
from recoup_llm import AssistantTurn, LLMClient, Message, ToolCall, ToolSchema, Usage

from recoup_orchestrator.clients import ToolGatewayClient

log = get_logger(__name__)


class RepairExhausted(Exception):
    pass


class IterationBudgetExceeded(Exception):
    pass


@dataclass
class AgentSpec:
    name: str  # "triage"
    prompt_name: str  # prompt file / version name
    output_model: type[BaseModel]
    tools: list[str]  # tool names this specialist may use (subset of the manifest)
    tier: str = "strong"  # fast | strong
    submit_description: str = "Submit your final structured result. Call exactly once when done."
    task_instructions: Callable[[dict[str, Any]], str] | None = (
        None  # state summary -> user message
    )
    submit_tool_name: str | None = None  # defaults to submit_<name>

    @property
    def submit_tool(self) -> str:
        return self.submit_tool_name or f"submit_{self.name}"


class LoopState(TypedDict):
    messages: list[dict[str, Any]]
    output: dict[str, Any] | None
    repairs: int
    iterations: int
    tool_log: list[dict[str, Any]]
    usage: dict[str, Any]
    provider: str
    model: str


@dataclass
class AgentResult:
    output: BaseModel
    usage: Usage
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    provider: str = ""
    model: str = ""
    latency_ms: int = 0


def _manifest_to_schemas(manifest: list[dict[str, Any]], allowed: list[str]) -> list[ToolSchema]:
    by_name = {t["name"]: t for t in manifest}
    out = []
    for name in allowed:
        t = by_name.get(name)
        if t is None:
            continue  # hidden by least-privilege manifest for this case
        out.append(
            ToolSchema(name=t["name"], description=t["description"], parameters=t["parameters"])
        )
    return out


def _submit_schema(spec: AgentSpec) -> ToolSchema:
    schema = spec.output_model.model_json_schema()
    schema.pop("title", None)
    return ToolSchema(name=spec.submit_tool, description=spec.submit_description, parameters=schema)


def _trim(obj: Any, limit: int = 6000) -> str:
    s = json.dumps(obj, default=str)
    return s if len(s) <= limit else s[:limit] + f"... [truncated {len(s) - limit} chars]"


class ToolLoopAgent:
    def __init__(
        self,
        spec: AgentSpec,
        *,
        llm: LLMClient,
        gateway: ToolGatewayClient,
        tenant_id: uuid.UUID,
        case_id: uuid.UUID,
        run_id: uuid.UUID,
        max_iterations: int = 12,
        max_repairs: int = 2,
        checkpointer: Any | None = None,
    ) -> None:
        self.spec = spec
        self.llm = llm
        self.gateway = gateway
        self.tenant_id, self.case_id, self.run_id = tenant_id, case_id, run_id
        self.max_iterations, self.max_repairs = max_iterations, max_repairs
        self.actor = f"agent:{spec.name}"
        self._tools: list[ToolSchema] = []
        self._graph = self._build(checkpointer or MemorySaver())

    # ---------- graph ----------
    def _build(self, checkpointer: Any) -> Any:
        g: StateGraph[LoopState] = StateGraph(LoopState)
        g.add_node("model", self._model_node)
        g.add_node("tools", self._tools_node)
        g.add_edge(START, "model")
        g.add_conditional_edges(
            "model", self._route, {"end": END, "tools": "tools", "model": "model"}
        )
        g.add_edge("tools", "model")
        return g.compile(checkpointer=checkpointer)

    def _route(self, state: LoopState) -> str:
        if state["output"] is not None:
            return "end"
        last = state["messages"][-1]
        if last.get("tool_calls"):
            return "tools"
        return "model"

    async def _model_node(self, state: LoopState) -> dict[str, Any]:
        if state["iterations"] >= self.max_iterations:
            raise IterationBudgetExceeded(
                f"{self.spec.name} exceeded {self.max_iterations} iterations"
            )
        messages = [Message.model_validate(m) for m in state["messages"]]
        if messages[-1].role == "assistant" and not messages[-1].tool_calls:
            messages.append(
                Message(
                    role="user",
                    content=f"Continue. When you are done, call {self.spec.submit_tool}.",
                )
            )
        turn: AssistantTurn = await self.llm.chat(messages, tools=self._tools, task=self.spec.name)
        usage = Usage.model_validate(state["usage"]) + turn.usage
        new_msgs = [m.model_dump() for m in messages[len(state["messages"]) :]] + [
            turn.as_message().model_dump()
        ]
        return {
            "messages": state["messages"] + new_msgs,
            "iterations": state["iterations"] + 1,
            "usage": usage.model_dump(),
            "provider": turn.provider,
            "model": turn.model,
        }

    async def _tools_node(self, state: LoopState) -> dict[str, Any]:
        last = Message.model_validate(state["messages"][-1])
        results: list[Message] = []
        log_rows: list[dict[str, Any]] = []
        output: dict[str, Any] | None = None
        repairs = state["repairs"]
        real_calls = [c for c in last.tool_calls if c.name != self.spec.submit_tool]
        submits = [c for c in last.tool_calls if c.name == self.spec.submit_tool]

        async def run(call: ToolCall) -> tuple[ToolCall, dict[str, Any], int]:
            t0 = time.perf_counter()
            try:
                env = await self.gateway.invoke(
                    call.name,
                    tenant_id=self.tenant_id,
                    case_id=self.case_id,
                    run_id=self.run_id,
                    actor=self.actor,
                    args=call.args,
                )
            except Exception as e:
                env = {"status": "ERROR", "message": str(e)[:500]}
            return call, env, int((time.perf_counter() - t0) * 1000)

        for call, env, ms in await asyncio.gather(*(run(c) for c in real_calls)):
            payload = (
                env.get("result")
                if env.get("status") == "SUCCESS"
                else {k: env.get(k) for k in ("status", "error", "message", "details")}
            )
            results.append(
                Message(role="tool", name=call.name, tool_call_id=call.id, content=_trim(payload))
            )
            log_rows.append(
                {
                    "tool": call.name,
                    "args": call.args,
                    "status": env.get("status"),
                    "latency_ms": ms,
                    "invocation_id": env.get("invocation_id"),
                    "error": env.get("message") if env.get("status") != "SUCCESS" else None,
                }
            )
        for call in submits:
            try:
                parsed = self.spec.output_model.model_validate(call.args)
                output = parsed.model_dump(mode="json")
                log_rows.append({"tool": call.name, "status": "ACCEPTED"})
                results.append(
                    Message(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content='{"accepted": true}',
                    )
                )
            except ValidationError as e:
                repairs += 1
                if repairs > self.max_repairs:
                    raise RepairExhausted(
                        f"{self.spec.name}: invalid output after {self.max_repairs} repairs: "
                        f"{e.errors(include_url=False)[:3]}"
                    ) from e
                errs = [
                    {"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors(include_url=False)
                ][:8]
                results.append(
                    Message(
                        role="tool",
                        name=call.name,
                        tool_call_id=call.id,
                        content=json.dumps(
                            {
                                "accepted": False,
                                "validation_errors": errs,
                                "hint": "Fix the fields and call the submit tool again.",
                            }
                        ),
                    )
                )
                log_rows.append({"tool": call.name, "status": "INVALID", "errors": errs})
        return {
            "messages": state["messages"] + [m.model_dump() for m in results],
            "output": output,
            "repairs": repairs,
            "tool_log": state["tool_log"] + log_rows,
        }

    # ---------- entry ----------
    async def run(
        self,
        *,
        system_prompt: str,
        user_message: str,
        manifest: list[dict[str, Any]],
        thread_id: str,
    ) -> AgentResult:
        self._tools = [*_manifest_to_schemas(manifest, self.spec.tools), _submit_schema(self.spec)]
        t0 = time.perf_counter()
        init: LoopState = {
            "messages": [
                Message(role="system", content=system_prompt).model_dump(),
                Message(role="user", content=user_message).model_dump(),
            ],
            "output": None,
            "repairs": 0,
            "iterations": 0,
            "tool_log": [],
            "usage": Usage().model_dump(),
            "provider": "",
            "model": "",
        }
        final: LoopState = await self._graph.ainvoke(
            init,
            config={
                "configurable": {"thread_id": thread_id},
                "recursion_limit": self.max_iterations * 2 + 4,
            },
        )
        assert final["output"] is not None
        return AgentResult(
            output=self.spec.output_model.model_validate(final["output"]),
            usage=Usage.model_validate(final["usage"]),
            tool_log=final["tool_log"],
            messages=final["messages"],
            iterations=final["iterations"],
            provider=final["provider"],
            model=final["model"],
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
