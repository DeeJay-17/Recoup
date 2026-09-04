"""Rule-based stand-ins for the LLM, one per agent. They drive the same tool loop and produce
the same structured outputs, so the whole pipeline runs without a provider key and evals get a
baseline to beat. Registered on import."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from recoup_llm import AssistantTurn, HeuristicLLM, Message, ToolCall, ToolSchema
from recoup_llm.heuristic import last_tool_results

_ID = 0


def _call(name: str, **args: Any) -> ToolCall:
    global _ID
    _ID += 1
    return ToolCall(id=f"h{_ID}", name=name, args=args)


def _state(messages: list[Message]) -> dict[str, Any]:
    for m in messages:
        if m.role == "user":
            mm = re.search(r"<state>\n(.*)\n</state>", m.content, re.S)
            if mm:
                return dict(json.loads(mm.group(1)))
    return {}


def _has(tools: list[ToolSchema], name: str) -> bool:
    return any(t.name == name for t in tools)


def _submit(name: str, **args: Any) -> AssistantTurn:
    return AssistantTurn(tool_calls=[_call(name, **args)])


# ---------- supervisor ----------
DISPUTES = {"DISPUTE_PRICING", "DISPUTE_QUANTITY", "DUPLICATE_INVOICE", "SHORT_PAY"}
FOLLOWUP_HOURS = [72, 168, 336]


def supervisor(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    s = _state(messages)
    runs = s.get("specialist_runs", {})
    budget = s.get("budget", {})
    inv = s.get("investigation") or {}
    cause = inv.get("confirmed_cause") or (s.get("triage") or {}).get(
        "root_cause_hypotheses", [{}]
    )[0].get("cause")
    actions = s.get("actions", [])
    pending = [a for a in actions if a["status"] == "PENDING"]
    executed = [a for a in actions if a["status"] == "EXECUTED"]
    rejected = [a for a in actions if a["status"] == "REJECTED"]
    denied = [a for a in actions if a["status"] == "DENIED"]
    intent = (s.get("last_customer_intent") or {}).get("intent")
    signals = s.get("signals", [])
    last_signal = signals[-1] if signals else {}
    email_sent = any(a["action_type"] == "SEND_EMAIL" for a in executed)
    waits = int(s.get("waits") or 0)
    plan = [
        {
            "goal": "Classify root cause",
            "status": "done" if s.get("triage") else "todo",
            "owner": "Triage",
        },
        {
            "goal": "Confirm with evidence",
            "status": "done" if inv else "todo",
            "owner": "Investigator",
        },
        {
            "goal": "Resolve with the customer",
            "status": "doing" if inv else "todo",
            "owner": "Reconciler/Negotiator/Communicator",
        },
    ]

    def decide(
        nxt: str, why: str, conf: float = 0.85, reason: str | None = None, hours: int | None = None
    ) -> AssistantTurn:
        return _submit(
            "submit_decision",
            reasoning_summary=why[:600],
            updated_plan=plan,
            next=nxt,
            confidence=conf,
            escalation_reason=reason,
            wait_timeout_hours=hours,
        )

    if budget.get("used_pct", 0) >= 85:
        return decide(
            "ESCALATED",
            "Budget nearly exhausted; escalating with findings.",
            0.6,
            "STEP_OR_TOKEN_BUDGET",
        )
    if pending:
        return decide(
            "AWAIT_APPROVAL",
            f"{len(pending)} action(s) pending human approval: {[a['action_type'] for a in pending]}.",
            0.95,
        )
    if rejected and not any(a["status"] == "PENDING" for a in actions):
        return decide(
            "ESCALATED",
            f"A human rejected {rejected[-1]['action_type']}; handing over.",
            0.8,
            f"human rejected {rejected[-1]['action_type']}",
        )
    if denied:
        return decide(
            "ESCALATED",
            f"Policy denied {denied[-1]['action_type']}; no compliant path.",
            0.8,
            f"policy denied {denied[-1]['action_type']}",
        )
    if not s.get("triage") and runs.get("Triage", 0) < 1:
        return decide("Triage", "No triage yet; classify the root cause first.", 0.9)
    if not inv and runs.get("Investigator", 0) < 1:
        return decide("Investigator", "Triage done; gather evidence for the top hypothesis.", 0.85)
    # ---- customer replied ----
    if intent and last_signal.get("type") == "customer_reply" and intent != "PROCESSED":
        if intent in ("CONFIRMS_PAYMENT", "ACCEPTS_OFFER"):
            plan[2]["status"] = "done"
            return decide(
                "RESOLVED", f"Customer {intent.lower().replace('_', ' ')}; outcome secured.", 0.85
            )
        if intent == "PROVIDES_PO":
            if executed and any(
                a["action_type"] == "SEND_EMAIL" and "resend" in (a.get("summary") or "").lower()
                for a in executed
            ):
                return decide("RESOLVED", "PO received and corrected invoice re-sent.", 0.8)
            return decide(
                "Communicator", "Customer provided the PO; re-send the corrected invoice.", 0.85
            )
        if intent == "REDIRECTS_CONTACT":
            return decide(
                "Communicator", "Customer pointed to a different AP contact; re-send there.", 0.8
            )
        if intent in ("DISPUTES", "REJECTS_OFFER"):
            return decide(
                "ESCALATED",
                f"Customer response: {intent}; a human should take over.",
                0.8,
                f"customer {intent}",
            )
        if intent in ("REQUESTS_INFO", "UNCLEAR", "OUT_OF_OFFICE"):
            return decide(
                "ESCALATED",
                f"Customer reply needs human judgement ({intent}).",
                0.6,
                f"customer {intent}",
            )
    if last_signal.get("type") == "timeout":
        if waits >= len(FOLLOWUP_HOURS):
            return decide(
                "ESCALATED",
                "Follow-up cadence exhausted without a reply.",
                0.7,
                "no customer reply after follow-ups",
            )
        return decide("Communicator", "No reply in time; send the next follow-up.", 0.75)
    # ---- resolution path by cause ----
    credit_due = inv.get("proposed_credit_memo") not in (None, "0", "0.00", 0)
    if (
        cause in DISPUTES
        and credit_due
        and not s.get("reconciliation")
        and runs.get("Reconciler", 0) < 1
    ):
        return decide(
            "Reconciler",
            f"{cause} confirmed with credit {inv.get('proposed_credit_memo')}; compute and propose the memo.",
            0.9,
        )
    if cause == "CASH_FLOW" and not s.get("negotiation") and runs.get("Negotiator", 0) < 1:
        return decide("Negotiator", "No dispute; slow payer. Design a plan within policy.", 0.85)
    if not email_sent and runs.get("Communicator", 0) < 1:
        why = {
            "MISSING_PO": "Ask AP for the PO number.",
            "WRONG_CONTACT": "Re-send the invoice to the active AP contact.",
            "CASH_FLOW": "Send the plan offer.",
            "SHORT_PAY": "Follow up on the short payment.",
        }.get(cause, "Notify the customer of the resolution.")
        return decide("Communicator", why, 0.85)
    if email_sent and last_signal.get("type") != "customer_reply":
        hours = FOLLOWUP_HOURS[min(waits, len(FOLLOWUP_HOURS) - 1)]
        return decide(
            "WAIT_FOR_CUSTOMER", "Email sent; wait for the customer's reply.", 0.9, hours=hours
        )
    return decide(
        "ESCALATED",
        "No further autonomous step applies; handing over with the evidence.",
        0.6,
        "playbook exhausted",
    )


# ---------- triage ----------
def triage(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    s = _state(messages)
    ref = (s.get("case", {}).get("invoice_refs") or [""])[0]
    cust = s.get("case", {}).get("customer_ref")
    seen = last_tool_results(messages)
    if "get_invoice" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call("get_invoice", invoice_ref=ref),
                _call("get_customer", customer_ref=cust),
            ]
        )
    if "get_remittances" not in seen:
        calls = [_call("get_remittances", customer_ref=cust, invoice_ref=ref)]
        if _has(tools, "get_email_thread"):
            calls.append(_call("get_email_thread", limit=5))
        return AssistantTurn(tool_calls=calls)
    inv, c = seen.get("get_invoice") or {}, seen.get("get_customer") or {}
    rems = (seen.get("get_remittances") or {}).get("items") or []
    hyps: list[dict[str, Any]] = []
    evidence: list[str] = []
    if isinstance(inv, dict) and isinstance(c, dict) and inv:
        po = inv.get("po_number")
        if c.get("requires_po") and (not po or not str(po).startswith("PO-")):
            hyps.append(
                {
                    "cause": "MISSING_PO",
                    "confidence": 0.85,
                    "evidence_refs": [f"invoice.po_number={po}", "customer.requires_po=true"],
                }
            )
        inactive = {x["email"] for x in c.get("contacts", []) if not x.get("active")}
        if inv.get("billed_to_email") in inactive:
            hyps.append(
                {
                    "cause": "WRONG_CONTACT",
                    "confidence": 0.85,
                    "evidence_refs": [f"contact:{inv.get('billed_to_email')} inactive"],
                }
            )
        if inv.get("status") == "PARTIALLY_PAID" or any(
            "less" in (r.get("memo") or "").lower() for r in rems
        ):
            hyps.append(
                {
                    "cause": "SHORT_PAY",
                    "confidence": 0.8,
                    "evidence_refs": [f"invoice.amount_paid={inv.get('amount_paid')}"]
                    + [f"remittance:{r.get('remittance_id')} memo" for r in rems[:1]],
                }
            )
        if not hyps:
            risk = float(c.get("credit_risk_score") or 0)
            if risk >= 0.55:
                hyps.append(
                    {
                        "cause": "CASH_FLOW",
                        "confidence": 0.5,
                        "evidence_refs": [f"customer.credit_risk_score={risk}"],
                    }
                )
                hyps.append(
                    {
                        "cause": "DISPUTE_PRICING",
                        "confidence": 0.3,
                        "evidence_refs": ["needs reconciliation"],
                    }
                )
            else:
                hyps.append(
                    {
                        "cause": "DISPUTE_PRICING",
                        "confidence": 0.45,
                        "evidence_refs": ["needs reconciliation"],
                    }
                )
                hyps.append(
                    {
                        "cause": "DUPLICATE_INVOICE",
                        "confidence": 0.25,
                        "evidence_refs": ["needs duplicate check"],
                    }
                )
    if not hyps:
        hyps = [
            {"cause": "UNKNOWN", "confidence": 0.3, "evidence_refs": ["tool results unavailable"]}
        ]
    top = hyps[0]["cause"]
    path = {"MISSING_PO": "outreach", "WRONG_CONTACT": "outreach", "CASH_FLOW": "negotiate"}.get(
        top, "investigate"
    )
    amount = Decimal(str(inv.get("amount_open", "0"))) if isinstance(inv, dict) else Decimal("0")
    days = int(s.get("case", {}).get("days_overdue") or 0)
    priority = (
        1
        if amount > 25000 or days > 60
        else 2
        if amount > 5000 or days > 30
        else 3
        if amount > 1000
        else 4
    )
    return _submit(
        "submit_triage",
        root_cause_hypotheses=hyps[:3],
        priority=priority,
        recommended_path=path,
        summary=f"Top hypothesis {top} from invoice/customer/remittance facts; evidence: {', '.join(evidence + hyps[0]['evidence_refs'])}.",
    )


# ---------- investigator ----------
def investigator(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    s = _state(messages)
    ref = (s.get("case", {}).get("invoice_refs") or [""])[0]
    seen = last_tool_results(messages)
    if "reconcile_lines" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call("reconcile_lines", invoice_ref=ref),
                _call("check_duplicate_invoice", invoice_ref=ref),
            ]
        )
    rec = seen.get("reconcile_lines") or {}
    dup = seen.get("check_duplicate_invoice") or {}
    triage = s.get("triage") or {}
    top = (triage.get("root_cause_hypotheses") or [{}])[0].get("cause")
    evidence: list[dict[str, str]] = []
    gaps: list[str] = []
    cause, conf, credit, rebill = "UNKNOWN", 0.4, None, False
    if isinstance(rec, dict) and "discrepancies" in rec:
        kinds = sorted({d["kind"] for d in rec["discrepancies"]})
        for d in rec["discrepancies"][:6]:
            evidence.append(
                {
                    "source": "reconcile_lines",
                    "ref": f"{ref} line {d.get('line_no')} {d.get('sku')}",
                    "finding": d["note"][:280],
                }
            )
        if not rec.get("po_found"):
            gaps.append("purchase order not found in ERP")
        if not rec.get("delivery_found"):
            gaps.append("no delivery proof on file")
        if isinstance(dup, dict) and dup.get("is_duplicate"):
            cause, conf = "DUPLICATE_INVOICE", 0.92
            credit = Decimal(str(s.get("case", {}).get("amount_open") or "0"))
            evidence.append(
                {
                    "source": "check_duplicate_invoice",
                    "ref": dup["candidates"][0]["invoice_ref"],
                    "finding": dup["explanation"][:280],
                }
            )
        elif any(k.startswith("PRICE") for k in kinds):
            cause, conf = "DISPUTE_PRICING", 0.9
            credit = Decimal(str(rec["proposed_credit_memo"]))
        elif any(k.startswith("QTY") for k in kinds):
            cause, conf = "DISPUTE_QUANTITY", 0.88
            credit, rebill = (
                Decimal(str(rec["proposed_credit_memo"])),
                bool(rec.get("rebill_required")),
            )
        elif "FREIGHT_NOT_BILLABLE" in kinds and top == "SHORT_PAY":
            cause, conf = "SHORT_PAY", 0.9
            credit = Decimal(str(rec["proposed_credit_memo"]))
        elif top in ("MISSING_PO", "WRONG_CONTACT", "SHORT_PAY", "CASH_FLOW"):
            cause, conf = top, 0.8 if not kinds else 0.6
            evidence.append(
                {"source": "reconcile_lines", "ref": ref, "finding": rec.get("summary", "")[:280]}
            )
        else:
            cause, conf = "CASH_FLOW", 0.6
            evidence.append(
                {
                    "source": "reconcile_lines",
                    "ref": ref,
                    "finding": "Invoice matches PO, delivery and contract; nothing owed back.",
                }
            )
    else:
        gaps.append("reconciliation unavailable")
    return _submit(
        "submit_investigation",
        evidence=evidence,
        confirmed_cause=cause,
        confidence=conf,
        contradicts_triage=bool(top and top != cause),
        gaps=gaps,
        proposed_credit_memo=str(credit) if credit else None,
        rebill_required=rebill,
        summary=f"Reconciliation {rec.get('summary', 'n/a') if isinstance(rec, dict) else 'n/a'}; duplicate check: {dup.get('explanation', 'n/a') if isinstance(dup, dict) else 'n/a'}.",
    )


# ---------- reconciler ----------
def reconciler(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    s = _state(messages)
    ref = (s.get("case", {}).get("invoice_refs") or [""])[0]
    seen = last_tool_results(messages)
    if "reconcile_lines" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call("reconcile_lines", invoice_ref=ref),
                _call("check_duplicate_invoice", invoice_ref=ref),
            ]
        )
    rec, dup = seen.get("reconcile_lines") or {}, seen.get("check_duplicate_invoice") or {}
    kinds = sorted({d["kind"] for d in rec.get("discrepancies", [])} - {"LINE_NOT_ON_PO"})
    is_dup = bool(isinstance(dup, dict) and dup.get("is_duplicate"))
    if "calculate_credit_memo" not in seen and kinds and not is_dup:
        return AssistantTurn(
            tool_calls=[_call("calculate_credit_memo", invoice_ref=ref, include_kinds=kinds)]
        )
    calc = seen.get("calculate_credit_memo") or {}
    amount = (
        Decimal(str(s.get("case", {}).get("amount_open") or "0"))
        if is_dup
        else Decimal(str(calc.get("credit_memo_amount") or rec.get("proposed_credit_memo") or "0"))
    )
    lines = [
        {
            "kind": d["kind"],
            "line_no": d.get("line_no"),
            "sku": d.get("sku"),
            "credit_before_tax": str(d["credit_before_tax"]),
            "note": d["note"][:280],
        }
        for d in rec.get("discrepancies", [])[:10]
    ]
    if amount <= 0:
        return _submit(
            "submit_reconciliation",
            line_discrepancies=lines,
            proposed_credit_memo="0",
            rebill_required=False,
            rationale="Reconciliation shows nothing owed back to the customer.",
            nothing_owed=True,
        )
    reason = (
        "DUPLICATE"
        if is_dup
        else "FREIGHT"
        if kinds == ["FREIGHT_NOT_BILLABLE"]
        else "QUANTITY"
        if any(k.startswith("QTY") for k in kinds)
        else "PRICING"
    )
    if "propose_action" not in seen:
        memo = f"Credit for {reason.lower()} discrepancy on {ref}: {', '.join(kinds) or 'duplicate of ' + str(dup.get('candidates', [{}])[0].get('invoice_ref'))}"
        return AssistantTurn(
            tool_calls=[
                _call(
                    "propose_action",
                    action_type="CREATE_CREDIT_MEMO",
                    payload={
                        "invoice_ref": ref,
                        "amount": str(amount),
                        "reason_code": reason,
                        "memo": memo[:480],
                    },
                    rationale=f"Deterministic reconciliation: {rec.get('summary', '')[:300]}",
                    evidence_refs=[f"po:{rec.get('po_number')}", f"invoice:{ref}"],
                )
            ]
        )
    prop = seen.get("propose_action") or {}
    return _submit(
        "submit_reconciliation",
        line_discrepancies=lines,
        proposed_credit_memo=str(amount),
        rebill_required=bool(rec.get("rebill_required")),
        void_duplicate=is_dup,
        rationale=f"{rec.get('summary', '')[:300]} Proposed credit memo {amount} ({reason}); policy {prop.get('policy_decision')}.",
        action_id=prop.get("action_id"),
        nothing_owed=False,
    )


# ---------- negotiator ----------
def negotiator(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    from datetime import date, timedelta

    s = _state(messages)
    refs = s.get("case", {}).get("invoice_refs") or []
    cust = s.get("case", {}).get("customer_ref")
    seen = last_tool_results(messages)
    if "get_customer" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call("get_customer", customer_ref=cust),
                _call("get_contract_terms", customer_ref=cust),
            ]
        )
    c = seen.get("get_customer") or {}
    if c.get("credit_hold"):
        return _submit(
            "submit_negotiation",
            offer={"type": "NONE", "summary": "Customer on credit hold; no plan offered."},
            fallback_offers=[],
            walk_away_condition="Credit hold: escalate.",
            rationale="Policy forbids payment plans on credit hold.",
        )
    first_due = (date.today() + timedelta(days=14)).isoformat()
    if "simulate_payment_plan" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call(
                    "simulate_payment_plan",
                    invoice_refs=refs,
                    installments=3,
                    first_due=first_due,
                    discount_pct="0",
                ),
                _call(
                    "evaluate_policy",
                    action_type="PAYMENT_PLAN",
                    action={"installments": 3, "discount_pct": 0, "extension_days": 60},
                ),
            ]
        )
    sim = seen.get("simulate_payment_plan") or {}
    pol = seen.get("evaluate_policy") or {}
    inst = 3 if pol.get("decision") in ("ALLOW", "REQUIRE_APPROVAL") else 2
    if "propose_action" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call(
                    "propose_action",
                    action_type="PAYMENT_PLAN",
                    payload={
                        "invoice_refs": refs,
                        "installments": inst,
                        "first_due": first_due,
                        "discount_pct": "0",
                    },
                    rationale=f"Slow payer (risk {c.get('credit_risk_score')}); {inst} monthly installments, no discount, keeps us within policy.",
                    evidence_refs=[f"customer:{cust}"],
                )
            ]
        )
    prop = seen.get("propose_action") or {}
    offer = {
        "type": "PAYMENT_PLAN",
        "installments": inst,
        "first_due": first_due,
        "discount_pct": "0",
        "extension_days": sim.get("extension_days", 60),
        "plan_total": sim.get("plan_total"),
        "summary": f"{inst} monthly installments starting {first_due}",
    }
    return _submit(
        "submit_negotiation",
        offer=offer,
        fallback_offers=[
            {"type": "EXTENSION", "extension_days": 30, "summary": "30-day extension, full amount"}
        ],
        walk_away_condition="Customer asks for more than 3 installments or any discount above 2%.",
        rationale=f"Plan total {sim.get('plan_total')}; policy {prop.get('policy_decision')}.",
        action_id=prop.get("action_id"),
    )


# ---------- communicator ----------
def communicator(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    s = _state(messages)
    case = s.get("case", {})
    ref = (case.get("invoice_refs") or [""])[0]
    seen = last_tool_results(messages)
    if "get_contacts" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call("get_contacts", customer_ref=case.get("customer_ref")),
                _call("get_invoice", invoice_ref=ref),
                _call("get_email_thread", limit=5),
            ]
        )
    contacts, inv, thread = (
        seen.get("get_contacts") or {},
        seen.get("get_invoice") or {},
        seen.get("get_email_thread") or {},
    )
    intent = s.get("last_customer_intent") or {}
    to = (
        (intent.get("new_contact_email") if intent.get("intent") == "REDIRECTS_CONTACT" else None)
        or (contacts.get("primary_ap") or {}).get("email")
        or "ap@unknown.example"
    )
    name = (contacts.get("primary_ap") or {}).get("name") or "there"
    inv_ = s.get("investigation") or {}
    cause = inv_.get("confirmed_cause")
    executed = {a["action_type"]: a for a in s.get("actions", []) if a["status"] == "EXECUTED"}
    signals = s.get("signals", [])
    last_signal = signals[-1] if signals else {}
    common = {
        "contact_name": name,
        "invoice_ref": ref,
        "amount": str(inv.get("amount_open", case.get("amount_open"))),
        "currency": inv.get("currency", "USD"),
        "sender_name": "Ava Chen",
        "company_name": "Acme Industrial Supply",
    }
    if last_signal.get("type") == "timeout":
        template, variables = (
            "payment_reminder",
            {**common, "days_overdue": case.get("days_overdue"), "due_date": inv.get("due_date")},
        )
    elif (
        intent.get("intent") == "PROVIDES_PO"
        or cause == "WRONG_CONTACT"
        or intent.get("intent") == "REDIRECTS_CONTACT"
    ):
        template, variables = "resend_invoice", {**common, "due_date": inv.get("due_date")}
    elif cause == "MISSING_PO":
        template, variables = "po_request", {**common, "issue_date": inv.get("issue_date")}
    elif "CREATE_CREDIT_MEMO" in executed:
        res = (executed["CREATE_CREDIT_MEMO"].get("execution_result") or {}).get("result") or {}
        rec = s.get("reconciliation") or {}
        template, variables = (
            "dispute_resolution",
            {
                **common,
                "po_number": inv.get("po_number") or "n/a",
                "finding": (rec.get("rationale") or "pricing discrepancy")[:200],
                "credit_memo_ref": res.get("credit_memo_ref", "pending"),
                "credit_amount": res.get("amount", rec.get("proposed_credit_memo")),
                "remaining_amount": str(inv.get("amount_open")),
            },
        )
    elif "PAYMENT_PLAN" in executed or s.get("negotiation"):
        offer = (s.get("negotiation") or {}).get("offer") or {}
        template, variables = (
            "payment_plan_offer",
            {**common, "plan_summary": offer.get("summary", "monthly installments")},
        )
    elif cause == "SHORT_PAY":
        template, variables = (
            "short_pay_followup",
            {
                **common,
                "paid_amount": str(inv.get("amount_paid")),
                "deduction_amount": str(inv.get("amount_open")),
                "deduction_memo": "freight",
                "contract_date": "our agreement",
                "contract_explanation": "freight charges are billable",
            },
        )
    else:
        template, variables = (
            "payment_reminder",
            {**common, "days_overdue": case.get("days_overdue"), "due_date": inv.get("due_date")},
        )
    if "render_template" not in seen:
        return AssistantTurn(
            tool_calls=[_call("render_template", template=template, variables=variables)]
        )
    r = seen.get("render_template") or {}
    if "classify_tone" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call(
                    "classify_tone", subject=r.get("subject", ""), body_text=r.get("body_text", "")
                )
            ]
        )
    tone = (seen.get("classify_tone") or {}).get("tone_score", 0.9)
    if "propose_action" not in seen:
        return AssistantTurn(
            tool_calls=[
                _call(
                    "propose_action",
                    action_type="SEND_EMAIL",
                    payload={
                        "to": [to],
                        "template": template,
                        "variables": variables,
                        "invoice_refs": [ref],
                        "in_reply_to": thread.get("last_inbound_message_id"),
                    },
                    rationale=f"{template} to the active AP contact; tone {tone}.",
                    evidence_refs=[f"contact:{to}"],
                )
            ]
        )
    prop = seen.get("propose_action") or {}
    return _submit(
        "submit_email",
        to=[to],
        subject=r.get("subject", ""),
        body_text=r.get("body_text", ""),
        template=template,
        tone_score=tone,
        purpose=template,
        action_id=prop.get("action_id"),
        policy_decision=prop.get("policy_decision"),
    )


# ---------- intent ----------
def intent(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    seen = last_tool_results(messages)
    if "get_email_thread" not in seen:
        return AssistantTurn(tool_calls=[_call("get_email_thread", limit=6)])
    msgs = [
        m
        for m in (seen.get("get_email_thread") or {}).get("messages", [])
        if m.get("direction") == "IN"
    ]
    text = (msgs[-1]["body_text"] if msgs else "").lower()
    m_po = re.search(r"\b(PO-\d{4,8})\b", text, re.I)
    m_date = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    m_mail = (
        re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text.replace(" at ", "@"))
        if ("contact" in text or "reach" in text or "left" in text)
        else None
    )
    if m_po:
        kind = "PROVIDES_PO"
    elif any(
        k in text
        for k in (
            "scheduled",
            "payment will",
            "we will pay",
            "paid on",
            "remitted",
            "processed for payment",
        )
    ):
        kind = "CONFIRMS_PAYMENT"
    elif any(k in text for k in ("agree to the plan", "accept", "works for us", "plan is fine")):
        kind = "ACCEPTS_OFFER"
    elif any(k in text for k in ("cannot accept", "reject", "not acceptable")):
        kind = "REJECTS_OFFER"
    elif any(
        k in text for k in ("dispute", "incorrect", "never received", "overcharg", "wrong price")
    ):
        kind = "DISPUTES"
    elif "out of office" in text or "out-of-office" in text:
        kind = "OUT_OF_OFFICE"
    elif m_mail or ("no longer" in text and "contact" in text):
        kind = "REDIRECTS_CONTACT"
    elif any(k in text for k in ("send", "copy", "statement", "can you provide")):
        kind = "REQUESTS_INFO"
    else:
        kind = "UNCLEAR"
    return _submit(
        "submit_intent",
        intent=kind,
        po_number=m_po.group(1).upper() if m_po else None,
        promised_pay_date=m_date.group(1) if m_date else None,
        new_contact_email=m_mail.group(0) if m_mail else None,
        disputed_points=[text[:120]] if kind == "DISPUTES" else [],
        summary=(text[:300] or "no inbound text"),
        confidence=0.75 if kind != "UNCLEAR" else 0.4,
    )


HeuristicLLM.register("supervisor", supervisor)
HeuristicLLM.register("reconciler", reconciler)
HeuristicLLM.register("negotiator", negotiator)
HeuristicLLM.register("communicator", communicator)
HeuristicLLM.register("intent", intent)
HeuristicLLM.register("triage", triage)
HeuristicLLM.register("investigator", investigator)
