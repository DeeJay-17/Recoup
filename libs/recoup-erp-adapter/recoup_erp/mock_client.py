from __future__ import annotations

from typing import Any

import httpx

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


class ERPError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"ERP {status}: {message}")
        self.status = status


class ERPNotFound(ERPError):
    pass


class MockERPAdapter:
    """HTTP client for the Mock ERP service (services/mock-erp)."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        r = await self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        return self._handle(r)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = await self._client.post(path, json=body)
        return self._handle(r)

    @staticmethod
    def _handle(r: httpx.Response) -> Any:
        if r.status_code == 404:
            raise ERPNotFound(404, r.text)
        if r.status_code >= 400:
            raise ERPError(r.status_code, r.text)
        return r.json()

    async def get_customer(self, customer_ref: str) -> Customer:
        return Customer.model_validate(await self._get(f"/customers/{customer_ref}"))

    async def get_invoice(self, invoice_ref: str) -> Invoice:
        return Invoice.model_validate(await self._get(f"/invoices/{invoice_ref}"))

    async def list_open_invoices(
        self, *, overdue_days_gte: int = 0, customer_ref: str | None = None, limit: int = 500
    ) -> list[Invoice]:
        data = await self._get(
            "/invoices",
            status="OPEN",
            overdue_days_gte=overdue_days_gte,
            customer_ref=customer_ref,
            limit=limit,
        )
        return [Invoice.model_validate(i) for i in data["items"]]

    async def get_purchase_order(self, po_number: str) -> PurchaseOrder:
        return PurchaseOrder.model_validate(await self._get(f"/purchase-orders/{po_number}"))

    async def get_delivery_proof(self, invoice_ref: str) -> DeliveryProof | None:
        try:
            data = await self._get(f"/invoices/{invoice_ref}/delivery-proof")
        except ERPNotFound:
            return None
        return DeliveryProof.model_validate(data)

    async def get_contract_terms(self, customer_ref: str) -> ContractTerms:
        return ContractTerms.model_validate(await self._get(f"/customers/{customer_ref}/contract"))

    async def get_remittances(
        self, customer_ref: str, *, invoice_ref: str | None = None
    ) -> list[Remittance]:
        data = await self._get(f"/customers/{customer_ref}/remittances", invoice_ref=invoice_ref)
        return [Remittance.model_validate(x) for x in data["items"]]

    async def create_credit_memo(self, req: CreditMemoRequest) -> CreditMemo:
        return CreditMemo.model_validate(
            await self._post("/credit-memos", req.model_dump(mode="json"))
        )

    async def apply_payment_plan(self, req: PaymentPlanRequest) -> PaymentPlan:
        return PaymentPlan.model_validate(
            await self._post("/payment-plans", req.model_dump(mode="json"))
        )
