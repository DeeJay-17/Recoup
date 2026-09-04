from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from recoup_common.db import Database, utcnow
from recoup_common.errors import ConflictError, NotFoundError, ValidationError
from recoup_erp.models import (
    Contact,
    ContractTerms,
    CreditMemo,
    CreditMemoRequest,
    Customer,
    DeliveryLine,
    DeliveryProof,
    Invoice,
    InvoiceLine,
    PaymentPlan,
    PaymentPlanRequest,
    PurchaseOrder,
    PurchaseOrderLine,
    Remittance,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_mock_erp import models as m
from recoup_mock_erp import repo

router = APIRouter()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


# ---------- mappers ----------
def customer_out(c: m.Customer) -> Customer:
    return Customer(
        customer_ref=c.customer_ref,
        name=c.name,
        payment_terms_days=c.payment_terms_days,
        requires_po=c.requires_po,
        credit_hold=c.credit_hold,
        credit_risk_score=float(c.credit_risk_score),
        contacts=[Contact.model_validate(x) for x in c.contacts],
        billing_address=c.billing_address,
    )


def invoice_out(i: m.Invoice) -> Invoice:
    return Invoice(
        invoice_ref=i.invoice_ref,
        customer_ref=i.customer_ref,
        po_number=i.po_number,
        status=i.status,  # type: ignore[arg-type]
        issue_date=i.issue_date,
        due_date=i.due_date,
        currency=i.currency,
        subtotal=i.subtotal,
        freight=i.freight,
        tax=i.tax,
        total=i.total,
        amount_paid=i.amount_paid,
        amount_open=i.amount_open,
        billed_to_email=i.billed_to_email,
        lines=[InvoiceLine.model_validate(x) for x in i.lines],
    )


# ---------- read API (adapter surface) ----------
@router.get("/customers/{customer_ref}", response_model=Customer, tags=["erp"])
async def get_customer(customer_ref: str, session: SessionDep) -> Customer:
    c = await session.get(m.Customer, customer_ref)
    if not c:
        raise NotFoundError(f"customer {customer_ref} not found")
    return customer_out(c)


class InvoicePage(BaseModel):
    items: list[Invoice]
    total: int
    as_of: date


@router.get("/invoices", response_model=InvoicePage, tags=["erp"])
async def list_invoices(
    session: SessionDep,
    status: str | None = Query(default=None, pattern="^(OPEN|PARTIALLY_PAID|PAID|VOID)$"),
    customer_ref: str | None = None,
    overdue_days_gte: int = Query(default=0, ge=0),
    as_of: date | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> InvoicePage:
    today = as_of or date.today()
    rows, total = await repo.list_invoices(
        session,
        status=status,
        customer_ref=customer_ref,
        overdue_days_gte=overdue_days_gte,
        as_of=today,
        limit=limit,
        offset=offset,
    )
    return InvoicePage(items=[invoice_out(r) for r in rows], total=total, as_of=today)


@router.get("/invoices/{invoice_ref}", response_model=Invoice, tags=["erp"])
async def get_invoice(invoice_ref: str, session: SessionDep) -> Invoice:
    i = await session.get(m.Invoice, invoice_ref)
    if not i:
        raise NotFoundError(f"invoice {invoice_ref} not found")
    return invoice_out(i)


@router.get("/invoices/{invoice_ref}/delivery-proof", response_model=DeliveryProof, tags=["erp"])
async def get_delivery_proof(invoice_ref: str, session: SessionDep) -> DeliveryProof:
    d = await session.get(m.Delivery, invoice_ref)
    if not d:
        raise NotFoundError(f"no delivery proof for {invoice_ref}")
    return DeliveryProof(
        invoice_ref=d.invoice_ref,
        delivered_at=d.delivered_at,
        signed_by=d.signed_by,
        carrier=d.carrier,
        tracking_number=d.tracking_number,
        lines=[DeliveryLine.model_validate(x) for x in d.lines],
    )


@router.get("/purchase-orders/{po_number}", response_model=PurchaseOrder, tags=["erp"])
async def get_po(po_number: str, session: SessionDep) -> PurchaseOrder:
    p = await session.get(m.PurchaseOrder, po_number)
    if not p:
        raise NotFoundError(f"purchase order {po_number} not found")
    return PurchaseOrder(
        po_number=p.po_number,
        customer_ref=p.customer_ref,
        status=p.status,
        issued_at=p.issued_at,
        currency=p.currency,
        lines=[PurchaseOrderLine.model_validate(x) for x in p.lines],
        total=p.total,
    )


@router.get("/customers/{customer_ref}/contract", response_model=ContractTerms, tags=["erp"])
async def get_contract(customer_ref: str, session: SessionDep) -> ContractTerms:
    k = await session.get(m.Contract, customer_ref)
    if not k:
        raise NotFoundError(f"no contract for {customer_ref}")
    return ContractTerms(
        customer_ref=k.customer_ref,
        effective_from=k.effective_from,
        payment_terms_days=k.payment_terms_days,
        price_list={sku: Decimal(p) for sku, p in k.price_list.items()},
        freight_billable=k.freight_billable,
        early_pay_discount_pct=k.early_pay_discount_pct,
        max_discount_pct=k.max_discount_pct,
        dispute_window_days=k.dispute_window_days,
    )


class RemittancePage(BaseModel):
    items: list[Remittance]


@router.get("/customers/{customer_ref}/remittances", response_model=RemittancePage, tags=["erp"])
async def get_remittances(
    customer_ref: str, session: SessionDep, invoice_ref: str | None = None
) -> RemittancePage:
    stmt = select(m.Remittance).where(m.Remittance.customer_ref == customer_ref)
    if invoice_ref:
        stmt = stmt.where(m.Remittance.invoice_ref == invoice_ref)
    rows = (await session.scalars(stmt.order_by(m.Remittance.received_at.desc()))).all()
    return RemittancePage(
        items=[
            Remittance(
                remittance_id=r.remittance_id,
                customer_ref=r.customer_ref,
                invoice_ref=r.invoice_ref,
                amount=r.amount,
                received_at=r.received_at,
                method=r.method,
                memo=r.memo,
            )
            for r in rows
        ]
    )


# ---------- mutating API (idempotent) ----------
@router.post("/credit-memos", response_model=CreditMemo, status_code=201, tags=["erp"])
async def create_credit_memo(body: CreditMemoRequest, session: SessionDep) -> CreditMemo:
    existing = await session.scalar(
        select(m.CreditMemo).where(m.CreditMemo.idempotency_key == body.idempotency_key)
    )
    if existing:
        return _cm_out(existing)
    inv = await session.get(m.Invoice, body.invoice_ref)
    if not inv:
        raise NotFoundError(f"invoice {body.invoice_ref} not found")
    if body.amount <= 0 or body.amount > inv.amount_open:
        raise ValidationError(
            "credit memo amount must be > 0 and <= amount_open",
            details={"amount_open": str(inv.amount_open)},
        )
    cm = m.CreditMemo(
        credit_memo_ref=f"CM-{uuid.uuid4().hex[:8].upper()}",
        invoice_ref=inv.invoice_ref,
        customer_ref=inv.customer_ref,
        amount=body.amount,
        reason_code=body.reason_code,
        memo=body.memo,
        idempotency_key=body.idempotency_key,
        created_at=utcnow(),
    )
    session.add(cm)
    await repo.apply_credit_memo(session, inv, body.amount)
    await session.flush()
    return _cm_out(cm)


def _cm_out(cm: m.CreditMemo) -> CreditMemo:
    return CreditMemo(
        credit_memo_ref=cm.credit_memo_ref,
        invoice_ref=cm.invoice_ref,
        customer_ref=cm.customer_ref,
        amount=cm.amount,
        reason_code=cm.reason_code,
        memo=cm.memo,
        created_at=cm.created_at,
    )


@router.post("/payment-plans", response_model=PaymentPlan, status_code=201, tags=["erp"])
async def create_payment_plan(body: PaymentPlanRequest, session: SessionDep) -> PaymentPlan:
    existing = await session.scalar(
        select(m.PaymentPlan).where(m.PaymentPlan.idempotency_key == body.idempotency_key)
    )
    if existing:
        return _plan_out(existing)
    invoices = (
        await session.scalars(select(m.Invoice).where(m.Invoice.invoice_ref.in_(body.invoice_refs)))
    ).all()
    if len(invoices) != len(set(body.invoice_refs)):
        raise NotFoundError("one or more invoices not found")
    customer_refs = {i.customer_ref for i in invoices}
    if len(customer_refs) != 1:
        raise ConflictError("all invoices in a plan must belong to one customer")
    total_open = sum((i.amount_open for i in invoices), Decimal("0"))
    discounted = (total_open * (Decimal("1") - body.discount_pct / Decimal("100"))).quantize(
        Decimal("0.01")
    )
    per = (discounted / body.installments).quantize(Decimal("0.01"))
    installments = []
    remaining = discounted
    for n in range(body.installments):
        amt = per if n < body.installments - 1 else remaining
        remaining -= amt
        installments.append(
            {
                "n": str(n + 1),
                "due": (body.first_due + timedelta(days=30 * n)).isoformat(),
                "amount": str(amt),
            }
        )
    plan = m.PaymentPlan(
        plan_ref=f"PLAN-{uuid.uuid4().hex[:8].upper()}",
        customer_ref=customer_refs.pop(),
        invoice_refs=list(body.invoice_refs),
        installments=installments,
        total=discounted,
        discount_pct=body.discount_pct,
        idempotency_key=body.idempotency_key,
        created_at=utcnow(),
    )
    session.add(plan)
    await session.flush()
    return _plan_out(plan)


def _plan_out(p: m.PaymentPlan) -> PaymentPlan:
    return PaymentPlan(
        plan_ref=p.plan_ref,
        customer_ref=p.customer_ref,
        invoice_refs=p.invoice_refs,
        installments=p.installments,  # type: ignore[arg-type]
        total=p.total,
        discount_pct=p.discount_pct,
        created_at=p.created_at,
    )


# ---------- admin (never exposed to agents) ----------
class SeedRequest(BaseModel):
    seed: int = 42
    customers: int = Field(default=60, ge=1, le=2000)
    invoices: int = Field(default=400, ge=1, le=20000)
    as_of: date | None = None


class SeedResponse(BaseModel):
    seed: int
    as_of: date
    customers: int
    invoices: int
    scenario_counts: dict[str, int]


@router.post("/admin/seed", response_model=SeedResponse, tags=["admin"])
async def admin_seed(body: SeedRequest, session: SessionDep) -> SeedResponse:
    ds = await repo.seed(
        session, seed=body.seed, customers=body.customers, invoices=body.invoices, as_of=body.as_of
    )
    return SeedResponse(
        seed=ds.seed,
        as_of=ds.as_of,
        customers=len(ds.customers),
        invoices=len(ds.invoices),
        scenario_counts=ds.scenario_counts(),
    )


@router.get("/admin/ground-truth/{invoice_ref}", tags=["admin"])
async def ground_truth(invoice_ref: str, session: SessionDep) -> dict[str, Any]:
    inv = await session.get(m.Invoice, invoice_ref)
    if not inv:
        raise NotFoundError(f"invoice {invoice_ref} not found")
    return {"invoice_ref": inv.invoice_ref, "scenario": inv.scenario, **inv.ground_truth}


@router.get("/admin/stats", tags=["admin"])
async def stats(session: SessionDep) -> dict[str, Any]:
    from sqlalchemy import func

    rows = (
        await session.execute(
            select(
                m.Invoice.scenario, m.Invoice.status, func.count(), func.sum(m.Invoice.amount_open)
            )
            .group_by(m.Invoice.scenario, m.Invoice.status)
            .order_by(m.Invoice.scenario, m.Invoice.status)
        )
    ).all()
    return {
        "by_scenario_status": [
            {"scenario": s, "status": st, "count": c, "amount_open": str(a or 0)}
            for s, st, c, a in rows
        ]
    }
