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
def supervisor(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
    s = _state(messages)
    runs = s.get("specialist_runs", {})
    budget = s.get("budget", {})
    plan = [
        {
            "goal": "Classify root cause",
            "status": "done" if s.get("triage") else "todo",
            "owner": "Triage",
        },
        {
            "goal": "Confirm with evidence",
            "status": "done" if s.get("investigation") else "todo",
            "owner": "Investigator",
        },
        {"goal": "Resolve or hand to human", "status": "todo", "owner": "human"},
    ]
    if budget.get("used_pct", 0) >= 80:
        return _submit(
            "submit_decision",
            reasoning_summary="Budget nearly exhausted; escalating with findings.",
            updated_plan=plan,
            next="ESCALATED",
            escalation_reason="STEP_OR_TOKEN_BUDGET",
            confidence=0.6,
        )
    if not s.get("triage") and runs.get("Triage", 0) < 1:
        return _submit(
            "submit_decision",
            reasoning_summary="No triage yet; classify the root cause first.",
            updated_plan=plan,
            next="Triage",
            confidence=0.9,
        )
    if not s.get("investigation") and runs.get("Investigator", 0) < 1:
        return _submit(
            "submit_decision",
            reasoning_summary="Triage done; gather evidence for the top hypothesis.",
            updated_plan=plan,
            next="Investigator",
            confidence=0.85,
        )
    inv = s.get("investigation") or {}
    cause = inv.get("confirmed_cause") or (s.get("triage") or {}).get(
        "root_cause_hypotheses", [{}]
    )[0].get("cause")
    plan[2]["status"] = "doing"
    return _submit(
        "submit_decision",
        reasoning_summary=f"Root cause {cause} confirmed at {inv.get('confidence')}; resolution specialists are not available in this deployment, handing to a human with the evidence.",
        updated_plan=plan,
        next="ESCALATED",
        escalation_reason=f"Resolution requires a human: {cause} (credit {inv.get('proposed_credit_memo')}, gaps {inv.get('gaps')})",
        confidence=float(inv.get("confidence") or 0.5),
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


HeuristicLLM.register("supervisor", supervisor)
HeuristicLLM.register("triage", triage)
HeuristicLLM.register("investigator", investigator)
