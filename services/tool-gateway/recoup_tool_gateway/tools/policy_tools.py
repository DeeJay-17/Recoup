from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.registry import tool


class EvalArgs(BaseModel):
    action_type: str = Field(
        description="CREATE_CREDIT_MEMO | SEND_EMAIL | PAYMENT_PLAN | REBILL | ESCALATE"
    )
    action: dict[str, Any] = Field(
        description="Facts about the intended action, e.g. {'amount': 412.10}"
    )
    extra_facts: dict[str, Any] = Field(
        default_factory=dict, description="Optional 'email' / 'evidence' facts"
    )


class EvalResult(BaseModel):
    decision: str
    required_role: str | None
    reason: str | None
    matched_policies: list[str]


@tool(
    name="evaluate_policy",
    description="Dry-run the tenant's policies for an intended action to learn whether it would be ALLOWed, need approval, or be DENIED. Nothing is recorded or executed.",
    result=EvalResult,
    tags=["policy"],
)
async def evaluate_policy(ctx: ToolContext, args: EvalArgs) -> EvalResult:
    from recoup_tool_gateway.service import base_policy_context

    context = await base_policy_context(ctx)
    context.update({"action": args.action, **args.extra_facts})
    r = await ctx.policy.evaluate(
        tenant_id=ctx.tenant_id,
        case_id=ctx.case_id,
        actor=ctx.actor,
        action_type=args.action_type,
        context=context,
        record=False,
    )
    return EvalResult(
        decision=r["decision"],
        required_role=r.get("required_role"),
        reason=r.get("reason"),
        matched_policies=[m["name"] for m in r.get("matched", [])],
    )
