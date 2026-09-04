import pytest
from recoup_common.errors import ValidationError
from recoup_communication import templating
from recoup_communication.linking import (
    extract_invoice_refs,
    normalise_address,
    strip_reply_prefix,
)


def test_extract_invoice_refs_dedupes_and_uppercases() -> None:
    refs = extract_invoice_refs("Re: inv-100123 and INV-100123", "also INV-100999 please", None)
    assert refs == ["INV-100123", "INV-100999"]


def test_normalise_address() -> None:
    assert normalise_address("Jane Doe <Jane@X.com>") == "jane@x.com"
    assert normalise_address("  bob@y.io ") == "bob@y.io"


def test_strip_reply_prefix() -> None:
    assert strip_reply_prefix("RE: Fwd: re: Invoice INV-1") == "Invoice INV-1"


def test_templates_list_and_render() -> None:
    names = {t["name"] for t in templating.list_templates()}
    assert {"po_request", "payment_reminder", "dispute_resolution"} <= names
    subject, body, missing = templating.render(
        "po_request",
        {
            "invoice_ref": "INV-100123",
            "contact_name": "Jane",
            "amount": "1,234.50",
            "currency": "USD",
            "issue_date": "2026-07-01",
            "sender_name": "Ava",
            "company_name": "Acme",
        },
    )
    assert subject == "PO number needed for invoice INV-100123"
    assert "purchase order number" in body
    assert missing == []


def test_template_missing_variable_is_explicit() -> None:
    with pytest.raises(ValidationError) as ei:
        templating.render("po_request", {"invoice_ref": "INV-1"})
    assert "contact_name" in ei.value.details["missing_variables"]
