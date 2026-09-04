from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from recoup_common.db import make_metadata
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "mockerp"


class Base(DeclarativeBase):
    metadata = make_metadata(SCHEMA)


NUM = Numeric(14, 2)


class Customer(Base):
    __tablename__ = "customers"
    customer_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)
    requires_po: Mapped[bool] = mapped_column(Boolean, nullable=False)
    credit_hold: Mapped[bool] = mapped_column(Boolean, nullable=False)
    credit_risk_score: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    contacts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    billing_address: Mapped[str | None] = mapped_column(Text)


class Contract(Base):
    __tablename__ = "contracts"
    customer_ref: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.customers.customer_ref"), primary_key=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price_list: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    freight_billable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    early_pay_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    max_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    dispute_window_days: Mapped[int] = mapped_column(Integer, nullable=False)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    po_number: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_ref: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.customers.customer_ref"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    total: Mapped[Decimal] = mapped_column(NUM, nullable=False)


class Invoice(Base):
    __tablename__ = "invoices"
    invoice_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_ref: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.customers.customer_ref"), nullable=False
    )
    po_number: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    freight: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    tax: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    total: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    amount_open: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    billed_to_email: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    scenario: Mapped[str] = mapped_column(Text, nullable=False)
    ground_truth: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class Delivery(Base):
    __tablename__ = "deliveries"
    invoice_ref: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.invoices.invoice_ref"), primary_key=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_by: Mapped[str | None] = mapped_column(Text)
    carrier: Mapped[str | None] = mapped_column(Text)
    tracking_number: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)


class Remittance(Base):
    __tablename__ = "remittances"
    remittance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_ref: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.customers.customer_ref"), nullable=False
    )
    invoice_ref: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    memo: Mapped[str | None] = mapped_column(Text)


class CreditMemo(Base):
    __tablename__ = "credit_memos"
    credit_memo_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    invoice_ref: Mapped[str] = mapped_column(
        Text, ForeignKey(f"{SCHEMA}.invoices.invoice_ref"), nullable=False
    )
    customer_ref: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    memo: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentPlan(Base):
    __tablename__ = "payment_plans"
    plan_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_ref: Mapped[str] = mapped_column(Text, nullable=False)
    invoice_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    installments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    total: Mapped[Decimal] = mapped_column(NUM, nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SeedRun(Base):
    __tablename__ = "seed_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    customers: Mapped[int] = mapped_column(Integer, nullable=False)
    invoices: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
