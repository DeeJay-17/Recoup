"""Read-only ERP tools. No side effects, no policy checks."""

from __future__ import annotations

from pydantic import BaseModel, Field
from recoup_erp import (
    ContractTerms,
    Customer,
    DeliveryProof,
    Invoice,
    PurchaseOrder,
    Remittance,
)

from recoup_tool_gateway.context import ToolContext
from recoup_tool_gateway.registry import tool


class InvoiceRefArgs(BaseModel):
    invoice_ref: str = Field(description="Invoice number, e.g. INV-100123")


class CustomerRefArgs(BaseModel):
    customer_ref: str = Field(description="ERP customer id, e.g. CUST-0007")


class PoArgs(BaseModel):
    po_number: str = Field(description="Customer PO number as printed on the invoice")


class OpenInvoicesArgs(BaseModel):
    customer_ref: str = Field(description="ERP customer id")
    overdue_days_gte: int = Field(
        default=0, ge=0, description="Only invoices overdue at least this many days"
    )


class InvoiceList(BaseModel):
    items: list[Invoice]


class DeliveryProofResult(BaseModel):
    found: bool
    proof: DeliveryProof | None = None


class RemittanceList(BaseModel):
    items: list[Remittance]


@tool(
    name="get_invoice",
    description="Fetch an invoice with its line items, totals, PO number and payment status from the ERP.",
    result=Invoice,
    tags=["erp"],
)
async def get_invoice(ctx: ToolContext, args: InvoiceRefArgs) -> Invoice:
    return await ctx.erp.get_invoice(args.invoice_ref)


@tool(
    name="get_customer",
    description="Fetch a customer's master record: payment terms, PO requirement, credit hold, risk score and contacts.",
    result=Customer,
    tags=["erp"],
)
async def get_customer(ctx: ToolContext, args: CustomerRefArgs) -> Customer:
    return await ctx.erp.get_customer(args.customer_ref)


@tool(
    name="get_purchase_order",
    description="Fetch a customer purchase order and its line items from the ERP.",
    result=PurchaseOrder,
    tags=["erp"],
)
async def get_purchase_order(ctx: ToolContext, args: PoArgs) -> PurchaseOrder:
    return await ctx.erp.get_purchase_order(args.po_number)


@tool(
    name="get_delivery_proof",
    description="Fetch proof of delivery (carrier, signature, delivered quantities per line) for an invoice, if any.",
    result=DeliveryProofResult,
    tags=["erp"],
)
async def get_delivery_proof(ctx: ToolContext, args: InvoiceRefArgs) -> DeliveryProofResult:
    proof = await ctx.erp.get_delivery_proof(args.invoice_ref)
    return DeliveryProofResult(found=proof is not None, proof=proof)


@tool(
    name="get_contract_terms",
    description="Fetch the customer's contract: negotiated price list, whether freight is billable, discount limits and dispute window.",
    result=ContractTerms,
    tags=["erp"],
)
async def get_contract_terms(ctx: ToolContext, args: CustomerRefArgs) -> ContractTerms:
    return await ctx.erp.get_contract_terms(args.customer_ref)


class RemittanceArgs(BaseModel):
    customer_ref: str
    invoice_ref: str | None = Field(default=None, description="Filter to one invoice")


@tool(
    name="get_remittances",
    description="List payments received from a customer (amount, date, method, remittance memo), optionally for one invoice.",
    result=RemittanceList,
    tags=["erp"],
)
async def get_remittances(ctx: ToolContext, args: RemittanceArgs) -> RemittanceList:
    return RemittanceList(
        items=await ctx.erp.get_remittances(args.customer_ref, invoice_ref=args.invoice_ref)
    )


@tool(
    name="list_open_invoices",
    description="List a customer's open invoices (useful to spot duplicates or bundle a payment plan).",
    result=InvoiceList,
    tags=["erp"],
)
async def list_open_invoices(ctx: ToolContext, args: OpenInvoicesArgs) -> InvoiceList:
    return InvoiceList(
        items=await ctx.erp.list_open_invoices(
            customer_ref=args.customer_ref, overdue_days_gte=args.overdue_days_gte, limit=100
        )
    )
