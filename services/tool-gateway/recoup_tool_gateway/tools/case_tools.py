"""Case-service tools: read the case, record triage, propose actions, escalate."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field
from recoup_common.errors import ValidationError

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.registry import tool

RootCause = Literal[
    "DISPUTE_PRICING",
    "DISPUTE_QUANTITY",
    "MISSING_PO",
    "WRONG_CONTACT",
    "SHORT_PAY",
    "DUPLICATE_INVOICE",
    "CASH_FLOW",
    "UNKNOWN",
]


class NoArgs(BaseModel):
    pass


class CaseSnapshot(BaseModel):
    id: str
    status: str
    customer_ref: str
    customer_name: str | None
    invoice_refs: list[str]
    root_cause: str | None
    root_cause_conf: Decimal | None
    priority: int
    amount_open: Decimal
    currency: str
    days_overdue: int
    agent_mode: str
    version: int


class TimelineEntry(BaseModel):
    kind: str
    actor_id: str
    title: str
    occurred_at: str


class CaseTimeline(BaseModel):
    case_id: str
    events: list[TimelineEntry]
    pending_actions: int


def _snapshot(c: dict[str, Any]) -> CaseSnapshot:
    return CaseSnapshot(
        id=c["id"],
        status=c["status"],
        customer_ref=c["customer_ref"],
        customer_name=c.get("customer_name"),
        invoice_refs=c["invoice_refs"],
        root_cause=c.get("root_cause"),
        root_cause_conf=Decimal(str(c["root_cause_conf"]))
        if c.get("root_cause_conf") is not None
        else None,
        priority=c["priority"],
        amount_open=Decimal(str(c["amount_open"])),
        currency=c["currency"],
        days_overdue=c["days_overdue"],
        agent_mode=c["agent_mode"],
        version=c["version"],
    )


def _require_case(ctx: ToolContext) -> uuid.UUID:
    if ctx.case_id is None:
        raise ValidationError("this tool needs a case_id in the invocation")
    return ctx.case_id


@tool(
    name="get_case",
    description="Read the current case: status, root cause so far, open amount, priority, invoices and whether a human has taken over.",
    result=CaseSnapshot,
    tags=["case"],
)
async def get_case(ctx: ToolContext, args: NoArgs) -> CaseSnapshot:
    _require_case(ctx)
    case = await ctx.case()
    assert case is not None
    return _snapshot(case)


class TimelineArgs(BaseModel):
    limit: int = Field(default=30, ge=1, le=200, description="Most recent N events")


@tool(
    name="get_case_timeline",
    description="Read the case's audit timeline (agent steps, tool calls, emails, approvals, notes) newest last.",
    result=CaseTimeline,
    tags=["case"],
)
async def get_case_timeline(ctx: ToolContext, args: TimelineArgs) -> CaseTimeline:
    case_id = _require_case(ctx)
    d = await ctx.cases.get_detail(ctx.tenant_id, case_id)
    events = d["timeline"][-args.limit :]
    return CaseTimeline(
        case_id=str(case_id),
        events=[
            TimelineEntry(
                kind=e["kind"],
                actor_id=e["actor_id"],
                title=e["title"],
                occurred_at=e["occurred_at"],
            )
            for e in events
        ],
        pending_actions=sum(1 for a in d["actions"] if a["status"] == "PENDING"),
    )


class TriageArgs(BaseModel):
    root_cause: RootCause
    confidence: Decimal = Field(ge=0, le=1)
    summary: str = Field(
        max_length=1000, description="One paragraph: why this root cause, citing evidence"
    )
    priority: int | None = Field(default=None, ge=1, le=5)


@tool(
    name="set_triage",
    description="Record the triage outcome (root cause, confidence, summary) on the case. Moves NEW cases to TRIAGED.",
    result=CaseSnapshot,
    side_effect=False,
    tags=["case"],
)
async def set_triage(ctx: ToolContext, args: TriageArgs) -> CaseSnapshot:
    case_id = _require_case(ctx)
    c = await ctx.cases.triage(
        ctx.tenant_id,
        case_id,
        {
            "root_cause": args.root_cause,
            "root_cause_conf": str(args.confidence),
            "priority": args.priority,
            "summary": args.summary,
            "actor_id": ctx.actor,
        },
    )
    ctx._cache.pop("case", None)
    return _snapshot(c)


class NoteArgs(BaseModel):
    text: str = Field(
        max_length=2000, description="Short working note for the human reading the case"
    )


class NoteResult(BaseModel):
    recorded: bool


@tool(
    name="add_case_note",
    description="Leave a short note on the case timeline for humans (e.g. an evidence gap you could not close).",
    result=NoteResult,
    side_effect=True,
    tags=["case"],
)
async def add_case_note(ctx: ToolContext, args: NoteArgs) -> NoteResult:
    case_id = _require_case(ctx)
    await ctx.cases.append_event(
        ctx.tenant_id,
        case_id,
        {
            "kind": "agent_note",
            "actor_type": "agent",
            "actor_id": ctx.actor,
            "title": args.text[:200],
            "payload": {"text": args.text},
        },
    )
    return NoteResult(recorded=True)


class ProposeArgs(BaseModel):
    action_type: Literal[
        "CREATE_CREDIT_MEMO", "SEND_EMAIL", "PAYMENT_PLAN", "REBILL", "ESCALATE", "CLOSE"
    ]
    payload: dict[str, Any] = Field(
        description="The exact arguments the executing tool will receive (e.g. send_email args)"
    )
    rationale: str = Field(max_length=2000)
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="e.g. ['po:PO-50012', 'delivery:INV-100123', 'email:<id>']",
    )


class ProposeResult(BaseModel):
    action_id: str
    status: str
    policy_decision: str
    required_role: str | None
    policy_reason: str | None
    next_step: str


async def _propose_facts(ctx: ToolContext, args: ProposeArgs) -> dict[str, Any]:
    from recoup_tool_gateway.tools.erp_write import credit_memo_facts

    facts: dict[str, Any] = {"action": dict(args.payload)}
    if args.action_type == "CREATE_CREDIT_MEMO":
        facts.update(await credit_memo_facts(ctx, args.payload))
    if args.action_type == "SEND_EMAIL":
        from recoup_tool_gateway.tools.comm_tools import email_facts

        facts.update(await email_facts(ctx, args.payload))
    return facts


@tool(
    name="propose_action",
    description=(
        "Propose a side-effecting action for the case. The Policy Service decides ALLOW / REQUIRE_APPROVAL / DENY "
        "deterministically. If approval is required, a human decides in the console and you receive a signal; "
        "the returned action_id is the approval_ref for the executing tool."
    ),
    result=ProposeResult,
    side_effect=True,
    requires_policy_check=True,
    action_type="*",
    policy_facts=_propose_facts,
    tags=["case"],
)
async def propose_action(ctx: ToolContext, args: ProposeArgs) -> ProposeResult:
    case_id = _require_case(ctx)
    decision = ctx._cache.get("policy_decision") or {}
    body = {
        "action_type": args.action_type,
        "payload": args.payload,
        "rationale": args.rationale,
        "evidence_refs": args.evidence_refs,
        "policy_decision": decision.get("decision", "REQUIRE_APPROVAL"),
        "policy_rule": ", ".join(m["name"] for m in decision.get("matched", [])) or None,
        "required_role": decision.get("required_role"),
        "proposed_by": ctx.actor,
        "run_id": str(ctx.run_id) if ctx.run_id else None,
    }
    a = await ctx.cases.propose_action(ctx.tenant_id, case_id, body)
    d = a["policy_decision"]
    nxt = {
        "ALLOW": "Execute now with approval_ref=<action_id>.",
        "REQUIRE_APPROVAL": f"Wait for a {a.get('required_role') or 'human'} decision; then execute with approval_ref=<action_id>.",
        "DENY": "Do not execute. Re-plan with the denial reason.",
    }[d]
    return ProposeResult(
        action_id=a["id"],
        status=a["status"],
        policy_decision=d,
        required_role=a.get("required_role"),
        policy_reason=decision.get("reason"),
        next_step=nxt,
    )


class EscalateArgs(BaseModel):
    reason: str = Field(max_length=1000)
    summary: str = Field(
        max_length=3000,
        description="Human-ready brief: what was found, what was tried, recommended action, open questions",
    )


class EscalateResult(BaseModel):
    status: str


async def _escalate_facts(ctx: ToolContext, args: EscalateArgs) -> dict[str, Any]:
    return {"action": {"reason": args.reason}}


@tool(
    name="escalate_case",
    description="Hand the case to a human with a brief. Use when confidence is low, evidence is missing, or policy blocks the only path.",
    result=EscalateResult,
    side_effect=True,
    requires_policy_check=True,
    action_type="ESCALATE",
    policy_facts=_escalate_facts,
    tags=["case"],
)
async def escalate_case(ctx: ToolContext, args: EscalateArgs) -> EscalateResult:
    case_id = _require_case(ctx)
    await ctx.cases.append_event(
        ctx.tenant_id,
        case_id,
        {
            "kind": "escalation_brief",
            "actor_type": "agent",
            "actor_id": ctx.actor,
            "title": f"Escalated: {args.reason[:150]}",
            "payload": {"summary": args.summary},
        },
    )
    c = await ctx.cases.transition(ctx.tenant_id, case_id, "ESCALATED", args.reason, ctx.actor)
    return EscalateResult(status=c["status"])
