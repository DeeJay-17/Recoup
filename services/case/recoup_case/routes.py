from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from recoup_common.auth import Principal, Role, get_principal, require_role
from recoup_common.db import Database
from recoup_common.errors import ForbiddenError, NotFoundError
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_case import service
from recoup_case.ingestion import IngestionJob
from recoup_case.schemas import (
    AssignBody,
    CaseCreate,
    CaseDetail,
    CaseOut,
    CasePage,
    DecisionBody,
    EditBody,
    IngestResult,
    NoteBody,
    ProposedActionCreate,
    ProposedActionOut,
    TakeoverBody,
    TimelineAppend,
    TimelineEventOut,
    TransitionBody,
    TriageUpdate,
)
from recoup_case.state_machine import ActionStatus, CaseStatus

router = APIRouter()
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
Analyst = Annotated[Principal, Depends(require_role(Role.ANALYST))]
Viewer = Annotated[Principal, Depends(get_principal)]


# ---------- read ----------
@router.get("/cases", response_model=CasePage, tags=["cases"])
async def list_cases(
    session: SessionDep,
    principal: Viewer,
    status: Annotated[list[CaseStatus] | None, Query()] = None,
    assignee_id: uuid.UUID | None = None,
    customer_ref: str | None = None,
    min_days_overdue: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> CasePage:
    rows, total = await service.list_cases(
        session,
        principal.tenant_id,
        status=status,
        assignee_id=assignee_id,
        customer_ref=customer_ref,
        min_days_overdue=min_days_overdue,
        limit=limit,
        offset=offset,
    )
    return CasePage(
        items=[CaseOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/cases/{case_id}", response_model=CaseOut, tags=["cases"])
async def get_case(case_id: uuid.UUID, session: SessionDep, principal: Viewer) -> CaseOut:
    return CaseOut.model_validate(await service.get_case(session, principal.tenant_id, case_id))


@router.get("/cases/{case_id}/detail", response_model=CaseDetail, tags=["cases"])
async def get_case_detail(case_id: uuid.UUID, session: SessionDep, principal: Viewer) -> CaseDetail:
    case = await service.get_case(session, principal.tenant_id, case_id)
    return CaseDetail(
        case=CaseOut.model_validate(case),
        timeline=[
            TimelineEventOut.model_validate(t) for t in await service.timeline(session, case)
        ],
        actions=[
            ProposedActionOut.model_validate(a) for a in await service.actions_for(session, case)
        ],
    )


@router.get("/cases/{case_id}/timeline", response_model=list[TimelineEventOut], tags=["cases"])
async def get_timeline(
    case_id: uuid.UUID, session: SessionDep, principal: Viewer
) -> list[TimelineEventOut]:
    case = await service.get_case(session, principal.tenant_id, case_id)
    return [TimelineEventOut.model_validate(t) for t in await service.timeline(session, case)]


@router.get("/cases/{case_id}/actions", response_model=list[ProposedActionOut], tags=["actions"])
async def get_actions(
    case_id: uuid.UUID, session: SessionDep, principal: Viewer
) -> list[ProposedActionOut]:
    case = await service.get_case(session, principal.tenant_id, case_id)
    return [ProposedActionOut.model_validate(a) for a in await service.actions_for(session, case)]


@router.get("/approvals", response_model=list[ProposedActionOut], tags=["actions"])
async def approvals_inbox(
    session: SessionDep,
    principal: Analyst,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProposedActionOut]:
    rows, _ = await service.pending_actions(
        session, principal.tenant_id, limit=limit, offset=offset
    )
    return [ProposedActionOut.model_validate(a) for a in rows]


# ---------- human mutations ----------
@router.post("/cases", response_model=CaseOut, status_code=201, tags=["cases"])
async def create_case(body: CaseCreate, session: SessionDep, principal: Analyst) -> CaseOut:
    case = await service.create_case(
        session, principal.tenant_id, body, actor_id=f"human:{principal.sub}"
    )
    return CaseOut.model_validate(case)


@router.post("/cases/{case_id}/transition", response_model=CaseOut, tags=["cases"])
async def transition_case(
    case_id: uuid.UUID, body: TransitionBody, session: SessionDep, principal: Analyst
) -> CaseOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    if body.to == CaseStatus.WRITTEN_OFF and not principal.at_least(Role.MANAGER):
        raise ForbiddenError("write-off requires manager role")
    await service.transition(
        session,
        case,
        body.to,
        actor_type="human",
        actor_id=str(principal.sub),
        reason=body.reason,
        expected_version=body.expected_version,
    )
    return CaseOut.model_validate(case)


@router.post(
    "/cases/{case_id}/notes", response_model=TimelineEventOut, status_code=201, tags=["cases"]
)
async def add_note(
    case_id: uuid.UUID, body: NoteBody, session: SessionDep, principal: Analyst
) -> TimelineEventOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    ev = await service.add_note(session, case, principal, body.text)
    await session.flush()
    return TimelineEventOut.model_validate(ev)


@router.post("/cases/{case_id}/assign", response_model=CaseOut, tags=["cases"])
async def assign_case(
    case_id: uuid.UUID, body: AssignBody, session: SessionDep, principal: Analyst
) -> CaseOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    await service.assign(session, case, principal, body.assignee_id)
    return CaseOut.model_validate(case)


@router.post("/cases/{case_id}/takeover", response_model=CaseOut, tags=["cases"])
async def takeover(
    case_id: uuid.UUID, body: TakeoverBody, session: SessionDep, principal: Analyst
) -> CaseOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    await service.takeover(session, case, principal, body.reason)
    return CaseOut.model_validate(case)


@router.post("/cases/{case_id}/release", response_model=CaseOut, tags=["cases"])
async def release(case_id: uuid.UUID, session: SessionDep, principal: Analyst) -> CaseOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    await service.release(session, case, principal)
    return CaseOut.model_validate(case)


@router.post(
    "/cases/{case_id}/actions/{action_id}/approve",
    response_model=ProposedActionOut,
    tags=["actions"],
)
async def approve_action(
    case_id: uuid.UUID,
    action_id: uuid.UUID,
    body: DecisionBody,
    session: SessionDep,
    principal: Analyst,
) -> ProposedActionOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    action = await service.get_action(session, case, action_id)
    await service.decide_action(
        session,
        case,
        action,
        principal,
        decision=ActionStatus.APPROVED,
        feedback_code=body.feedback_code,
        feedback_note=body.feedback_note,
        expected_version=body.expected_version,
    )
    return ProposedActionOut.model_validate(action)


@router.post(
    "/cases/{case_id}/actions/{action_id}/reject",
    response_model=ProposedActionOut,
    tags=["actions"],
)
async def reject_action(
    case_id: uuid.UUID,
    action_id: uuid.UUID,
    body: DecisionBody,
    session: SessionDep,
    principal: Analyst,
) -> ProposedActionOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    action = await service.get_action(session, case, action_id)
    await service.decide_action(
        session,
        case,
        action,
        principal,
        decision=ActionStatus.REJECTED,
        feedback_code=body.feedback_code,
        feedback_note=body.feedback_note,
        expected_version=body.expected_version,
    )
    return ProposedActionOut.model_validate(action)


@router.post(
    "/cases/{case_id}/actions/{action_id}/edit", response_model=ProposedActionOut, tags=["actions"]
)
async def edit_action(
    case_id: uuid.UUID,
    action_id: uuid.UUID,
    body: EditBody,
    session: SessionDep,
    principal: Analyst,
) -> ProposedActionOut:
    case = await service.get_case(session, principal.tenant_id, case_id)
    action = await service.get_action(session, case, action_id)
    await service.decide_action(
        session,
        case,
        action,
        principal,
        decision=ActionStatus.EDITED,
        human_final=body.human_final,
        feedback_code=body.feedback_code,
        feedback_note=body.feedback_note,
        expected_version=body.expected_version,
    )
    return ProposedActionOut.model_validate(action)


# ---------- internal (agents / tool gateway / ops): no user JWT; isolated in prod ----------
@internal.post("/ingest", response_model=IngestResult)
async def ingest_now(request: Request) -> IngestResult:
    job: IngestionJob = request.app.state.ingestion
    return await job.run_once()


@internal.post("/cases/{case_id}/actions", response_model=ProposedActionOut, status_code=201)
async def propose_action(
    case_id: uuid.UUID, tenant_id: uuid.UUID, body: ProposedActionCreate, session: SessionDep
) -> ProposedActionOut:
    case = await service.get_case(session, tenant_id, case_id)
    action = await service.propose_action(session, case, body)
    return ProposedActionOut.model_validate(action)


@internal.post("/cases/{case_id}/triage", response_model=CaseOut)
async def apply_triage(
    case_id: uuid.UUID, tenant_id: uuid.UUID, body: TriageUpdate, session: SessionDep
) -> CaseOut:
    case = await service.get_case(session, tenant_id, case_id)
    await service.apply_triage(session, case, body)
    return CaseOut.model_validate(case)


@internal.post("/cases/{case_id}/transition", response_model=CaseOut)
async def internal_transition(
    case_id: uuid.UUID,
    tenant_id: uuid.UUID,
    body: TransitionBody,
    session: SessionDep,
    actor_id: str = "agent:supervisor",
) -> CaseOut:
    case = await service.get_case(session, tenant_id, case_id)
    await service.transition(
        session,
        case,
        body.to,
        actor_type="agent",
        actor_id=actor_id,
        reason=body.reason,
        resolution=body.resolution,
    )
    return CaseOut.model_validate(case)


@internal.get("/cases/{case_id}", response_model=CaseOut)
async def internal_get_case(
    case_id: uuid.UUID, tenant_id: uuid.UUID, session: SessionDep
) -> CaseOut:
    return CaseOut.model_validate(await service.get_case(session, tenant_id, case_id))


@internal.get("/cases/{case_id}/detail", response_model=CaseDetail)
async def internal_case_detail(
    case_id: uuid.UUID, tenant_id: uuid.UUID, session: SessionDep
) -> CaseDetail:
    case = await service.get_case(session, tenant_id, case_id)
    return CaseDetail(
        case=CaseOut.model_validate(case),
        timeline=[
            TimelineEventOut.model_validate(t) for t in await service.timeline(session, case)
        ],
        actions=[
            ProposedActionOut.model_validate(a) for a in await service.actions_for(session, case)
        ],
    )


@internal.get("/cases/by-invoice/{invoice_ref}", response_model=CaseOut)
async def internal_case_by_invoice(
    invoice_ref: str, tenant_id: uuid.UUID, session: SessionDep
) -> CaseOut:
    case = await service.find_case_for_invoice(session, tenant_id, invoice_ref)
    if case is None:
        raise NotFoundError(f"no case for invoice {invoice_ref}")
    return CaseOut.model_validate(case)


@internal.get("/cases/{case_id}/actions/{action_id}", response_model=ProposedActionOut)
async def internal_get_action(
    case_id: uuid.UUID, action_id: uuid.UUID, tenant_id: uuid.UUID, session: SessionDep
) -> ProposedActionOut:
    case = await service.get_case(session, tenant_id, case_id)
    return ProposedActionOut.model_validate(await service.get_action(session, case, action_id))


@internal.post("/cases/{case_id}/actions/{action_id}/executed", response_model=ProposedActionOut)
async def internal_mark_executed(
    case_id: uuid.UUID,
    action_id: uuid.UUID,
    tenant_id: uuid.UUID,
    body: dict[str, Any],
    session: SessionDep,
    actor_id: str = "tool-gateway",
) -> ProposedActionOut:
    case = await service.get_case(session, tenant_id, case_id)
    action = await service.get_action(session, case, action_id)
    await service.mark_executed(session, case, action, body, actor_id=actor_id)
    return ProposedActionOut.model_validate(action)


@internal.post("/cases/{case_id}/events", response_model=TimelineEventOut, status_code=201)
async def internal_append_event(
    case_id: uuid.UUID, tenant_id: uuid.UUID, body: TimelineAppend, session: SessionDep
) -> TimelineEventOut:
    case = await service.get_case(session, tenant_id, case_id)
    ev = await service.append_event(session, case, body)
    await session.flush()
    return TimelineEventOut.model_validate(ev)
