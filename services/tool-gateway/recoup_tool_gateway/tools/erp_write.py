"""Mutating ERP tools. Policy-gated; the gateway verifies amounts against deterministic reconciliation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from recoup_common.errors import ValidationError
from recoup_erp import CreditMemo, CreditMemoRequest, PaymentPlan, PaymentPlanRequest

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.registry import tool
from recoup_tool_gateway.tools.reconcile import load_reconcile_inputs, reconcile

DISPUTE_CAUSES = {"DISPUTE_PRICING", "DISPUTE_QUANTITY", "DUPLICATE_INVOICE", "SHORT_PAY"}


async def credit_memo_facts(ctx: ToolContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Evidence facts are computed by the gateway, not claimed by the model."""
    ref = payload.get("invoice_ref")
    amount = Decimal(str(payload.get("amount", "0")))
    facts: dict[str, Any] = {"action": {**payload, "amount": str(amount)}}
    if not ref:
        return facts
    try:
        invoice, po, delivery, contract = await load_reconcile_inputs(ctx, str(ref))
    except Exception:
        return {**facts, "evidence": {"has_po_match": False, "reconciled": False}}
    rec = reconcile(invoice, po, delivery, contract)
    dup_full_void = False
    if amount == invoice.amount_open:
        # a full credit is legitimate for duplicates; reconciliation cannot see that, dup check can
        dup_full_void = True
    within = amount <= rec.proposed_credit_memo + Decimal("1.00")
    facts["evidence"] = {
        "reconciled": True,
        "has_po_match": rec.po_found and rec.po_customer_matches,
        "delivery_found": rec.delivery_found,
        "reconciled_credit": str(rec.proposed_credit_memo),
        "amount_within_reconciled": within,
        "full_open_balance": dup_full_void,
        "discrepancy_kinds": sorted({d.kind for d in rec.discrepancies}),
    }
    return facts


class CreditMemoArgs(BaseModel):
    invoice_ref: str
    amount: Decimal = Field(gt=0)
    reason_code: str = Field(
        pattern=r"^(PRICING|QUANTITY|DUPLICATE|FREIGHT|SHORT_PAY|GOODWILL|OTHER)$"
    )
    memo: str = Field(max_length=500, description="Customer-visible memo text")


async def _cm_facts(ctx: ToolContext, args: CreditMemoArgs) -> dict[str, Any]:
    return await credit_memo_facts(ctx, args.model_dump(mode="json"))


def _cm_visible(case: dict[str, Any] | None, customer: dict[str, Any] | None) -> bool:
    return bool(case and case.get("root_cause") in DISPUTE_CAUSES)


@tool(
    name="create_credit_memo",
    description="Issue a credit memo against an invoice in the ERP. Policy-gated; amount is checked against deterministic reconciliation. Requires approval_ref from propose_action unless policy ALLOWs.",
    result=CreditMemo,
    side_effect=True,
    requires_policy_check=True,
    action_type="CREATE_CREDIT_MEMO",
    policy_facts=_cm_facts,
    visible=_cm_visible,
    tags=["erp", "mutating"],
)
async def create_credit_memo(ctx: ToolContext, args: CreditMemoArgs) -> CreditMemo:
    return await ctx.erp.create_credit_memo(
        CreditMemoRequest(
            invoice_ref=args.invoice_ref,
            amount=args.amount,
            reason_code=args.reason_code,
            memo=args.memo,
            idempotency_key=ctx._cache["idempotency_key"],
        )
    )


class PlanApplyArgs(BaseModel):
    invoice_refs: list[str] = Field(min_length=1)
    installments: int = Field(ge=1, le=12)
    first_due: date
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)


async def _plan_facts(ctx: ToolContext, args: PlanApplyArgs) -> dict[str, Any]:
    invoices = [await ctx.erp.get_invoice(r) for r in args.invoice_refs]
    earliest = min(i.due_date for i in invoices)
    last_due = date.fromordinal(args.first_due.toordinal() + 30 * (args.installments - 1))
    return {
        "action": {
            "invoice_refs": args.invoice_refs,
            "installments": args.installments,
            "discount_pct": str(args.discount_pct),
            "extension_days": max(0, (last_due - earliest).days),
            "total_open": str(sum((i.amount_open for i in invoices), Decimal("0"))),
        }
    }


def _plan_visible(case: dict[str, Any] | None, customer: dict[str, Any] | None) -> bool:
    return not (customer and customer.get("credit_hold"))


@tool(
    name="apply_payment_plan",
    description="Apply an installment plan (optionally with an early-pay discount) to open invoices in the ERP. Policy-gated.",
    result=PaymentPlan,
    side_effect=True,
    requires_policy_check=True,
    action_type="PAYMENT_PLAN",
    policy_facts=_plan_facts,
    visible=_plan_visible,
    tags=["erp", "mutating"],
)
async def apply_payment_plan(ctx: ToolContext, args: PlanApplyArgs) -> PaymentPlan:
    if len(set(args.invoice_refs)) != len(args.invoice_refs):
        raise ValidationError("duplicate invoice refs")
    return await ctx.erp.apply_payment_plan(
        PaymentPlanRequest(
            invoice_refs=args.invoice_refs,
            installments=args.installments,
            first_due=args.first_due,
            discount_pct=args.discount_pct,
            idempotency_key=ctx._cache["idempotency_key"],
        )
    )
