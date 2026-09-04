from datetime import UTC, date, datetime
from decimal import Decimal

from recoup_erp import ContractTerms, DeliveryProof, Invoice, PurchaseOrder
from recoup_erp.models import DeliveryLine, InvoiceLine, PurchaseOrderLine
from recoup_tool_gateway.tools.reconcile import reconcile

D = Decimal


def _invoice(lines: list[tuple[str, str, str]], freight: str = "0") -> Invoice:
    inv_lines = [
        InvoiceLine(
            line_no=i + 1, sku=sku, description=sku, qty=D(q), unit_price=D(p), amount=D(q) * D(p)
        )
        for i, (sku, q, p) in enumerate(lines)
    ]
    subtotal = sum((line.amount for line in inv_lines), D("0"))
    tax = (subtotal * D("0.07")).quantize(D("0.01"))
    total = subtotal + D(freight) + tax
    return Invoice(
        invoice_ref="INV-1",
        customer_ref="C1",
        po_number="PO-1",
        status="OPEN",
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        subtotal=subtotal,
        freight=D(freight),
        tax=tax,
        total=total,
        amount_paid=D("0"),
        amount_open=total,
        lines=inv_lines,
    )


def _po(lines: list[tuple[str, str, str]], customer: str = "C1") -> PurchaseOrder:
    po_lines = [
        PurchaseOrderLine(line_no=i + 1, sku=s, description=s, qty=D(q), unit_price=D(p))
        for i, (s, q, p) in enumerate(lines)
    ]
    return PurchaseOrder(
        po_number="PO-1",
        customer_ref=customer,
        issued_at=date(2026, 6, 20),
        lines=po_lines,
        total=sum((line.qty * line.unit_price for line in po_lines), D("0")),
    )


def _delivery(qtys: dict[str, str]) -> DeliveryProof:
    return DeliveryProof(
        invoice_ref="INV-1",
        delivered_at=datetime(2026, 6, 30, tzinfo=UTC),
        signed_by="x",
        carrier="UPS",
        tracking_number="1",
        lines=[
            DeliveryLine(line_no=i + 1, sku=s, qty_delivered=D(q))
            for i, (s, q) in enumerate(qtys.items())
        ],
    )


def _contract(freight_billable: bool = True, prices: dict[str, str] | None = None) -> ContractTerms:
    return ContractTerms(
        customer_ref="C1",
        effective_from=date(2025, 1, 1),
        payment_terms_days=30,
        price_list={k: D(v) for k, v in (prices or {}).items()},
        freight_billable=freight_billable,
    )


def test_clean_invoice_has_no_discrepancies() -> None:
    inv = _invoice([("A", "10", "5.00"), ("B", "2", "100.00")])
    r = reconcile(
        inv,
        _po([("A", "10", "5.00"), ("B", "2", "100.00")]),
        _delivery({"A": "10", "B": "2"}),
        _contract(),
    )
    assert r.discrepancies == []
    assert r.proposed_credit_memo == D("0.00")
    assert r.po_customer_matches


def test_price_above_po_credits_delta_with_tax() -> None:
    inv = _invoice([("A", "10", "6.00")])
    r = reconcile(inv, _po([("A", "10", "5.00")]), None, None)
    assert [d.kind for d in r.discrepancies] == ["PRICE_ABOVE_PO"]
    assert r.credit_before_tax == D("10.00")
    assert r.proposed_credit_memo == D("10.70")


def test_short_delivery_credits_short_units_and_flags_rebill() -> None:
    inv = _invoice([("A", "10", "5.00")])
    r = reconcile(inv, _po([("A", "10", "5.00")]), _delivery({"A": "6"}), None)
    assert [d.kind for d in r.discrepancies] == ["QTY_SHORT_DELIVERED"]
    assert r.credit_before_tax == D("20.00")
    assert r.rebill_required


def test_qty_above_po_and_short_delivery_do_not_double_count() -> None:
    inv = _invoice([("A", "12", "5.00")])
    r = reconcile(inv, _po([("A", "10", "5.00")]), _delivery({"A": "8"}), None)
    kinds = sorted(d.kind for d in r.discrepancies)
    assert kinds == ["QTY_ABOVE_PO", "QTY_SHORT_DELIVERED"]
    assert r.credit_before_tax == D("20.00")  # 2 over PO + 2 short of PO qty


def test_freight_not_billable_is_untaxed_credit() -> None:
    inv = _invoice([("A", "1", "10.00")], freight="55.00")
    r = reconcile(inv, _po([("A", "1", "10.00")]), None, _contract(freight_billable=False))
    assert [d.kind for d in r.discrepancies] == ["FREIGHT_NOT_BILLABLE"]
    assert r.proposed_credit_memo == D("55.00")


def test_contract_price_used_when_no_po_line() -> None:
    inv = _invoice([("A", "4", "12.00")])
    r = reconcile(inv, None, None, _contract(prices={"A": "10.00"}))
    assert [d.kind for d in r.discrepancies] == ["PRICE_ABOVE_CONTRACT"]
    assert r.credit_before_tax == D("8.00")


def test_line_not_on_po_flagged_not_silently_credited() -> None:
    inv = _invoice([("A", "1", "10.00"), ("Z", "1", "99.00")])
    r = reconcile(inv, _po([("A", "1", "10.00")]), None, None)
    assert [d.kind for d in r.discrepancies] == ["LINE_NOT_ON_PO"]


def test_credit_capped_at_open_balance() -> None:
    inv = _invoice([("A", "10", "6.00")])
    inv = inv.model_copy(update={"amount_open": D("5.00")})
    r = reconcile(inv, _po([("A", "10", "5.00")]), None, None)
    assert r.proposed_credit_memo == D("5.00")
