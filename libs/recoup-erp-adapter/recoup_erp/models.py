"""Canonical ERP domain models. Every connector maps its native shapes to these."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

InvoiceStatus = Literal["OPEN", "PARTIALLY_PAID", "PAID", "VOID"]


class Contact(BaseModel):
    name: str
    email: str
    role: str = "AP"
    active: bool = True
    phone: str | None = None


class Customer(BaseModel):
    customer_ref: str
    name: str
    payment_terms_days: int = 30
    requires_po: bool = False
    credit_hold: bool = False
    credit_risk_score: float = Field(ge=0, le=1, default=0.2)
    contacts: list[Contact] = Field(default_factory=list)
    billing_address: str | None = None

    def primary_ap_contact(self) -> Contact | None:
        for c in self.contacts:
            if c.active and c.role == "AP":
                return c
        return next((c for c in self.contacts if c.active), None)


class InvoiceLine(BaseModel):
    line_no: int
    sku: str
    description: str
    qty: Decimal
    unit_price: Decimal
    amount: Decimal

    @property
    def is_freight(self) -> bool:
        return self.sku == "FREIGHT"


class Invoice(BaseModel):
    invoice_ref: str
    customer_ref: str
    po_number: str | None
    status: InvoiceStatus
    issue_date: date
    due_date: date
    currency: str = "USD"
    subtotal: Decimal
    freight: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    total: Decimal
    amount_paid: Decimal = Decimal("0")
    amount_open: Decimal
    billed_to_email: str | None = None
    lines: list[InvoiceLine] = Field(default_factory=list)

    def days_overdue(self, as_of: date) -> int:
        return max(0, (as_of - self.due_date).days)


class PurchaseOrderLine(BaseModel):
    line_no: int
    sku: str
    description: str
    qty: Decimal
    unit_price: Decimal


class PurchaseOrder(BaseModel):
    po_number: str
    customer_ref: str
    status: str = "OPEN"
    issued_at: date
    currency: str = "USD"
    lines: list[PurchaseOrderLine] = Field(default_factory=list)
    total: Decimal


class DeliveryLine(BaseModel):
    line_no: int
    sku: str
    qty_delivered: Decimal


class DeliveryProof(BaseModel):
    invoice_ref: str
    delivered_at: datetime | None
    signed_by: str | None
    carrier: str | None
    tracking_number: str | None
    lines: list[DeliveryLine] = Field(default_factory=list)


class ContractTerms(BaseModel):
    customer_ref: str
    effective_from: date
    payment_terms_days: int
    price_list: dict[str, Decimal] = Field(default_factory=dict)
    freight_billable: bool = True
    early_pay_discount_pct: Decimal = Decimal("0")
    max_discount_pct: Decimal = Decimal("2")
    dispute_window_days: int = 30


class Remittance(BaseModel):
    remittance_id: str
    customer_ref: str
    invoice_ref: str | None
    amount: Decimal
    received_at: datetime
    method: str = "ACH"
    memo: str | None = None


class CreditMemoRequest(BaseModel):
    invoice_ref: str
    amount: Decimal
    reason_code: str
    memo: str
    idempotency_key: str


class CreditMemo(BaseModel):
    credit_memo_ref: str
    invoice_ref: str
    customer_ref: str
    amount: Decimal
    reason_code: str
    memo: str
    created_at: datetime


class PaymentPlanRequest(BaseModel):
    invoice_refs: list[str]
    installments: int = Field(ge=1, le=12)
    first_due: date
    discount_pct: Decimal = Decimal("0")
    idempotency_key: str


class PaymentPlan(BaseModel):
    plan_ref: str
    customer_ref: str
    invoice_refs: list[str]
    installments: list[dict[str, str]]
    total: Decimal
    discount_pct: Decimal
    created_at: datetime
