"""Case domain operations.

Every mutation: validate -> mutate -> timeline -> outbox, in one transaction.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from recoup_common.auth import Principal
from recoup_common.db import utcnow
from recoup_common.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from recoup_common.events.outbox import enqueue_event
from recoup_common.events.topics import EventTypes
from recoup_common.tracing import current_trace_id
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_case.models import Case, CaseInvoice, Outbox, ProposedAction, TimelineEvent
from recoup_case.schemas import CaseCreate, ProposedActionCreate, TimelineAppend, TriageUpdate
from recoup_case.state_machine import (
    ActionStatus,
    AgentMode,
    CaseStatus,
    PolicyDecision,
    assert_transition,
    is_terminal,
)

SOURCE = "case-service"


def compute_priority(amount_open: Decimal, days_overdue: int) -> int:
    """1 = highest. Big and old -> urgent."""
    score = 0
    if amount_open >= 25_000:
        score += 3
    elif amount_open >= 5_000:
        score += 2
    elif amount_open >= 1_000:
        score += 1
    if days_overdue >= 60:
        score += 3
    elif days_overdue >= 30:
        score += 2
    elif days_overdue >= 14:
        score += 1
    return max(1, 5 - score)


# ---------- helpers ----------
def _timeline(
    session: AsyncSession,
    case: Case,
    *,
    kind: str,
    actor_type: str,
    actor_id: str,
    title: str,
    payload: dict[str, Any] | None = None,
) -> TimelineEvent:
    ev = TimelineEvent(
        case_id=case.id,
        tenant_id=case.tenant_id,
        kind=kind,
        actor_type=actor_type,
        actor_id=actor_id,
        title=title,
        payload=payload or {},
        trace_id=current_trace_id(),
        occurred_at=utcnow(),
    )
    session.add(ev)
    return ev


def _emit(session: AsyncSession, case: Case, event_type: str, data: dict[str, Any]) -> None:
    enqueue_event(
        session,
        Outbox,
        source=SOURCE,
        tenant_id=case.tenant_id,
        aggregate_id=case.id,
        event_type=event_type,
        payload={"case_id": str(case.id), "tenant_id": str(case.tenant_id), **data},
    )


def _touch(case: Case, expected_version: int | None = None) -> None:
    if expected_version is not None and case.version != expected_version:
        raise ConflictError(
            "case was modified by someone else; reload",
            details={"expected": expected_version, "actual": case.version},
        )
    case.updated_at = utcnow()


def _set_status(
    session: AsyncSession,
    case: Case,
    to: CaseStatus,
    *,
    actor_type: str,
    actor_id: str,
    reason: str | None = None,
) -> None:
    src = CaseStatus(case.status)
    assert_transition(src, to)
    case.status = to.value
    if is_terminal(to):
        case.resolved_at = utcnow()
    _timeline(
        session,
        case,
        kind="state_change",
        actor_type=actor_type,
        actor_id=actor_id,
        title=f"{src} → {to}",
        payload={"from": src.value, "to": to.value, "reason": reason},
    )
    _emit(
        session,
        case,
        EventTypes.CASE_STATE_CHANGED,
        {"from": src.value, "to": to.value, "reason": reason, "actor": actor_id},
    )
    if is_terminal(to):
        _emit(session, case, EventTypes.CASE_RESOLVED, {"status": to.value, "reason": reason})


# ---------- queries ----------
async def get_case(session: AsyncSession, tenant_id: uuid.UUID, case_id: uuid.UUID) -> Case:
    case = await session.get(Case, case_id)
    if not case or case.tenant_id != tenant_id:
        raise NotFoundError("case not found")
    return case


async def list_cases(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: list[CaseStatus] | None,
    assignee_id: uuid.UUID | None,
    customer_ref: str | None,
    min_days_overdue: int | None,
    limit: int,
    offset: int,
) -> tuple[list[Case], int]:
    stmt = select(Case).where(Case.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Case.status.in_([s.value for s in status]))
    if assignee_id:
        stmt = stmt.where(Case.assignee_id == assignee_id)
    if customer_ref:
        stmt = stmt.where(Case.customer_ref == customer_ref)
    if min_days_overdue is not None:
        stmt = stmt.where(Case.days_overdue >= min_days_overdue)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await session.scalars(
            stmt.order_by(Case.priority.asc(), Case.amount_open.desc(), Case.opened_at.asc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return list(rows), int(total or 0)


async def timeline(session: AsyncSession, case: Case) -> list[TimelineEvent]:
    rows = await session.scalars(
        select(TimelineEvent)
        .where(TimelineEvent.case_id == case.id)
        .order_by(TimelineEvent.occurred_at.asc(), TimelineEvent.id.asc())
    )
    return list(rows.all())


async def actions_for(session: AsyncSession, case: Case) -> list[ProposedAction]:
    rows = await session.scalars(
        select(ProposedAction)
        .where(ProposedAction.case_id == case.id)
        .order_by(ProposedAction.created_at.desc())
    )
    return list(rows.all())


async def pending_actions(
    session: AsyncSession, tenant_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[ProposedAction], int]:
    stmt = select(ProposedAction).where(
        ProposedAction.tenant_id == tenant_id, ProposedAction.status == ActionStatus.PENDING.value
    )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.scalars(
        stmt.order_by(ProposedAction.created_at.asc()).limit(limit).offset(offset)
    )
    return list(rows.all()), int(total or 0)


async def find_case_for_invoice(
    session: AsyncSession, tenant_id: uuid.UUID, invoice_ref: str
) -> Case | None:
    stmt = (
        select(Case)
        .join(CaseInvoice, CaseInvoice.case_id == Case.id)
        .where(CaseInvoice.tenant_id == tenant_id, CaseInvoice.invoice_ref == invoice_ref)
    )
    case: Case | None = await session.scalar(stmt)
    return case


# ---------- mutations ----------
async def create_case(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    data: CaseCreate,
    *,
    actor_id: str = "system:ingestion",
) -> Case:
    for ref in data.invoice_refs:
        if await session.get(CaseInvoice, (tenant_id, ref)):
            raise ConflictError(f"invoice {ref} already has a case", details={"invoice_ref": ref})
    case = Case(
        tenant_id=tenant_id,
        customer_ref=data.customer_ref,
        customer_name=data.customer_name,
        invoice_refs=list(data.invoice_refs),
        status=CaseStatus.NEW.value,
        priority=data.priority or compute_priority(data.amount_open, data.days_overdue),
        amount_open=data.amount_open,
        currency=data.currency.upper(),
        days_overdue=data.days_overdue,
        agent_mode=AgentMode.AUTONOMOUS.value,
        opened_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(case)
    await session.flush()
    for ref in data.invoice_refs:
        session.add(CaseInvoice(tenant_id=tenant_id, invoice_ref=ref, case_id=case.id))
    _timeline(
        session,
        case,
        kind="created",
        actor_type="system",
        actor_id=actor_id,
        title=f"Case opened for {', '.join(data.invoice_refs)} ({data.days_overdue}d overdue)",
        payload={"invoice_snapshot": data.invoice_snapshot or {}},
    )
    _emit(
        session,
        case,
        EventTypes.CASE_CREATED,
        {
            "customer_ref": case.customer_ref,
            "invoice_refs": case.invoice_refs,
            "amount_open": str(case.amount_open),
            "currency": case.currency,
            "priority": case.priority,
            "days_overdue": case.days_overdue,
        },
    )
    return case


async def transition(
    session: AsyncSession,
    case: Case,
    to: CaseStatus,
    *,
    actor_type: str,
    actor_id: str,
    reason: str | None = None,
    expected_version: int | None = None,
    resolution: dict[str, Any] | None = None,
) -> Case:
    _touch(case, expected_version)
    if resolution is not None:
        case.resolution = resolution
    _set_status(session, case, to, actor_type=actor_type, actor_id=actor_id, reason=reason)
    return case


async def apply_triage(session: AsyncSession, case: Case, data: TriageUpdate) -> Case:
    _touch(case)
    case.root_cause = data.root_cause
    case.root_cause_conf = data.root_cause_conf
    if data.priority:
        case.priority = data.priority
    _timeline(
        session,
        case,
        kind="agent_step",
        actor_type="agent",
        actor_id=data.actor_id,
        title=f"Triaged: {data.root_cause} ({float(data.root_cause_conf):.0%})",
        payload={
            "root_cause": data.root_cause,
            "confidence": str(data.root_cause_conf),
            "summary": data.summary,
        },
    )
    if CaseStatus(case.status) == CaseStatus.NEW:
        _set_status(session, case, CaseStatus.TRIAGED, actor_type="agent", actor_id=data.actor_id)
    return case


async def add_note(
    session: AsyncSession, case: Case, principal: Principal, text: str
) -> TimelineEvent:
    _touch(case)
    ev = _timeline(
        session,
        case,
        kind="note",
        actor_type="human",
        actor_id=str(principal.sub),
        title="Note added",
        payload={"text": text, "author_email": principal.email},
    )
    _emit(session, case, EventTypes.CASE_NOTE_ADDED, {"author": str(principal.sub)})
    return ev


async def assign(
    session: AsyncSession, case: Case, principal: Principal, assignee: uuid.UUID | None
) -> Case:
    _touch(case)
    case.assignee_id = assignee
    _timeline(
        session,
        case,
        kind="assignment",
        actor_type="human",
        actor_id=str(principal.sub),
        title="Assigned" if assignee else "Unassigned",
        payload={"assignee_id": str(assignee) if assignee else None},
    )
    return case


async def takeover(
    session: AsyncSession, case: Case, principal: Principal, reason: str | None
) -> Case:
    if is_terminal(CaseStatus(case.status)):
        raise ConflictError("cannot take over a closed case")
    _touch(case)
    if case.agent_mode == AgentMode.HUMAN_CONTROL.value:
        raise ConflictError("case is already under human control")
    case.agent_mode = AgentMode.HUMAN_CONTROL.value
    case.assignee_id = case.assignee_id or principal.sub
    _timeline(
        session,
        case,
        kind="takeover",
        actor_type="human",
        actor_id=str(principal.sub),
        title="Human took over; agent paused",
        payload={"reason": reason, "by": principal.email},
    )
    _emit(session, case, EventTypes.CASE_TAKEOVER, {"by": str(principal.sub), "reason": reason})
    return case


async def release(session: AsyncSession, case: Case, principal: Principal) -> Case:
    _touch(case)
    if case.agent_mode != AgentMode.HUMAN_CONTROL.value:
        raise ConflictError("case is not under human control")
    case.agent_mode = AgentMode.AUTONOMOUS.value
    _timeline(
        session,
        case,
        kind="release",
        actor_type="human",
        actor_id=str(principal.sub),
        title="Returned to agent",
        payload={"by": principal.email},
    )
    _emit(session, case, EventTypes.CASE_RELEASED, {"by": str(principal.sub)})
    return case


async def propose_action(
    session: AsyncSession, case: Case, data: ProposedActionCreate
) -> ProposedAction:
    if is_terminal(CaseStatus(case.status)):
        raise ConflictError("cannot propose actions on a closed case")
    _touch(case)
    status = {
        PolicyDecision.ALLOW: ActionStatus.APPROVED,  # execution follows; auto-approved by policy
        PolicyDecision.REQUIRE_APPROVAL: ActionStatus.PENDING,
        PolicyDecision.DENY: ActionStatus.DENIED,
    }[data.policy_decision]
    action = ProposedAction(
        case_id=case.id,
        tenant_id=case.tenant_id,
        action_type=data.action_type.value,
        payload=data.payload,
        rationale=data.rationale,
        evidence_refs=data.evidence_refs,
        policy_decision=data.policy_decision.value,
        policy_rule=data.policy_rule,
        required_role=data.required_role,
        status=status.value,
        proposed_by=data.proposed_by,
        run_id=data.run_id,
        created_at=utcnow(),
    )
    session.add(action)
    await session.flush()
    _timeline(
        session,
        case,
        kind="action_proposed",
        actor_type="agent",
        actor_id=data.proposed_by,
        title=f"Proposed {data.action_type} → policy {data.policy_decision}",
        payload={
            "action_id": str(action.id),
            "action_type": action.action_type,
            "policy_rule": data.policy_rule,
        },
    )
    _emit(
        session,
        case,
        EventTypes.CASE_ACTION_PROPOSED,
        {
            "action_id": str(action.id),
            "action_type": action.action_type,
            "policy_decision": action.policy_decision,
            "required_role": action.required_role,
        },
    )
    if status is ActionStatus.PENDING and CaseStatus(case.status) != CaseStatus.PENDING_APPROVAL:
        _set_status(
            session,
            case,
            CaseStatus.PENDING_APPROVAL,
            actor_type="agent",
            actor_id=data.proposed_by,
        )
    return action


async def get_action(session: AsyncSession, case: Case, action_id: uuid.UUID) -> ProposedAction:
    action = await session.get(ProposedAction, action_id)
    if not action or action.case_id != case.id:
        raise NotFoundError("action not found")
    return action


def _check_can_decide(action: ProposedAction, principal: Principal) -> None:
    if action.status != ActionStatus.PENDING.value:
        raise ConflictError(f"action is {action.status}, not PENDING")
    if action.required_role:
        from recoup_common.auth import Role

        if not principal.at_least(Role(action.required_role)):
            raise ForbiddenError(f"this action requires role >= {action.required_role}")


async def decide_action(
    session: AsyncSession,
    case: Case,
    action: ProposedAction,
    principal: Principal,
    *,
    decision: ActionStatus,
    human_final: dict[str, Any] | None = None,
    feedback_code: str | None = None,
    feedback_note: str | None = None,
    expected_version: int | None = None,
) -> ProposedAction:
    if decision not in (ActionStatus.APPROVED, ActionStatus.EDITED, ActionStatus.REJECTED):
        raise ValidationError("decision must be APPROVED, EDITED or REJECTED")
    _check_can_decide(action, principal)
    _touch(case, expected_version)
    action.status = decision.value
    action.decided_by = principal.sub
    action.decided_at = utcnow()
    action.feedback_code = feedback_code
    action.feedback_note = feedback_note
    if decision is ActionStatus.EDITED:
        if not human_final:
            raise ValidationError("human_final is required when editing")
        action.human_final = human_final
    event_type = {
        ActionStatus.APPROVED: EventTypes.CASE_ACTION_APPROVED,
        ActionStatus.EDITED: EventTypes.CASE_ACTION_EDITED,
        ActionStatus.REJECTED: EventTypes.CASE_ACTION_REJECTED,
    }[decision]
    _timeline(
        session,
        case,
        kind="approval",
        actor_type="human",
        actor_id=str(principal.sub),
        title=f"{decision.title()} {action.action_type}",
        payload={
            "action_id": str(action.id),
            "decision": decision.value,
            "feedback_code": feedback_code,
            "feedback_note": feedback_note,
            "by": principal.email,
        },
    )
    _emit(
        session,
        case,
        event_type,
        {"action_id": str(action.id), "action_type": action.action_type, "by": str(principal.sub)},
    )
    # No other pending actions? Move the case back into the agent's hands.
    still_pending = await session.scalar(
        select(func.count())
        .select_from(ProposedAction)
        .where(
            ProposedAction.case_id == case.id, ProposedAction.status == ActionStatus.PENDING.value
        )
    )
    if not still_pending and CaseStatus(case.status) == CaseStatus.PENDING_APPROVAL:
        nxt = (
            CaseStatus.ACTION_TAKEN
            if decision is not ActionStatus.REJECTED
            else CaseStatus.INVESTIGATING
        )
        _set_status(
            session,
            case,
            nxt,
            actor_type="human",
            actor_id=str(principal.sub),
            reason=f"action {decision.value.lower()}",
        )
    return action


async def mark_executed(
    session: AsyncSession,
    case: Case,
    action: ProposedAction,
    result: dict[str, Any],
    *,
    actor_id: str,
) -> ProposedAction:
    if action.status not in (ActionStatus.APPROVED.value, ActionStatus.EDITED.value):
        raise ConflictError(f"action must be APPROVED/EDITED to execute, is {action.status}")
    _touch(case)
    action.status = (
        ActionStatus.AUTO_EXECUTED.value
        if action.policy_decision == PolicyDecision.ALLOW.value and action.decided_by is None
        else ActionStatus.EXECUTED.value
    )
    action.executed_at = utcnow()
    action.execution_result = result
    _timeline(
        session,
        case,
        kind="action_executed",
        actor_type="system",
        actor_id=actor_id,
        title=f"Executed {action.action_type}",
        payload={"action_id": str(action.id), "result": result},
    )
    return action


async def append_event(session: AsyncSession, case: Case, data: TimelineAppend) -> TimelineEvent:
    """Append an externally-produced event (tool call, email) to the timeline. No state change."""
    return _timeline(
        session,
        case,
        kind=data.kind,
        actor_type=data.actor_type,
        actor_id=data.actor_id,
        title=data.title,
        payload=data.payload,
    )
