"""Runs the specialists end-to-end with the heuristic provider and a fake tool gateway."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from recoup_llm import HeuristicLLM
from recoup_orchestrator.agents import heuristics
from recoup_orchestrator.agents.loop import RepairExhausted, ToolLoopAgent
from recoup_orchestrator.agents.schemas import (
    Budget,
    CaseState,
    InvestigationOutput,
    SupervisorDecision,
    TriageOutput,
)
from recoup_orchestrator.agents.specs import INVESTIGATOR, SUPERVISOR, TRIAGE
from recoup_orchestrator.workflows.activities import _escalation_brief


class FakeGateway:
    """Answers tool calls from canned data and records what was asked."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def invoke(self, name: str, **kw: Any) -> dict[str, Any]:
        self.calls.append(name)
        if name in self.responses:
            return {"status": "SUCCESS", "result": self.responses[name], "invocation_id": 1}
        return {"status": "BLOCKED", "error": "not_found", "message": f"unknown tool {name}"}


MANIFEST = [
    {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}
    for n in (
        "get_invoice",
        "get_customer",
        "get_remittances",
        "get_email_thread",
        "reconcile_lines",
        "check_duplicate_invoice",
    )
]


def _state(**case: Any) -> CaseState:
    base = {
        "id": str(uuid.uuid4()),
        "status": "NEW",
        "customer_ref": "CUST-0001",
        "customer_name": "Acme",
        "invoice_refs": ["INV-100001"],
        "amount_open": "1200.00",
        "currency": "USD",
        "days_overdue": 20,
        "priority": 3,
        "root_cause": None,
        "root_cause_conf": None,
        "agent_mode": "AUTONOMOUS",
    }
    return CaseState(
        run_id=uuid.uuid4(),
        case_id=uuid.UUID(str(base["id"])),
        tenant_id=uuid.uuid4(),
        budget=Budget(max_steps=10, max_tokens=1000),
        case={**base, **case},
    )


async def _run(
    spec: Any, gateway: FakeGateway, state: CaseState, manifest: list[dict[str, Any]] = MANIFEST
) -> Any:
    agent = ToolLoopAgent(
        spec,
        llm=HeuristicLLM(),
        gateway=gateway,  # type: ignore[arg-type]
        tenant_id=state.tenant_id,
        case_id=state.case_id,
        run_id=state.run_id,
    )  # type: ignore[arg-type]
    return await agent.run(
        system_prompt="sys",
        user_message=spec.task_instructions(state.summary_for_prompt()),
        manifest=manifest,
        thread_id="t",
    )


async def test_triage_missing_po() -> None:
    gw = FakeGateway(
        {
            "get_invoice": {
                "invoice_ref": "INV-100001",
                "po_number": None,
                "status": "OPEN",
                "amount_open": "1200.00",
                "amount_paid": "0",
                "billed_to_email": "ap@x.com",
            },
            "get_customer": {
                "requires_po": True,
                "credit_risk_score": 0.2,
                "contacts": [{"email": "ap@x.com", "active": True}],
            },
            "get_remittances": {"items": []},
            "get_email_thread": {"messages": []},
        }
    )
    res = await _run(TRIAGE, gw, _state())
    out: TriageOutput = res.output
    assert out.top.cause == "MISSING_PO"
    assert out.recommended_path == "outreach"
    assert gw.calls[:2] == ["get_invoice", "get_customer"]
    assert res.tool_log[-1]["tool"] == "submit_triage"


async def test_triage_wrong_contact_and_short_pay() -> None:
    gw = FakeGateway(
        {
            "get_invoice": {
                "po_number": "PO-1",
                "status": "PARTIALLY_PAID",
                "amount_open": "50.00",
                "amount_paid": "950.00",
                "billed_to_email": "old@x.com",
            },
            "get_customer": {
                "requires_po": False,
                "credit_risk_score": 0.1,
                "contacts": [
                    {"email": "old@x.com", "active": False},
                    {"email": "new@x.com", "active": True},
                ],
            },
            "get_remittances": {
                "items": [{"remittance_id": "REM-1", "memo": "INV-100001 less freight"}]
            },
            "get_email_thread": {"messages": []},
        }
    )
    out: TriageOutput = (await _run(TRIAGE, gw, _state())).output
    causes = [h.cause for h in out.root_cause_hypotheses]
    assert causes[:2] == ["WRONG_CONTACT", "SHORT_PAY"]


async def test_investigator_confirms_pricing_dispute() -> None:
    state = _state()
    state.triage = TriageOutput(
        root_cause_hypotheses=[{"cause": "DISPUTE_PRICING", "confidence": 0.5}],
        priority=3,
        recommended_path="investigate",
        summary="x",
    )  # type: ignore[list-item]
    gw = FakeGateway(
        {
            "reconcile_lines": {
                "po_number": "PO-1",
                "po_found": True,
                "delivery_found": True,
                "proposed_credit_memo": "107.00",
                "rebill_required": False,
                "summary": "1 discrepancy",
                "discrepancies": [
                    {"kind": "PRICE_ABOVE_PO", "line_no": 1, "sku": "A", "note": "price 6 > 5"}
                ],
            },
            "check_duplicate_invoice": {
                "is_duplicate": False,
                "candidates": [],
                "explanation": "none",
            },
        }
    )
    out: InvestigationOutput = (await _run(INVESTIGATOR, gw, state)).output
    assert out.confirmed_cause == "DISPUTE_PRICING"
    assert str(out.proposed_credit_memo) == "107.00"
    assert out.evidence[0].source == "reconcile_lines"
    assert not out.contradicts_triage


async def test_supervisor_sequence_and_escalation_brief() -> None:
    gw = FakeGateway({})
    s = _state()
    d1: SupervisorDecision = (await _run(SUPERVISOR, gw, s, manifest=[])).output
    assert d1.next == "Triage"
    s.triage = TriageOutput(
        root_cause_hypotheses=[{"cause": "DISPUTE_PRICING", "confidence": 0.5}],
        priority=3,
        recommended_path="investigate",
        summary="x",
    )  # type: ignore[list-item]
    s.specialist_runs["Triage"] = 1
    d2: SupervisorDecision = (await _run(SUPERVISOR, gw, s, manifest=[])).output
    assert d2.next == "Investigator"
    s.investigation = InvestigationOutput(
        evidence=[],
        confirmed_cause="DISPUTE_PRICING",
        confidence=0.9,
        gaps=["no delivery proof"],
        proposed_credit_memo=107,
        summary="y",
    )  # type: ignore[arg-type]
    s.specialist_runs["Investigator"] = 1
    d3: SupervisorDecision = (await _run(SUPERVISOR, gw, s, manifest=[])).output
    assert d3.next == "ESCALATED" and d3.escalation_reason
    brief = _escalation_brief(s, d3.escalation_reason)
    assert "Reconciled credit due: 107" in brief and "no delivery proof" in brief


async def test_invalid_submission_gets_repaired_then_fails() -> None:
    from recoup_llm import AssistantTurn, Message, ToolCall, ToolSchema

    def bad(messages: list[Message], tools: list[ToolSchema]) -> AssistantTurn:
        return AssistantTurn(
            tool_calls=[ToolCall(id="x", name="submit_decision", args={"next": "NOPE"})]
        )

    HeuristicLLM.register("supervisor", bad)
    try:
        agent = ToolLoopAgent(
            SUPERVISOR,
            llm=HeuristicLLM(),
            gateway=FakeGateway({}),  # type: ignore[arg-type]
            tenant_id=uuid.uuid4(),
            case_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            max_repairs=1,
        )  # type: ignore[arg-type]
        with pytest.raises(RepairExhausted):
            await agent.run(system_prompt="s", user_message="u", manifest=[], thread_id="t")
    finally:
        HeuristicLLM.register("supervisor", heuristics.supervisor)


def test_budget_pct() -> None:
    s = _state()
    s.step_no, s.tokens_in = 5, 900
    assert s.budget_used_pct() == 90
