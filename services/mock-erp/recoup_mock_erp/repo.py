from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_mock_erp import models as m
from recoup_mock_erp.scenarios import Dataset, ScenarioGenerator


async def replace_dataset(session: AsyncSession, ds: Dataset) -> None:
    for table in (
        m.PaymentPlan,
        m.CreditMemo,
        m.Remittance,
        m.Delivery,
        m.Invoice,
        m.PurchaseOrder,
        m.Contract,
        m.Customer,
    ):
        await session.execute(delete(table))
    # No ORM relationships between these tables, so flush explicitly in FK order.
    session.add_all(m.Customer(**c) for c in ds.customers)
    await session.flush()
    session.add_all(m.Contract(**c) for c in ds.contracts)
    session.add_all(m.PurchaseOrder(**p) for p in ds.purchase_orders)
    await session.flush()
    session.add_all(m.Invoice(**i) for i in ds.invoices)
    await session.flush()
    session.add_all(m.Delivery(**d) for d in ds.deliveries)
    session.add_all(m.Remittance(**r) for r in ds.remittances)
    session.add(
        m.SeedRun(
            seed=ds.seed, as_of=ds.as_of, customers=len(ds.customers), invoices=len(ds.invoices)
        )
    )


async def seed(
    session: AsyncSession, *, seed: int, customers: int, invoices: int, as_of: date | None
) -> Dataset:
    ds = ScenarioGenerator(seed, as_of=as_of).generate(customers, invoices)
    await replace_dataset(session, ds)
    return ds


async def is_seeded(session: AsyncSession) -> bool:
    return (await session.scalar(select(m.SeedRun.id).limit(1))) is not None


async def list_invoices(
    session: AsyncSession,
    *,
    status: str | None,
    customer_ref: str | None,
    overdue_days_gte: int,
    as_of: date,
    limit: int,
    offset: int,
) -> tuple[list[m.Invoice], int]:
    from sqlalchemy import func

    stmt = select(m.Invoice)
    if status:
        statuses = ["OPEN", "PARTIALLY_PAID"] if status == "OPEN" else [status]
        stmt = stmt.where(m.Invoice.status.in_(statuses))
    if customer_ref:
        stmt = stmt.where(m.Invoice.customer_ref == customer_ref)
    if overdue_days_gte > 0:
        from datetime import timedelta

        stmt = stmt.where(m.Invoice.due_date <= as_of - timedelta(days=overdue_days_gte))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.scalars(
            stmt.order_by(m.Invoice.due_date, m.Invoice.invoice_ref).limit(limit).offset(offset)
        )
    ).all()
    return list(rows), int(total or 0)


async def apply_credit_memo(session: AsyncSession, invoice: m.Invoice, amount: Decimal) -> None:
    invoice.amount_open = max(Decimal("0.00"), invoice.amount_open - amount)
    if invoice.amount_open == 0:
        invoice.status = "PAID" if invoice.amount_paid > 0 else "VOID"
