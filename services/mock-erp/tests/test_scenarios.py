from datetime import date
from decimal import Decimal

from recoup_mock_erp.scenarios import ROOT_CAUSE_FOR, Scenario, ScenarioGenerator

AS_OF = date(2026, 9, 1)


def test_deterministic_for_same_seed() -> None:
    a = ScenarioGenerator(7, as_of=AS_OF).generate(20, 120)
    b = ScenarioGenerator(7, as_of=AS_OF).generate(20, 120)
    assert [i["invoice_ref"] for i in a.invoices] == [i["invoice_ref"] for i in b.invoices]
    assert [i["total"] for i in a.invoices] == [i["total"] for i in b.invoices]
    assert a.scenario_counts() == b.scenario_counts()


def test_different_seed_differs() -> None:
    a = ScenarioGenerator(1, as_of=AS_OF).generate(10, 60)
    b = ScenarioGenerator(2, as_of=AS_OF).generate(10, 60)
    assert [i["total"] for i in a.invoices] != [i["total"] for i in b.invoices]


def test_every_scenario_present_and_consistent() -> None:
    ds = ScenarioGenerator(42, as_of=AS_OF).generate(60, 400)
    counts = ds.scenario_counts()
    for s in Scenario:
        assert counts.get(s.value, 0) > 0, f"scenario {s} never generated"

    invoices = {i["invoice_ref"]: i for i in ds.invoices}
    pos = {p["po_number"]: p for p in ds.purchase_orders}
    deliveries = {d["invoice_ref"]: d for d in ds.deliveries}
    contracts = {c["customer_ref"]: c for c in ds.contracts}
    customers = {c["customer_ref"]: c for c in ds.customers}

    for inv in ds.invoices:
        gt = inv["ground_truth"]
        assert gt["root_cause"] == ROOT_CAUSE_FOR[Scenario(inv["scenario"])]
        # arithmetic holds
        assert inv["total"] == inv["subtotal"] + inv["freight"] + inv["tax"]
        assert inv["amount_open"] == inv["total"] - inv["amount_paid"]
        # overdue scenarios are actually overdue and open
        if inv["scenario"] not in (Scenario.CLEAN.value,):
            assert inv["due_date"] < AS_OF
            assert inv["amount_open"] > 0

        s = Scenario(inv["scenario"])
        if s is Scenario.DISPUTE_PRICING:
            po = pos[inv["po_number"]]
            po_prices = {line["sku"]: Decimal(line["unit_price"]) for line in po["lines"]}
            over = [
                line
                for line in inv["lines"]
                if Decimal(line["unit_price"]) > po_prices[line["sku"]]
            ]
            assert over, "pricing dispute must have an overpriced line"
            assert Decimal(gt["expected_credit_memo"]) > 0
        elif s is Scenario.DISPUTE_QUANTITY:
            d = deliveries[inv["invoice_ref"]]
            short = [
                (line, dl)
                for line in inv["lines"]
                for dl in d["lines"]
                if dl["line_no"] == line["line_no"]
                and Decimal(dl["qty_delivered"]) < Decimal(line["qty"])
            ]
            assert short
        elif s is Scenario.MISSING_PO:
            assert customers[inv["customer_ref"]]["requires_po"] is True
            assert inv["po_number"] is None or not inv["po_number"].startswith("PO-")
        elif s is Scenario.WRONG_CONTACT:
            contact = next(
                c
                for c in customers[inv["customer_ref"]]["contacts"]
                if c["email"] == inv["billed_to_email"]
            )
            assert contact["active"] is False
        elif s is Scenario.SHORT_PAY:
            assert inv["status"] == "PARTIALLY_PAID"
            assert inv["amount_open"] == inv["freight"]
            billable = contracts[inv["customer_ref"]]["freight_billable"]
            expected = Decimal("0.00") if billable else inv["freight"]
            assert Decimal(gt["expected_credit_memo"]) == expected
        elif s is Scenario.DUPLICATE_INVOICE:
            orig = invoices[gt["duplicate_of"]]
            assert orig["status"] == "PAID"
            assert orig["po_number"] == inv["po_number"]
            assert Decimal(gt["expected_credit_memo"]) == inv["total"]
        elif s is Scenario.CASH_FLOW:
            assert Decimal(str(customers[inv["customer_ref"]]["credit_risk_score"])) >= Decimal(
                "0.55"
            )


def test_overdue_share_roughly_matches_target() -> None:
    ds = ScenarioGenerator(42, as_of=AS_OF).generate(60, 1000)
    overdue = [i for i in ds.invoices if i["scenario"] != "CLEAN"]
    share = len(overdue) / len(ds.invoices)
    assert 0.35 < share < 0.6
