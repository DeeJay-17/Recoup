"""Deterministic money math. LLMs never compute credit memos; they call these."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field
from recoup_erp import ContractTerms, DeliveryProof, Invoice, PurchaseOrder
from recoup_erp.mock_client import ERPNotFound

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.registry import tool

TWO = Decimal("0.01")


def money(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


DiscrepancyKind = Literal[
    "PRICE_ABOVE_PO",
    "PRICE_ABOVE_CONTRACT",
    "QTY_ABOVE_PO",
    "QTY_SHORT_DELIVERED",
    "LINE_NOT_ON_PO",
    "FREIGHT_NOT_BILLABLE",
]


class LineDiscrepancy(BaseModel):
    kind: DiscrepancyKind
    line_no: int | None
    sku: str | None
    invoiced: str
    reference: str
    delta_qty: Decimal | None = None
    delta_unit_price: Decimal | None = None
    credit_before_tax: Decimal
    note: str


class ReconcileArgs(BaseModel):
    invoice_ref: str = Field(
        description="Invoice to reconcile against its PO, delivery proof and contract"
    )


class ReconcileResult(BaseModel):
    invoice_ref: str
    po_number: str | None
    po_found: bool
    po_customer_matches: bool
    delivery_found: bool
    contract_found: bool
    tax_rate: Decimal
    discrepancies: list[LineDiscrepancy]
    credit_before_tax: Decimal
    proposed_credit_memo: Decimal
    rebill_required: bool
    lines_checked: int
    summary: str


def reconcile(
    invoice: Invoice,
    po: PurchaseOrder | None,
    delivery: DeliveryProof | None,
    contract: ContractTerms | None,
) -> ReconcileResult:
    tax_rate = (invoice.tax / invoice.subtotal) if invoice.subtotal else Decimal("0")
    tax_rate = tax_rate.quantize(Decimal("0.0001"))
    po_lines = {line.sku: line for line in po.lines} if po else {}
    delivered = {line.sku: line.qty_delivered for line in delivery.lines} if delivery else {}
    out: list[LineDiscrepancy] = []
    rebill = False

    for line in invoice.lines:
        if line.is_freight:
            continue
        ref_price: Decimal | None = None
        ref_src = ""
        if line.sku in po_lines:
            ref_price, ref_src = po_lines[line.sku].unit_price, "PO"
        elif contract and line.sku in contract.price_list:
            ref_price, ref_src = contract.price_list[line.sku], "contract"
        elif po is not None:
            out.append(
                LineDiscrepancy(
                    kind="LINE_NOT_ON_PO",
                    line_no=line.line_no,
                    sku=line.sku,
                    invoiced=f"{line.qty} @ {line.unit_price}",
                    reference="not on PO",
                    credit_before_tax=money(line.amount),
                    note="Line has no PO counterpart; needs human confirmation before crediting.",
                )
            )
            continue
        if ref_price is not None and line.unit_price > ref_price:
            delta = line.unit_price - ref_price
            out.append(
                LineDiscrepancy(
                    kind="PRICE_ABOVE_PO" if ref_src == "PO" else "PRICE_ABOVE_CONTRACT",
                    line_no=line.line_no,
                    sku=line.sku,
                    invoiced=str(line.unit_price),
                    reference=f"{ref_price} ({ref_src})",
                    delta_unit_price=delta,
                    credit_before_tax=money(delta * line.qty),
                    note=f"Unit price {line.unit_price} exceeds {ref_src} price {ref_price} on {line.qty} units.",
                )
            )
        billable_qty = line.qty
        if line.sku in po_lines and line.qty > po_lines[line.sku].qty:
            over = line.qty - po_lines[line.sku].qty
            price_for_credit = min(line.unit_price, ref_price or line.unit_price)
            out.append(
                LineDiscrepancy(
                    kind="QTY_ABOVE_PO",
                    line_no=line.line_no,
                    sku=line.sku,
                    invoiced=str(line.qty),
                    reference=f"{po_lines[line.sku].qty} (PO)",
                    delta_qty=over,
                    credit_before_tax=money(over * price_for_credit),
                    note=f"Invoiced {line.qty} but PO ordered {po_lines[line.sku].qty}.",
                )
            )
            billable_qty = po_lines[line.sku].qty
        if line.sku in delivered and delivered[line.sku] < billable_qty:
            short = billable_qty - delivered[line.sku]
            price_for_credit = min(line.unit_price, ref_price or line.unit_price)
            out.append(
                LineDiscrepancy(
                    kind="QTY_SHORT_DELIVERED",
                    line_no=line.line_no,
                    sku=line.sku,
                    invoiced=str(billable_qty),
                    reference=f"{delivered[line.sku]} (delivered)",
                    delta_qty=short,
                    credit_before_tax=money(short * price_for_credit),
                    note=f"Only {delivered[line.sku]} of {billable_qty} delivered; credit the short units.",
                )
            )
            rebill = True

    if invoice.freight > 0 and contract is not None and not contract.freight_billable:
        out.append(
            LineDiscrepancy(
                kind="FREIGHT_NOT_BILLABLE",
                line_no=None,
                sku="FREIGHT",
                invoiced=str(invoice.freight),
                reference="0.00 (contract: freight not billable)",
                credit_before_tax=money(invoice.freight),
                note="Contract says freight is not billable; the freight charge should be credited.",
            )
        )

    # Freight is not taxed in the mock ERP; line credits carry tax.
    line_credit = sum(
        (d.credit_before_tax for d in out if d.kind != "FREIGHT_NOT_BILLABLE"), Decimal("0")
    )
    freight_credit = sum(
        (d.credit_before_tax for d in out if d.kind == "FREIGHT_NOT_BILLABLE"), Decimal("0")
    )
    credit_before_tax = money(line_credit + freight_credit)
    proposed = money(line_credit * (1 + tax_rate) + freight_credit)
    proposed = min(proposed, invoice.amount_open) if invoice.amount_open > 0 else proposed
    kinds = sorted({d.kind for d in out})
    summary = (
        f"{len(out)} discrepancy(ies): {', '.join(kinds)}; proposed credit {proposed}"
        if out
        else "Invoice matches PO, delivery and contract; no credit warranted."
    )
    return ReconcileResult(
        invoice_ref=invoice.invoice_ref,
        po_number=invoice.po_number,
        po_found=po is not None,
        po_customer_matches=bool(po and po.customer_ref == invoice.customer_ref),
        delivery_found=delivery is not None,
        contract_found=contract is not None,
        tax_rate=tax_rate,
        discrepancies=out,
        credit_before_tax=credit_before_tax,
        proposed_credit_memo=proposed,
        rebill_required=rebill,
        lines_checked=len([line for line in invoice.lines if not line.is_freight]),
        summary=summary,
    )


async def load_reconcile_inputs(
    ctx: ToolContext, invoice_ref: str
) -> tuple[Invoice, PurchaseOrder | None, DeliveryProof | None, ContractTerms | None]:
    invoice = await ctx.erp.get_invoice(invoice_ref)
    po = None
    if invoice.po_number:
        try:
            po = await ctx.erp.get_purchase_order(invoice.po_number)
        except ERPNotFound:
            po = None
    delivery = await ctx.erp.get_delivery_proof(invoice_ref)
    try:
        contract = await ctx.erp.get_contract_terms(invoice.customer_ref)
    except ERPNotFound:
        contract = None
    return invoice, po, delivery, contract


@tool(
    name="reconcile_lines",
    description="Deterministically compare an invoice's lines against its PO, delivery proof and contract price list. Returns every discrepancy with the exact credit it implies. Use this before proposing any credit memo.",
    result=ReconcileResult,
    tags=["reconcile"],
)
async def reconcile_lines(ctx: ToolContext, args: ReconcileArgs) -> ReconcileResult:
    invoice, po, delivery, contract = await load_reconcile_inputs(ctx, args.invoice_ref)
    return reconcile(invoice, po, delivery, contract)


class CalcCreditArgs(BaseModel):
    invoice_ref: str
    include_kinds: list[DiscrepancyKind] = Field(
        description="Which discrepancy kinds to include in the credit (e.g. exclude LINE_NOT_ON_PO until confirmed)"
    )


class CalcCreditResult(BaseModel):
    invoice_ref: str
    included: list[LineDiscrepancy]
    credit_before_tax: Decimal
    tax: Decimal
    credit_memo_amount: Decimal
    capped_to_amount_open: bool


@tool(
    name="calculate_credit_memo",
    description="Compute the exact credit memo amount for a subset of reconciliation discrepancies (tax applied to line credits, freight untaxed, capped at the open balance).",
    result=CalcCreditResult,
    tags=["reconcile"],
)
async def calculate_credit_memo(ctx: ToolContext, args: CalcCreditArgs) -> CalcCreditResult:
    invoice, po, delivery, contract = await load_reconcile_inputs(ctx, args.invoice_ref)
    rec = reconcile(invoice, po, delivery, contract)
    included = [d for d in rec.discrepancies if d.kind in args.include_kinds]
    line_credit = sum(
        (d.credit_before_tax for d in included if d.kind != "FREIGHT_NOT_BILLABLE"), Decimal("0")
    )
    freight_credit = sum(
        (d.credit_before_tax for d in included if d.kind == "FREIGHT_NOT_BILLABLE"), Decimal("0")
    )
    tax = money(line_credit * rec.tax_rate)
    total = money(line_credit + tax + freight_credit)
    capped = total > invoice.amount_open
    return CalcCreditResult(
        invoice_ref=invoice.invoice_ref,
        included=included,
        credit_before_tax=money(line_credit + freight_credit),
        tax=tax,
        credit_memo_amount=min(total, invoice.amount_open) if capped else total,
        capped_to_amount_open=capped,
    )


class DupArgs(BaseModel):
    invoice_ref: str


class DupCandidate(BaseModel):
    invoice_ref: str
    status: str
    total: Decimal
    issue_date: date
    same_po: bool
    same_lines: bool
    amount_paid: Decimal


class DupResult(BaseModel):
    invoice_ref: str
    is_duplicate: bool
    candidates: list[DupCandidate]
    explanation: str


@tool(
    name="check_duplicate_invoice",
    description="Check whether an invoice duplicates another invoice for the same customer and PO (same lines). A paid original means the open one should be voided.",
    result=DupResult,
    tags=["reconcile"],
)
async def check_duplicate_invoice(ctx: ToolContext, args: DupArgs) -> DupResult:
    inv = await ctx.erp.get_invoice(args.invoice_ref)
    others = [
        i
        for i in await ctx.erp.list_open_invoices(customer_ref=inv.customer_ref, limit=500)
        if i.invoice_ref != inv.invoice_ref
    ]
    # open invoices only come back from list_open_invoices; also probe remittances for paid ones
    paid_refs = {
        r.invoice_ref for r in await ctx.erp.get_remittances(inv.customer_ref) if r.invoice_ref
    }
    for ref in paid_refs - {i.invoice_ref for i in others} - {inv.invoice_ref}:
        try:
            others.append(await ctx.erp.get_invoice(ref))
        except ERPNotFound:
            continue
    sig = sorted((line.sku, str(line.qty), str(line.unit_price)) for line in inv.lines)
    cands = []
    for o in others:
        same_po = bool(inv.po_number) and o.po_number == inv.po_number
        same_lines = (
            sorted((line.sku, str(line.qty), str(line.unit_price)) for line in o.lines) == sig
        )
        if same_po or same_lines:
            cands.append(
                DupCandidate(
                    invoice_ref=o.invoice_ref,
                    status=o.status,
                    total=o.total,
                    issue_date=o.issue_date,
                    same_po=same_po,
                    same_lines=same_lines,
                    amount_paid=o.amount_paid,
                )
            )
    strong = [c for c in cands if c.same_po and c.same_lines]
    return DupResult(
        invoice_ref=inv.invoice_ref,
        is_duplicate=bool(strong),
        candidates=cands,
        explanation=(
            f"{inv.invoice_ref} duplicates {strong[0].invoice_ref} ({strong[0].status}, paid {strong[0].amount_paid})."
            if strong
            else "No invoice shares both PO and line items."
        ),
    )


class PlanArgs(BaseModel):
    invoice_refs: list[str] = Field(min_length=1)
    installments: int = Field(ge=1, le=12)
    first_due: date
    discount_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)


class PlanInstallment(BaseModel):
    n: int
    due: date
    amount: Decimal


class PlanResult(BaseModel):
    invoice_refs: list[str]
    total_open: Decimal
    discount_pct: Decimal
    discount_amount: Decimal
    plan_total: Decimal
    installments: list[PlanInstallment]
    extension_days: int


@tool(
    name="simulate_payment_plan",
    description="Compute an installment schedule (with optional early-pay discount) for one or more open invoices. Pure calculation; nothing is applied.",
    result=PlanResult,
    tags=["negotiate"],
)
async def simulate_payment_plan(ctx: ToolContext, args: PlanArgs) -> PlanResult:
    invoices = [await ctx.erp.get_invoice(r) for r in args.invoice_refs]
    total_open = money(sum((i.amount_open for i in invoices), Decimal("0")))
    discount = money(total_open * args.discount_pct / 100)
    plan_total = money(total_open - discount)
    per = money(plan_total / args.installments)
    rows, remaining = [], plan_total
    for n in range(args.installments):
        amt = per if n < args.installments - 1 else remaining
        remaining = money(remaining - amt)
        rows.append(
            PlanInstallment(n=n + 1, due=args.first_due + timedelta(days=30 * n), amount=amt)
        )
    earliest_due = min(i.due_date for i in invoices)
    return PlanResult(
        invoice_refs=args.invoice_refs,
        total_open=total_open,
        discount_pct=args.discount_pct,
        discount_amount=discount,
        plan_total=plan_total,
        installments=rows,
        extension_days=max(0, (rows[-1].due - earliest_due).days),
    )
