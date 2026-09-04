from datetime import date
from decimal import Decimal

from recoup_erp import Contact, Customer, Invoice, InvoiceLine


def test_primary_ap_contact_skips_inactive() -> None:
    c = Customer(
        customer_ref="C1",
        name="X",
        contacts=[
            Contact(name="Old", email="old@x.com", role="AP", active=False),
            Contact(name="Buyer", email="b@x.com", role="Purchasing", active=True),
            Contact(name="New", email="new@x.com", role="AP", active=True),
        ],
    )
    p = c.primary_ap_contact()
    assert p is not None and p.email == "new@x.com"


def test_invoice_days_overdue_and_freight_line() -> None:
    inv = Invoice(
        invoice_ref="INV-1",
        customer_ref="C1",
        po_number=None,
        status="OPEN",
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        subtotal=Decimal("10"),
        total=Decimal("10"),
        amount_open=Decimal("10"),
        lines=[
            InvoiceLine(
                line_no=1,
                sku="FREIGHT",
                description="f",
                qty=Decimal(1),
                unit_price=Decimal(5),
                amount=Decimal(5),
            )
        ],
    )
    assert inv.days_overdue(date(2026, 8, 10)) == 10
    assert inv.days_overdue(date(2026, 7, 1)) == 0
    assert inv.lines[0].is_freight
