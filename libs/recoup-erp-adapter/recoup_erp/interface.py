from __future__ import annotations

from typing import Protocol

from recoup_erp.models import (
    ContractTerms,
    CreditMemo,
    CreditMemoRequest,
    Customer,
    DeliveryProof,
    Invoice,
    PaymentPlan,
    PaymentPlanRequest,
    PurchaseOrder,
    Remittance,
)


class ERPAdapter(Protocol):
    """Connector contract. Real ERPs (NetSuite, SAP, QuickBooks) implement this."""

    async def get_customer(self, customer_ref: str) -> Customer: ...
    async def get_invoice(self, invoice_ref: str) -> Invoice: ...
    async def list_open_invoices(
        self, *, overdue_days_gte: int = 0, customer_ref: str | None = None, limit: int = 500
    ) -> list[Invoice]: ...
    async def get_purchase_order(self, po_number: str) -> PurchaseOrder: ...
    async def get_delivery_proof(self, invoice_ref: str) -> DeliveryProof | None: ...
    async def get_contract_terms(self, customer_ref: str) -> ContractTerms: ...
    async def get_remittances(
        self, customer_ref: str, *, invoice_ref: str | None = None
    ) -> list[Remittance]: ...
    async def create_credit_memo(self, req: CreditMemoRequest) -> CreditMemo: ...
    async def apply_payment_plan(self, req: PaymentPlanRequest) -> PaymentPlan: ...
