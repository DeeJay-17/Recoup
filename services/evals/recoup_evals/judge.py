"""LLM-as-judge with a rubric and structured output. Provider-agnostic; a deterministic judge
runs when no key is configured so CI still produces numbers."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from recoup_llm import AssistantTurn, HeuristicLLM, LLMClient, Message, ToolCall, ToolSchema

RUBRIC = """You grade an AI accounts-receivable agent's work on one overdue-invoice case.

Score each dimension 1-5 (5 = excellent) using the evidence provided, and be strict:
- faithfulness: does every claim in the agent's rationale trace to a tool result shown below?
  5 = every claim grounded and specific; 3 = mostly grounded with vague bits; 1 = invented evidence,
  wrong numbers, or claims contradicted by the tool results.
- email_quality (only if an email draft is shown; otherwise omit it): clarity, professional tone,
  correct recipient and invoice facts, a clear ask. 5 = ready to send; 1 = would embarrass the company.

Explain each score in one sentence. Call submit_judgement exactly once."""


class Judgement(BaseModel):
    faithfulness: int = Field(ge=1, le=5)
    faithfulness_reason: str = Field(max_length=400)
    email_quality: int | None = Field(default=None, ge=1, le=5)
    email_quality_reason: str | None = Field(default=None, max_length=400)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=5)

    @field_validator("faithfulness_reason", "email_quality_reason", mode="before")
    @classmethod
    def _clamp(cls, v: Any) -> Any:
        return v[:400].rstrip() if isinstance(v, str) and len(v) > 400 else v


def build_evidence(steps: list[dict[str, Any]]) -> str:
    """Everything the judge is allowed to consider: the agent's claims and the raw tool results."""
    lines: list[str] = []
    for s in steps:
        if s.get("status") != "SUCCESS" or not s.get("output"):
            continue
        out = s["output"]
        agent = s.get("agent_name")
        if agent in ("investigator", "reconciler", "negotiator", "triage"):
            claim = out.get("summary") or out.get("rationale") or out.get("reasoning_summary") or ""
            lines.append(f"[{agent} claims] {str(claim)[:800]}")
            for e in out.get("evidence") or []:
                lines.append(f"  cited: [{e.get('source')}] {e.get('ref')}: {e.get('finding')}")
            if out.get("proposed_credit_memo") is not None:
                lines.append(f"  proposed credit: {out['proposed_credit_memo']}")
        for m in s.get("messages") or []:
            if m.get("role") == "tool":
                lines.append(f"[tool {m.get('name')}] {str(m.get('content'))[:700]}")
    return "\n".join(lines[:120])


def build_email(steps: list[dict[str, Any]]) -> str | None:
    for s in reversed(steps):
        if s.get("agent_name") == "communicator" and s.get("output"):
            o = s["output"]
            return f"To: {o.get('to')}\nSubject: {o.get('subject')}\nTone score: {o.get('tone_score')}\n\n{str(o.get('body_text'))[:2000]}"
    return None


async def judge_run(llm: LLMClient, steps: list[dict[str, Any]]) -> Judgement | None:
    evidence = build_evidence(steps)
    if not evidence.strip():
        return None
    email = build_email(steps)
    tool = ToolSchema(
        name="submit_judgement",
        description="Submit your scores.",
        parameters=Judgement.model_json_schema(),
    )
    user = (
        f"Agent work and tool results:\n{evidence[:14000]}\n\n"
        + (f"Email draft:\n{email}\n\n" if email else "No email was drafted.\n\n")
        + "Call submit_judgement."
    )
    msgs = [Message(role="system", content=RUBRIC), Message(role="user", content=user)]
    for _ in range(3):
        turn = await llm.chat(msgs, tools=[tool], task="judge")
        call = next((c for c in turn.tool_calls if c.name == "submit_judgement"), None)
        if call is None:
            msgs += [turn.as_message(), Message(role="user", content="Call submit_judgement now.")]
            continue
        try:
            return Judgement.model_validate(call.args)
        except ValidationError as e:
            msgs += [
                turn.as_message(),
                Message(
                    role="tool",
                    name="submit_judgement",
                    tool_call_id=call.id,
                    content=json.dumps(
                        {"accepted": False, "errors": e.errors(include_url=False)[:4]}
                    ),
                ),
            ]
    return None


def _heuristic_judge(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    """Deterministic stand-in: rewards citations and penalises claims with no tool output."""
    text = messages[-1].content if messages else ""
    cited = text.count("  cited: ")
    tool_lines = text.count("[tool ")
    claims = text.count(" claims] ")
    faith = (
        5
        if cited >= 3 and tool_lines >= 3
        else 4
        if cited >= 1 and tool_lines >= 2
        else 3
        if tool_lines
        else 2
    )
    if claims and not tool_lines:
        faith = 1
    args: dict[str, Any] = {
        "faithfulness": faith,
        "faithfulness_reason": f"{cited} cited evidence item(s) against {tool_lines} tool result(s).",
        "unsupported_claims": [] if faith >= 4 else ["claims not traceable to tool output"],
    }
    if "Email draft:" in text:
        body = text.split("Email draft:", 1)[1]
        quality = 5 if ("Subject:" in body and len(body) > 200) else 3
        if "FINAL NOTICE" in body.upper() or "!!!" in body:
            quality = 1
        args |= {
            "email_quality": quality,
            "email_quality_reason": "Heuristic: structure, length and tone markers.",
        }
    return AssistantTurn(tool_calls=[ToolCall(id="j1", name="submit_judgement", args=args)])


HeuristicLLM.register("judge", _heuristic_judge)
