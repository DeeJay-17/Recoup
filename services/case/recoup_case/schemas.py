from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from recoup_case.state_machine import (
    ActionStatus,
    ActionType,
    AgentMode,
    CaseStatus,
    PolicyDecision,
)


class CaseCreate(BaseModel):
    customer_ref: str
    customer_name: str | None = None
    invoice_refs: list[str] = Field(min_length=1)
    amount_open: Decimal
    currency: str = Field(default="USD", min_length=3, max_length=3)
    days_overdue: int = 0
    priority: int | None = Field(default=None, ge=1, le=5)
    invoice_snapshot: dict[str, Any] | None = None


class CaseOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    customer_ref: str
    customer_name: str | None
    invoice_refs: list[str]
    status: CaseStatus
    root_cause: str | None
    root_cause_conf: Decimal | None
    priority: int
    amount_open: Decimal
    currency: str
    days_overdue: int
    assignee_id: uuid.UUID | None
    agent_mode: AgentMode
    workflow_id: str | None
    opened_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    resolution: dict[str, Any] | None
    version: int

    model_config = {"from_attributes": True}


class CasePage(BaseModel):
    items: list[CaseOut]
    total: int
    limit: int
    offset: int


class TimelineEventOut(BaseModel):
    id: int
    case_id: uuid.UUID
    kind: str
    actor_type: str
    actor_id: str
    title: str
    payload: dict[str, Any]
    trace_id: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ProposedActionCreate(BaseModel):
    action_type: ActionType
    payload: dict[str, Any]
    rationale: str
    evidence_refs: list[Any] = Field(default_factory=list)
    policy_decision: PolicyDecision
    policy_rule: str | None = None
    required_role: str | None = None
    proposed_by: str = "agent:unknown"
    run_id: uuid.UUID | None = None


class ProposedActionOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    action_type: ActionType
    payload: dict[str, Any]
    rationale: str
    evidence_refs: list[Any]
    policy_decision: PolicyDecision
    policy_rule: str | None
    required_role: str | None
    status: ActionStatus
    proposed_by: str
    run_id: uuid.UUID | None
    human_final: dict[str, Any] | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    feedback_code: str | None
    feedback_note: str | None
    executed_at: datetime | None
    execution_result: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DecisionBody(BaseModel):
    feedback_code: str | None = None
    feedback_note: str | None = None
    expected_version: int | None = None


class EditBody(DecisionBody):
    human_final: dict[str, Any]


class TransitionBody(BaseModel):
    to: CaseStatus
    reason: str | None = None
    expected_version: int | None = None


class NoteBody(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class TakeoverBody(BaseModel):
    reason: str | None = None


class AssignBody(BaseModel):
    assignee_id: uuid.UUID | None


class TriageUpdate(BaseModel):
    root_cause: str
    root_cause_conf: Decimal = Field(ge=0, le=1)
    priority: int | None = Field(default=None, ge=1, le=5)
    summary: str | None = None
    actor_id: str = "agent:triage"


class CaseDetail(BaseModel):
    case: CaseOut
    timeline: list[TimelineEventOut]
    actions: list[ProposedActionOut]


class IngestResult(BaseModel):
    tenant_id: uuid.UUID
    scanned: int
    created: int
    skipped: int
    duration_ms: int


class TimelineAppend(BaseModel):
    """Used by other services (tool gateway, comms) to append to the audit timeline."""

    kind: str = Field(pattern=r"^[a-z_]{2,40}$")
    actor_type: str = Field(pattern=r"^(agent|human|system)$")
    actor_id: str
    title: str = Field(max_length=300)
    payload: dict[str, Any] = Field(default_factory=dict)
