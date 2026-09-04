"""Structured outputs (Pydantic-validated) and the case state that flows through the workflow."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

RootCause = Literal[
    "DISPUTE_PRICING",
    "DISPUTE_QUANTITY",
    "MISSING_PO",
    "WRONG_CONTACT",
    "SHORT_PAY",
    "DUPLICATE_INVOICE",
    "CASH_FLOW",
    "UNKNOWN",
]
NextStep = Literal[
    "Triage",
    "Investigator",
    "Reconciler",
    "Negotiator",
    "Communicator",
    "WAIT_FOR_CUSTOMER",
    "AWAIT_APPROVAL",
    "RESOLVED",
    "ESCALATED",
]
SPECIALISTS_AVAILABLE: frozenset[str] = frozenset(
    {"Triage", "Investigator", "Reconciler", "Negotiator", "Communicator"}
)
DISPUTE_CAUSES: frozenset[str] = frozenset(
    {"DISPUTE_PRICING", "DISPUTE_QUANTITY", "DUPLICATE_INVOICE", "SHORT_PAY"}
)


class PlanItem(BaseModel):
    goal: str = Field(max_length=200)
    status: Literal["todo", "doing", "done", "blocked"] = "todo"
    owner: str | None = Field(default=None, description="Specialist or 'human'")


class SupervisorDecision(BaseModel):
    reasoning_summary: str = Field(max_length=600)
    updated_plan: list[PlanItem] = Field(max_length=12)
    next: NextStep
    wait_timeout_hours: int | None = Field(default=None, ge=1, le=720)
    escalation_reason: str | None = Field(default=None, max_length=500)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _escalation_needs_reason(self) -> SupervisorDecision:
        if self.next == "ESCALATED" and not self.escalation_reason:
            raise ValueError("escalation_reason is required when next is ESCALATED")
        return self


class Hypothesis(BaseModel):
    cause: RootCause
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=10)


class TriageOutput(BaseModel):
    root_cause_hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    priority: int = Field(ge=1, le=5)
    recommended_path: Literal["investigate", "outreach", "negotiate", "escalate"]
    summary: str = Field(max_length=800)

    @property
    def top(self) -> Hypothesis:
        return max(self.root_cause_hypotheses, key=lambda h: h.confidence)


class EvidenceItem(BaseModel):
    source: str = Field(
        max_length=40, description="e.g. reconcile_lines, purchase_order, delivery_proof, email"
    )
    ref: str = Field(max_length=120)
    finding: str = Field(max_length=300)


class InvestigationOutput(BaseModel):
    evidence: list[EvidenceItem] = Field(max_length=20)
    confirmed_cause: RootCause
    confidence: float = Field(ge=0, le=1)
    contradicts_triage: bool = False
    gaps: list[str] = Field(default_factory=list, max_length=10)
    proposed_credit_memo: Decimal | None = None
    rebill_required: bool = False
    summary: str = Field(max_length=800)


class ProposedActionRef(BaseModel):
    """What a specialist proposed through propose_action, tracked until executed."""

    action_id: str
    action_type: str
    policy_decision: str
    required_role: str | None = None
    status: str = "PENDING"  # PENDING | APPROVED | EDITED | REJECTED | EXECUTED | DENIED | FAILED
    proposed_by: str
    summary: str = ""
    execution_result: dict[str, Any] | None = None


class LineDiscrepancyOut(BaseModel):
    kind: str
    line_no: int | None = None
    sku: str | None = None
    credit_before_tax: Decimal
    note: str = Field(max_length=300)


class ReconcilerOutput(BaseModel):
    line_discrepancies: list[LineDiscrepancyOut] = Field(max_length=20)
    proposed_credit_memo: Decimal = Field(ge=0)
    rebill_required: bool = False
    void_duplicate: bool = False
    rationale: str = Field(max_length=800)
    action_id: str | None = Field(
        default=None, description="From propose_action when a credit memo was proposed"
    )
    nothing_owed: bool = False


class Offer(BaseModel):
    type: Literal["PAYMENT_PLAN", "EXTENSION", "EARLY_PAY_DISCOUNT", "NONE"]
    installments: int | None = Field(default=None, ge=1, le=12)
    first_due: str | None = Field(default=None, description="ISO date")
    discount_pct: Decimal | None = Field(default=None, ge=0, le=100)
    extension_days: int | None = Field(default=None, ge=0, le=180)
    plan_total: Decimal | None = None
    summary: str = Field(max_length=300)


class NegotiatorOutput(BaseModel):
    offer: Offer
    fallback_offers: list[Offer] = Field(default_factory=list, max_length=3)
    walk_away_condition: str = Field(max_length=300)
    rationale: str = Field(max_length=800)
    action_id: str | None = Field(
        default=None, description="From propose_action when a plan was proposed"
    )


class CommunicatorOutput(BaseModel):
    to: list[str] = Field(min_length=1, max_length=5)
    subject: str = Field(max_length=200)
    body_text: str = Field(max_length=6000)
    template: str | None = None
    tone_score: float = Field(ge=0, le=1)
    purpose: Literal[
        "po_request",
        "resend_invoice",
        "payment_reminder",
        "dispute_resolution",
        "payment_plan_offer",
        "short_pay_followup",
        "other",
    ]
    action_id: str | None = Field(default=None, description="From propose_action(SEND_EMAIL)")
    policy_decision: str | None = None


class CustomerIntent(BaseModel):
    """Constrained extraction from an untrusted customer email: claims, never instructions."""

    intent: Literal[
        "CONFIRMS_PAYMENT",
        "PROVIDES_PO",
        "ACCEPTS_OFFER",
        "REJECTS_OFFER",
        "DISPUTES",
        "REQUESTS_INFO",
        "REDIRECTS_CONTACT",
        "OUT_OF_OFFICE",
        "UNCLEAR",
    ]
    po_number: str | None = None
    promised_pay_date: str | None = Field(
        default=None, description="ISO date if a payment date was promised"
    )
    promised_amount: Decimal | None = None
    new_contact_email: str | None = None
    disputed_points: list[str] = Field(default_factory=list, max_length=5)
    summary: str = Field(max_length=400)
    confidence: float = Field(ge=0, le=1)


class Budget(BaseModel):
    max_steps: int
    max_tokens: int


class StepSummary(BaseModel):
    step_no: int
    agent: str
    kind: str
    summary: str
    tokens: int = 0


class CaseState(BaseModel):
    run_id: uuid.UUID
    case_id: uuid.UUID
    tenant_id: uuid.UUID
    mode: str = "LIVE"
    step_no: int = 0
    budget: Budget
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    case: dict[str, Any] = Field(default_factory=dict)
    plan: list[PlanItem] = Field(default_factory=list)
    history: list[StepSummary] = Field(default_factory=list)
    specialist_runs: dict[str, int] = Field(default_factory=dict)
    signals: list[dict[str, Any]] = Field(default_factory=list)
    triage: TriageOutput | None = None
    investigation: InvestigationOutput | None = None
    reconciliation: ReconcilerOutput | None = None
    negotiation: NegotiatorOutput | None = None
    last_email: CommunicatorOutput | None = None
    last_intent: CustomerIntent | None = None
    actions: list[ProposedActionRef] = Field(default_factory=list)
    outreach_count: int = 0
    waits: int = 0
    last_decision: SupervisorDecision | None = None
    prompt_bundle: dict[str, int] = Field(default_factory=dict)

    def pending_actions(self) -> list[ProposedActionRef]:
        return [a for a in self.actions if a.status == "PENDING"]

    def approved_unexecuted(self) -> list[ProposedActionRef]:
        return [a for a in self.actions if a.status in ("APPROVED", "EDITED")]

    def executed(self, action_type: str | None = None) -> list[ProposedActionRef]:
        return [
            a
            for a in self.actions
            if a.status == "EXECUTED" and (action_type is None or a.action_type == action_type)
        ]

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out

    def budget_used_pct(self) -> int:
        steps = self.step_no / self.budget.max_steps if self.budget.max_steps else 0
        toks = self.tokens_total / self.budget.max_tokens if self.budget.max_tokens else 0
        return int(max(steps, toks) * 100)

    def summary_for_prompt(self) -> dict[str, Any]:
        """Compact JSON view the Supervisor reasons over (no raw tool payloads)."""
        c = self.case
        return {
            "case": {
                "id": str(self.case_id),
                "status": c.get("status"),
                "customer_ref": c.get("customer_ref"),
                "customer_name": c.get("customer_name"),
                "invoice_refs": c.get("invoice_refs"),
                "amount_open": c.get("amount_open"),
                "currency": c.get("currency"),
                "days_overdue": c.get("days_overdue"),
                "priority": c.get("priority"),
                "root_cause": c.get("root_cause"),
                "root_cause_conf": c.get("root_cause_conf"),
                "agent_mode": c.get("agent_mode"),
            },
            "budget": {
                "step": self.step_no,
                "max_steps": self.budget.max_steps,
                "tokens": self.tokens_total,
                "max_tokens": self.budget.max_tokens,
                "used_pct": self.budget_used_pct(),
            },
            "plan": [p.model_dump() for p in self.plan],
            "history": [h.model_dump() for h in self.history[-10:]],
            "specialist_runs": self.specialist_runs,
            "triage": self.triage.model_dump(mode="json") if self.triage else None,
            "investigation": self.investigation.model_dump(mode="json")
            if self.investigation
            else None,
            "reconciliation": self.reconciliation.model_dump(mode="json")
            if self.reconciliation
            else None,
            "negotiation": self.negotiation.model_dump(mode="json") if self.negotiation else None,
            "last_email": (
                {
                    k: v
                    for k, v in self.last_email.model_dump(mode="json").items()
                    if k != "body_text"
                }
                if self.last_email
                else None
            ),
            "last_customer_intent": self.last_intent.model_dump(mode="json")
            if self.last_intent
            else None,
            "actions": [
                a.model_dump(mode="json", exclude={"execution_result"}) for a in self.actions
            ],
            "outreach_count": self.outreach_count,
            "waits": self.waits,
            "signals": self.signals[-5:],
            "available_specialists": sorted(SPECIALISTS_AVAILABLE),
        }


class CaseOutcome(BaseModel):
    run_id: uuid.UUID
    case_id: uuid.UUID
    status: Literal["RESOLVED", "ESCALATED", "FAILED", "CANCELLED"]
    reason: str
    steps: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    root_cause: str | None = None
    confidence: float | None = None
