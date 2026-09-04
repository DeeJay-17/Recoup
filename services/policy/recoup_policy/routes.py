from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from recoup_common.auth import Principal, Role, get_principal, require_role
from recoup_common.db import Database
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from recoup_policy import service
from recoup_policy.engine import Decision
from recoup_policy.models import Policy, PolicyEvaluation
from recoup_policy.schemas import (
    EvaluateRequest,
    EvaluateResponse,
    EvaluationOut,
    PolicyCreate,
    PolicyOut,
    PolicyUpdate,
    PolicyVersionCreate,
    PolicyVersionOut,
    SimulateRequest,
    SimulateResponse,
)
from recoup_policy.settings import Settings

router = APIRouter()
internal = APIRouter(prefix="/internal", tags=["internal"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.session() as session:
        yield session


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
Manager = Annotated[Principal, Depends(require_role(Role.MANAGER))]
Viewer = Annotated[Principal, Depends(get_principal)]


def _out(p: Policy) -> PolicyOut:
    cur = service.current_version(p)
    return PolicyOut(
        id=p.id,
        tenant_id=p.tenant_id,
        name=p.name,
        description=p.description,
        action_type=p.action_type,
        priority=p.priority,
        enabled=p.enabled,
        current_version=p.current_version,
        created_at=p.created_at,
        updated_at=p.updated_at,
        current=PolicyVersionOut.model_validate(cur) if cur else None,
    )


# ---------- manager-facing ----------
@router.get("/policies", response_model=list[PolicyOut], tags=["policies"])
async def list_policies(session: SessionDep, principal: Viewer) -> list[PolicyOut]:
    return [_out(p) for p in await service.list_policies(session, principal.tenant_id)]


@router.post("/policies", response_model=PolicyOut, status_code=201, tags=["policies"])
async def create_policy(body: PolicyCreate, session: SessionDep, principal: Manager) -> PolicyOut:
    p = await service.create_policy(session, principal.tenant_id, body, created_by=principal.email)
    return _out(await service.get_policy(session, principal.tenant_id, p.id))


@router.post("/policies/install-defaults", response_model=list[PolicyOut], tags=["policies"])
async def install_defaults(session: SessionDep, principal: Manager) -> list[PolicyOut]:
    await service.install_defaults(session, principal.tenant_id, created_by=principal.email)
    return [_out(p) for p in await service.list_policies(session, principal.tenant_id)]


@router.get("/policies/{policy_id}", response_model=PolicyOut, tags=["policies"])
async def get_policy(policy_id: uuid.UUID, session: SessionDep, principal: Viewer) -> PolicyOut:
    return _out(await service.get_policy(session, principal.tenant_id, policy_id))


@router.patch("/policies/{policy_id}", response_model=PolicyOut, tags=["policies"])
async def update_policy(
    policy_id: uuid.UUID, body: PolicyUpdate, session: SessionDep, principal: Manager
) -> PolicyOut:
    p = await service.get_policy(session, principal.tenant_id, policy_id)
    await service.update_policy(session, p, body)
    return _out(p)


@router.delete("/policies/{policy_id}", status_code=204, tags=["policies"])
async def delete_policy(policy_id: uuid.UUID, session: SessionDep, principal: Manager) -> None:
    p = await service.get_policy(session, principal.tenant_id, policy_id)
    await service.delete_policy(session, p)


@router.get(
    "/policies/{policy_id}/versions", response_model=list[PolicyVersionOut], tags=["policies"]
)
async def list_versions(
    policy_id: uuid.UUID, session: SessionDep, principal: Viewer
) -> list[PolicyVersionOut]:
    p = await service.get_policy(session, principal.tenant_id, policy_id)
    return [PolicyVersionOut.model_validate(v) for v in p.versions]


@router.post(
    "/policies/{policy_id}/versions", response_model=PolicyOut, status_code=201, tags=["policies"]
)
async def add_version(
    policy_id: uuid.UUID, body: PolicyVersionCreate, session: SessionDep, principal: Manager
) -> PolicyOut:
    p = await service.get_policy(session, principal.tenant_id, policy_id)
    await service.add_version(session, p, body, created_by=principal.email)
    return _out(p)


@router.post("/policies/{policy_id}/simulate", response_model=SimulateResponse, tags=["policies"])
async def simulate_policy(
    policy_id: uuid.UUID,
    body: SimulateRequest,
    session: SessionDep,
    principal: Manager,
    settings: SettingsDep,
) -> SimulateResponse:
    p = await service.get_policy(session, principal.tenant_id, policy_id)
    return await service.simulate(
        session,
        principal.tenant_id,
        p,
        body,
        default_decision=Decision(settings.default_decision),
        default_role=settings.default_required_role,
    )


@router.post("/policies/simulate", response_model=SimulateResponse, tags=["policies"])
async def simulate_new(
    body: SimulateRequest, session: SessionDep, principal: Manager, settings: SettingsDep
) -> SimulateResponse:
    return await service.simulate(
        session,
        principal.tenant_id,
        None,
        body,
        default_decision=Decision(settings.default_decision),
        default_role=settings.default_required_role,
    )


@router.get("/evaluations", response_model=list[EvaluationOut], tags=["policies"])
async def list_evaluations(
    session: SessionDep,
    principal: Viewer,
    case_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[EvaluationOut]:
    stmt = select(PolicyEvaluation).where(PolicyEvaluation.tenant_id == principal.tenant_id)
    if case_id:
        stmt = stmt.where(PolicyEvaluation.case_id == case_id)
    rows = await session.scalars(stmt.order_by(PolicyEvaluation.evaluated_at.desc()).limit(limit))
    return [EvaluationOut.model_validate(r) for r in rows.all()]


@router.post("/policies/evaluate", response_model=EvaluateResponse, tags=["policies"])
async def evaluate_as_user(
    body: EvaluateRequest, session: SessionDep, principal: Viewer, settings: SettingsDep
) -> EvaluateResponse:
    """Human-facing dry run (never recorded, tenant forced from token)."""
    body.tenant_id = principal.tenant_id
    body.record = False
    result, _ = await service.evaluate_request(
        session,
        body,
        default_decision=Decision(settings.default_decision),
        default_role=settings.default_required_role,
    )
    return EvaluateResponse(
        decision=result.decision,
        required_role=result.required_role,
        reason=result.reason,
        matched=result.matched,
        defaulted=result.defaulted,
    )


# ---------- internal: tool gateway / orchestrator ----------
@internal.post("/evaluate", response_model=EvaluateResponse)
async def evaluate_internal(
    body: EvaluateRequest, session: SessionDep, settings: SettingsDep
) -> EvaluateResponse:
    result, eval_id = await service.evaluate_request(
        session,
        body,
        default_decision=Decision(settings.default_decision),
        default_role=settings.default_required_role,
    )
    return EvaluateResponse(
        decision=result.decision,
        required_role=result.required_role,
        reason=result.reason,
        matched=result.matched,
        defaulted=result.defaulted,
        evaluation_id=eval_id,
    )


@internal.post("/tenants/{tenant_id}/install-defaults")
async def install_defaults_internal(tenant_id: uuid.UUID, session: SessionDep) -> dict[str, int]:
    n = await service.install_defaults(session, tenant_id, created_by="system:seed")
    return {"installed": n}
