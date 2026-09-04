"""The specialist roster for this phase. Adding a specialist = an AgentSpec + prompt + heuristic."""

from __future__ import annotations

import json
from typing import Any

from recoup_orchestrator.agents.loop import AgentSpec
from recoup_orchestrator.agents.schemas import InvestigationOutput, SupervisorDecision, TriageOutput


def _state_block(summary: dict[str, Any]) -> str:
    return "<state>\n" + json.dumps(summary, indent=1, default=str) + "\n</state>"


def supervisor_task(summary: dict[str, Any]) -> str:
    return (
        "Decide the next step for this case. Available specialists are listed in the state.\n"
        + _state_block(summary)
        + "\nCall submit_decision with your decision."
    )


def triage_task(summary: dict[str, Any]) -> str:
    c = summary["case"]
    return (
        f"Triage case {c['id']}: invoice(s) {c['invoice_refs']} for customer {c['customer_ref']} "
        f"({c.get('customer_name')}), {c['amount_open']} {c['currency']} open, {c['days_overdue']} days overdue.\n"
        + _state_block(summary)
        + "\nUse the tools to gather facts, then call submit_triage."
    )


def investigator_task(summary: dict[str, Any]) -> str:
    c = summary["case"]
    hyp = summary.get("triage", {}) or {}
    return (
        f"Investigate case {c['id']} (invoice(s) {c['invoice_refs']}, customer {c['customer_ref']}). "
        f"Triage hypotheses: {json.dumps(hyp.get('root_cause_hypotheses'), default=str)}.\n"
        + _state_block(summary)
        + "\nGather evidence with the tools, then call submit_investigation."
    )


SUPERVISOR = AgentSpec(
    name="supervisor",
    prompt_name="supervisor",
    output_model=SupervisorDecision,
    tools=[],
    tier="strong",
    submit_description="Submit the supervisor decision for this turn. Call exactly once.",
    submit_tool_name="submit_decision",
    task_instructions=supervisor_task,
)
TRIAGE = AgentSpec(
    name="triage",
    prompt_name="triage",
    output_model=TriageOutput,
    tools=[
        "get_invoice",
        "get_customer",
        "get_remittances",
        "get_email_thread",
        "get_case_timeline",
        "list_open_invoices",
    ],
    tier="fast",
    submit_description="Submit the triage result (hypotheses, priority, recommended path). Call once.",
    task_instructions=triage_task,
)
INVESTIGATOR = AgentSpec(
    name="investigator",
    prompt_name="investigator",
    output_model=InvestigationOutput,
    tools=[
        "reconcile_lines",
        "check_duplicate_invoice",
        "get_purchase_order",
        "get_delivery_proof",
        "get_contract_terms",
        "get_remittances",
        "get_invoice",
        "get_email_thread",
    ],
    tier="strong",
    submit_description="Submit the investigation result (evidence, confirmed cause, gaps). Call once.",
    submit_tool_name="submit_investigation",
    task_instructions=investigator_task,
)

SPECIALISTS: dict[str, AgentSpec] = {"Triage": TRIAGE, "Investigator": INVESTIGATOR}
