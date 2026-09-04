"""Deterministic scenario generator for the Mock ERP.

Given a seed, produces customers, contracts, POs, invoices, deliveries and remittances where a
controlled fraction of open invoices embed a *known* root cause. The ground truth is stored
alongside each invoice (never exposed through the adapter API) so the eval harness can score
agents.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from faker import Faker

TWO = Decimal("0.01")


def money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(TWO, rounding=ROUND_HALF_UP)


class Scenario(StrEnum):
    CLEAN = "CLEAN"  # paid or not yet due — should never become a case
    DISPUTE_PRICING = "DISPUTE_PRICING"  # invoiced unit price > contract/PO price
    DISPUTE_QUANTITY = "DISPUTE_QUANTITY"  # invoiced qty > delivered qty
    MISSING_PO = "MISSING_PO"  # customer requires PO; invoice has none / invalid
    WRONG_CONTACT = "WRONG_CONTACT"  # invoice emailed to an inactive contact
    SHORT_PAY = "SHORT_PAY"  # customer paid less, deducting freight
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"  # same PO already invoiced & paid
    CASH_FLOW = "CASH_FLOW"  # everything matches; customer just slow


ROOT_CAUSE_FOR = {
    Scenario.CLEAN: "NONE",
    Scenario.DISPUTE_PRICING: "DISPUTE_PRICING",
    Scenario.DISPUTE_QUANTITY: "DISPUTE_QUANTITY",
    Scenario.MISSING_PO: "MISSING_PO",
    Scenario.WRONG_CONTACT: "WRONG_CONTACT",
    Scenario.SHORT_PAY: "SHORT_PAY",
    Scenario.DUPLICATE_INVOICE: "DUPLICATE_INVOICE",
    Scenario.CASH_FLOW: "CASH_FLOW",
}

# Share of *overdue* invoices per scenario (mirrors §2 of the project plan)
OVERDUE_MIX: list[tuple[Scenario, float]] = [
    (Scenario.DISPUTE_PRICING, 0.18),
    (Scenario.DISPUTE_QUANTITY, 0.12),
    (Scenario.MISSING_PO, 0.17),
    (Scenario.WRONG_CONTACT, 0.12),
    (Scenario.SHORT_PAY, 0.12),
    (Scenario.DUPLICATE_INVOICE, 0.07),
    (Scenario.CASH_FLOW, 0.22),
]
OVERDUE_SHARE = 0.45  # fraction of all invoices that are open & overdue


@dataclass
class Catalog:
    items: dict[str, tuple[str, Decimal]] = field(default_factory=dict)  # sku -> (desc, list price)

    @staticmethod
    def build(rng: random.Random) -> Catalog:
        nouns = [
            "Bearing",
            "Valve",
            "Gasket",
            "Coupling",
            "Flange",
            "Sensor",
            "Actuator",
            "Filter",
            "Pump Seal",
            "Hose",
            "Bracket",
            "Relay",
            "Bushing",
            "Manifold",
            "Spindle",
        ]
        specs = ["Std", "HD", "SS316", "Pro", "XL", "Mini"]
        cat = Catalog()
        n = 1
        for noun in nouns:
            for spec in rng.sample(specs, 2):
                sku = f"SKU-{n:04d}"
                price = money(rng.uniform(8, 480))
                cat.items[sku] = (f"{noun} {spec}", price)
                n += 1
        return cat


@dataclass
class Dataset:
    seed: int
    as_of: date
    customers: list[dict[str, Any]]
    contracts: list[dict[str, Any]]
    purchase_orders: list[dict[str, Any]]
    invoices: list[dict[str, Any]]
    deliveries: list[dict[str, Any]]
    remittances: list[dict[str, Any]]

    def scenario_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for inv in self.invoices:
            out[inv["scenario"]] = out.get(inv["scenario"], 0) + 1
        return dict(sorted(out.items()))


class ScenarioGenerator:
    def __init__(self, seed: int = 42, *, as_of: date | None = None) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.fake = Faker()
        self.fake.seed_instance(seed)
        self.as_of = as_of or date.today()
        self.catalog = Catalog.build(self.rng)
        self._inv_seq = 100000
        self._po_seq = 50000
        self._rem_seq = 9000

    # ---------- helpers ----------
    def _pick_scenario(self) -> Scenario:
        if self.rng.random() >= OVERDUE_SHARE:
            return Scenario.CLEAN
        r = self.rng.random()
        acc = 0.0
        for s, w in OVERDUE_MIX:
            acc += w
            if r <= acc:
                return s
        return Scenario.CASH_FLOW

    def _contacts(self, domain: str) -> list[dict[str, Any]]:
        n_active = self.rng.choice([1, 1, 2])
        contacts = []
        for i in range(n_active):
            first, last = self.fake.first_name(), self.fake.last_name()
            contacts.append(
                {
                    "name": f"{first} {last}",
                    "email": f"{first}.{last}@{domain}".lower(),
                    "role": "AP" if i == 0 else self.rng.choice(["AP", "Purchasing"]),
                    "active": True,
                    "phone": self.fake.phone_number(),
                }
            )
        # A former AP contact who has since left (used by WRONG_CONTACT)
        first, last = self.fake.first_name(), self.fake.last_name()
        contacts.append(
            {
                "name": f"{first} {last}",
                "email": f"{first}.{last}@{domain}".lower(),
                "role": "AP",
                "active": False,
                "phone": None,
            }
        )
        return contacts

    def _customer(self, idx: int) -> tuple[dict[str, Any], dict[str, Any]]:
        company = self.fake.company()
        domain = "".join(ch for ch in company.lower() if ch.isalnum())[:14] + ".example.com"
        risk = money(self.rng.betavariate(2, 5))
        terms = self.rng.choice([30, 30, 30, 45, 60])
        customer = {
            "customer_ref": f"CUST-{idx:04d}",
            "name": company,
            "payment_terms_days": terms,
            "requires_po": self.rng.random() < 0.55,
            "credit_hold": self.rng.random() < 0.04,
            "credit_risk_score": risk,
            "contacts": self._contacts(domain),
            "billing_address": self.fake.address().replace("\n", ", "),
        }
        skus = self.rng.sample(sorted(self.catalog.items), k=self.rng.randint(6, 14))
        discount = money(self.rng.uniform(0, 0.15))
        price_list = {sku: str(money(self.catalog.items[sku][1] * (1 - discount))) for sku in skus}
        contract = {
            "customer_ref": customer["customer_ref"],
            "effective_from": self.as_of - timedelta(days=self.rng.randint(120, 900)),
            "payment_terms_days": terms,
            "price_list": price_list,
            "freight_billable": self.rng.random() < 0.6,
            "early_pay_discount_pct": Decimal(self.rng.choice(["0", "0", "1", "2"])),
            "max_discount_pct": Decimal(self.rng.choice(["2", "2", "3", "5"])),
            "dispute_window_days": self.rng.choice([30, 30, 45, 60]),
        }
        return customer, contract

    def _po(
        self, customer: dict[str, Any], contract: dict[str, Any], issued: date
    ) -> dict[str, Any]:
        self._po_seq += 1
        skus = self.rng.sample(sorted(contract["price_list"]), k=self.rng.randint(1, 5))
        lines = []
        total = Decimal("0")
        for i, sku in enumerate(skus, 1):
            qty = Decimal(self.rng.choice([1, 2, 4, 5, 10, 12, 20, 25, 50, 100]))
            price = Decimal(contract["price_list"][sku])
            lines.append(
                {
                    "line_no": i,
                    "sku": sku,
                    "description": self.catalog.items[sku][0],
                    "qty": str(qty),
                    "unit_price": str(price),
                }
            )
            total += qty * price
        return {
            "po_number": f"PO-{self._po_seq}",
            "customer_ref": customer["customer_ref"],
            "status": "OPEN",
            "issued_at": issued,
            "currency": "USD",
            "lines": lines,
            "total": money(total),
        }

    @staticmethod
    def _invoice_lines_from_po(po: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for line in po["lines"]:
            qty, price = Decimal(line["qty"]), Decimal(line["unit_price"])
            out.append({**line, "amount": str(money(qty * price))})
        return out

    @staticmethod
    def _totals(lines: list[dict[str, Any]], freight: Decimal) -> tuple[Decimal, Decimal, Decimal]:
        subtotal = money(sum(Decimal(line["amount"]) for line in lines))
        tax = money(subtotal * Decimal("0.07"))
        total = money(subtotal + freight + tax)
        return subtotal, tax, total

    def _delivery(
        self, invoice_ref: str, lines: list[dict[str, Any]], shipped: date
    ) -> dict[str, Any]:
        return {
            "invoice_ref": invoice_ref,
            "delivered_at": datetime.combine(shipped, datetime.min.time(), tzinfo=UTC)
            + timedelta(hours=self.rng.randint(8, 17)),
            "signed_by": self.fake.name(),
            "carrier": self.rng.choice(["UPS", "FedEx", "XPO", "Old Dominion"]),
            "tracking_number": self.fake.bothify("1Z###??########"),
            "lines": [
                {"line_no": line["line_no"], "sku": line["sku"], "qty_delivered": line["qty"]}
                for line in lines
            ],
        }

    # ---------- main ----------
    def generate(self, n_customers: int = 60, n_invoices: int = 400) -> Dataset:
        customers: list[dict[str, Any]] = []
        contracts: list[dict[str, Any]] = []
        pos: list[dict[str, Any]] = []
        invoices: list[dict[str, Any]] = []
        deliveries: list[dict[str, Any]] = []
        remittances: list[dict[str, Any]] = []

        for i in range(1, n_customers + 1):
            c, k = self._customer(i)
            customers.append(c)
            contracts.append(k)

        # Weighted customer selection so some customers have many invoices (realistic long tail)
        weights = [self.rng.paretovariate(1.5) for _ in customers]

        for _ in range(n_invoices):
            ci = self.rng.choices(range(len(customers)), weights=weights, k=1)[0]
            customer, contract = customers[ci], contracts[ci]
            scenario = self._pick_scenario()
            self._inv_seq += 1
            invoice_ref = f"INV-{self._inv_seq}"
            terms = customer["payment_terms_days"]

            if scenario is Scenario.CLEAN:
                # Either paid, or open and not yet overdue
                issue = self.as_of - timedelta(days=self.rng.randint(1, 120))
            else:
                overdue_days = self.rng.randint(8, 75)
                issue = self.as_of - timedelta(days=terms + overdue_days)
            due = issue + timedelta(days=terms)
            po = self._po(customer, contract, issue - timedelta(days=self.rng.randint(3, 20)))
            pos.append(po)

            lines = self._invoice_lines_from_po(po)
            freight = money(self.rng.uniform(15, 220)) if self.rng.random() < 0.7 else Decimal("0")
            po_number: str | None = po["po_number"]
            billed_to = customer["contacts"][0]["email"]
            status = "OPEN"
            amount_paid = Decimal("0")
            gt: dict[str, Any] = {
                "root_cause": ROOT_CAUSE_FOR[scenario],
                "expected_credit_memo": "0.00",
                "expected_final_state": "RESOLVED",
                "rebill_required": False,
                "notes": "",
            }

            if scenario is Scenario.DISPUTE_PRICING:
                n_bad = min(len(lines), self.rng.choice([1, 1, 2]))
                credit = Decimal("0")
                for line in self.rng.sample(lines, n_bad):
                    bump = Decimal(str(round(self.rng.uniform(1.05, 1.22), 3)))
                    new_price = money(Decimal(line["unit_price"]) * bump)
                    credit += (new_price - Decimal(line["unit_price"])) * Decimal(line["qty"])
                    line["unit_price"] = str(new_price)
                    line["amount"] = str(money(new_price * Decimal(line["qty"])))
                credit = money(credit)
                gt["expected_credit_memo"] = str(money(credit * Decimal("1.07")))  # incl. tax
                gt["notes"] = f"{n_bad} line(s) invoiced above contract price"

            elif scenario is Scenario.DISPUTE_QUANTITY:
                line = self.rng.choice(lines)
                inv_qty = Decimal(line["qty"])
                delivered = money(inv_qty * Decimal(str(self.rng.choice([0.5, 0.6, 0.75, 0.8]))))
                delivered = min(delivered.quantize(Decimal("1")), inv_qty - 1)  # strictly short
                credit = money((inv_qty - delivered) * Decimal(line["unit_price"]))
                gt["expected_credit_memo"] = str(money(credit * Decimal("1.07")))
                gt["rebill_required"] = self.rng.random() < 0.5
                gt["notes"] = f"line {line['line_no']} delivered {delivered} of {inv_qty}"
                d = self._delivery(invoice_ref, lines, issue - timedelta(days=1))
                for dl in d["lines"]:
                    if dl["line_no"] == line["line_no"]:
                        dl["qty_delivered"] = str(delivered)
                deliveries.append(d)

            elif scenario is Scenario.MISSING_PO:
                customer["requires_po"] = True
                po_number = (
                    None if self.rng.random() < 0.6 else f"REF-{self.rng.randint(1000, 9999)}"
                )
                gt["notes"] = "customer requires a valid PO on every invoice"
                gt["expected_final_state"] = "RESOLVED"

            elif scenario is Scenario.WRONG_CONTACT:
                billed_to = next(c["email"] for c in customer["contacts"] if not c["active"])
                gt["notes"] = "invoice sent to a contact who left the company"

            elif scenario is Scenario.SHORT_PAY:
                if freight == 0:
                    freight = money(self.rng.uniform(40, 200))
                _, _, full_total = self._totals(lines, freight)
                amount_paid = money(full_total - freight)
                status = "PARTIALLY_PAID"
                self._rem_seq += 1
                remittances.append(
                    {
                        "remittance_id": f"REM-{self._rem_seq}",
                        "customer_ref": customer["customer_ref"],
                        "invoice_ref": invoice_ref,
                        "amount": amount_paid,
                        "received_at": datetime.combine(due, datetime.min.time(), tzinfo=UTC)
                        - timedelta(days=self.rng.randint(0, 5)),
                        "method": self.rng.choice(["ACH", "WIRE", "CHECK"]),
                        "memo": f"{invoice_ref} less freight - not per contract",
                    }
                )
                if contract["freight_billable"]:
                    gt["expected_credit_memo"] = "0.00"
                    gt["notes"] = "freight IS billable per contract; collect the balance"
                else:
                    gt["expected_credit_memo"] = str(freight)
                    gt["notes"] = "freight NOT billable per contract; credit the deduction"

            elif scenario is Scenario.DUPLICATE_INVOICE:
                # An earlier invoice for the same PO exists and was paid.
                self._inv_seq += 1
                orig_ref = f"INV-{self._inv_seq}"
                o_sub, o_tax, o_total = self._totals(lines, freight)
                orig_issue = issue - timedelta(days=self.rng.randint(5, 20))
                invoices.append(
                    {
                        "invoice_ref": orig_ref,
                        "customer_ref": customer["customer_ref"],
                        "po_number": po["po_number"],
                        "status": "PAID",
                        "issue_date": orig_issue,
                        "due_date": orig_issue + timedelta(days=terms),
                        "currency": "USD",
                        "subtotal": o_sub,
                        "freight": freight,
                        "tax": o_tax,
                        "total": o_total,
                        "amount_paid": o_total,
                        "amount_open": Decimal("0.00"),
                        "billed_to_email": billed_to,
                        "lines": lines,
                        "scenario": Scenario.CLEAN.value,
                        "ground_truth": {
                            "root_cause": "NONE",
                            "notes": f"original of {invoice_ref}",
                        },
                    }
                )
                deliveries.append(self._delivery(orig_ref, lines, orig_issue - timedelta(days=1)))
                self._rem_seq += 1
                remittances.append(
                    {
                        "remittance_id": f"REM-{self._rem_seq}",
                        "customer_ref": customer["customer_ref"],
                        "invoice_ref": orig_ref,
                        "amount": o_total,
                        "received_at": datetime.combine(
                            orig_issue + timedelta(days=terms - 2), datetime.min.time(), tzinfo=UTC
                        ),
                        "method": "ACH",
                        "memo": f"payment {orig_ref} {po['po_number']}",
                    }
                )
                gt["notes"] = f"duplicate of {orig_ref}; void in full"
                gt["duplicate_of"] = orig_ref

            elif scenario is Scenario.CASH_FLOW:
                customer["credit_risk_score"] = money(
                    max(Decimal(str(customer["credit_risk_score"])), Decimal("0.55"))
                )
                gt["expected_final_state"] = "RESOLVED"
                gt["acceptable_offers"] = [
                    {"type": "PAYMENT_PLAN", "max_installments": 3},
                    {"type": "EXTENSION", "max_days": 30},
                ]
                gt["notes"] = "no dispute; customer is slow-paying; offer plan within policy"

            subtotal, tax, total = self._totals(lines, freight)
            if scenario is Scenario.DUPLICATE_INVOICE:
                gt["expected_credit_memo"] = str(total)
            if scenario is Scenario.CLEAN and self.rng.random() < 0.7 and due < self.as_of:
                status, amount_paid = "PAID", total
            elif scenario is Scenario.CLEAN and due < self.as_of:
                # a clean invoice that is overdue is really CASH_FLOW; keep the dataset honest
                scenario = Scenario.CASH_FLOW
                gt = {**gt, "root_cause": "CASH_FLOW", "notes": "slow payer, no dispute"}
                customer["credit_risk_score"] = money(
                    max(Decimal(str(customer["credit_risk_score"])), Decimal("0.55"))
                )
            amount_open = money(total - amount_paid)

            invoices.append(
                {
                    "invoice_ref": invoice_ref,
                    "customer_ref": customer["customer_ref"],
                    "po_number": po_number,
                    "status": status,
                    "issue_date": issue,
                    "due_date": due,
                    "currency": "USD",
                    "subtotal": subtotal,
                    "freight": freight,
                    "tax": tax,
                    "total": total,
                    "amount_paid": money(amount_paid),
                    "amount_open": amount_open,
                    "billed_to_email": billed_to,
                    "lines": lines,
                    "scenario": scenario.value,
                    "ground_truth": gt,
                }
            )
            if scenario is not Scenario.DISPUTE_QUANTITY and status != "VOID":
                deliveries.append(self._delivery(invoice_ref, lines, issue - timedelta(days=1)))
            if status == "PAID":
                self._rem_seq += 1
                remittances.append(
                    {
                        "remittance_id": f"REM-{self._rem_seq}",
                        "customer_ref": customer["customer_ref"],
                        "invoice_ref": invoice_ref,
                        "amount": total,
                        "received_at": datetime.combine(due, datetime.min.time(), tzinfo=UTC)
                        - timedelta(days=self.rng.randint(0, 10)),
                        "method": "ACH",
                        "memo": f"payment {invoice_ref}",
                    }
                )

        return Dataset(
            seed=self.seed,
            as_of=self.as_of,
            customers=customers,
            contracts=contracts,
            purchase_orders=pos,
            invoices=invoices,
            deliveries=deliveries,
            remittances=remittances,
        )
